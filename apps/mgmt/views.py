
import json
import datetime

from django.conf import settings

from elasticsearch.exceptions import NotFoundError

from django.contrib.auth import get_user_model
from django.conf import settings

from rest_framework import viewsets
from rest_framework import mixins
from rest_framework import permissions
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import detail_route, list_route
from rest_framework import filters
from . import app_serializers
from . import models
from utils.es import es
from . import initialize
from utils.es import indices_client
from utils.verify_code import EmailVerifyCode
from utils.c_permissions import IsAdminCreate, IsAdminOrSelfChange, IsAdminOrReadOnly
from utils.c_pagination import CPageNumberPagination

User = get_user_model()

email_verify_code = EmailVerifyCode()

#验证码过期时间（秒）
MAX_AGE = settings.MAX_AGE

# Create your views here.
class TableViewset(mixins.ListModelMixin,
                   mixins.CreateModelMixin,
                   mixins.RetrieveModelMixin,
                   mixins.DestroyModelMixin,
                   mixins.UpdateModelMixin,
                   viewsets.GenericViewSet,):
    permission_classes = (IsAdminOrReadOnly,)

    serializer_class = app_serializers.TableSerializer
    queryset = models.Table.objects.all()

    def is_data_raise(self, table_name):
        res = es.search(index=[table_name, table_name+".", table_name+".."], doc_type="data")
        if res["hits"]["total"]:
            raise exceptions.ParseError("Table has started to use, if need to modify, please delete and re-create")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        table = serializer.save()
        initialize.add_table(table, create_index=True)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        self.is_data_raise(instance.name)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        initialize.delete_table(instance)
        table = serializer.save()
        initialize.add_table(table, create_index=True)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        initialize.delete_table(instance)
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

class UserViewset(viewsets.ModelViewSet):
    serializer_class = app_serializers.UserSerializer
    queryset = User.objects.all()
    permission_classes = (permissions.IsAuthenticated, IsAdminCreate, IsAdminOrSelfChange)
    pagination_class = CPageNumberPagination
    filter_backends = (filters.SearchFilter, )
    search_fields = ("username", "email")

    def get_serializer_class(self):
        # if(settings.AUTH_LDAP_SERVER_URI and self.action!="get_my_info"):
        #     raise exceptions.ParseError("Please operate on LDAP server")
        if self.action == "change_password":
            return app_serializers.ChangePWSerializer
        elif self.action == "reset_password_admin":
            return app_serializers.RestPWAdminSerializer
        elif self.action == "reset_password_email":
            return app_serializers.RestPWEmailSerializer
        elif self.action == "send_verify_code":
            return app_serializers.SendVerifyCodeSerializer
        elif self.action == "get_my_info":
            return super().get_serializer_class()
        return super().get_serializer_class()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if(instance.is_superuser):
            raise exceptions.ParseError("Super user can not delete")
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @list_route(methods=['post'], permission_classes=[permissions.IsAuthenticated], url_path='change-password')
    def change_password(self, request, pk=None):
        serializer = self.get_serializer(data=request.data, context={"request", request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save()
        return Response({"detail": "Successfully modified!"})


    @list_route(methods=['post'], permission_classes=[permissions.IsAdminUser], url_path='reset-password-admin')
    def reset_password_admin(self, request, pk=None):
        serializer = self.get_serializer(data=request.data, context={"request", request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save()
        return Response({"detail": "Reset successfully"})

    @list_route(methods=['post'], permission_classes=[], url_path="send-verify-code")
    def send_verify_code(self, request, pk=None):
        serializer = self.get_serializer(data=request.data, context={"request", request})
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"]
        try:
            verify_code_inst = models.RestPWVerifyCode.objects.get(user__username=username)
        except models.RestPWVerifyCode.DoesNotExist:
            pass
        else:
            if datetime.datetime.now() - verify_code_inst.add_time < datetime.timedelta(seconds=60):
                raise exceptions.ParseError("Less than 60 seconds from last sent")
            verify_code_inst.delete()
        user = User.objects.get(username=username)
        try:
            code = email_verify_code.send_verifycode(user.email)
        except Exception as exc:
            raise exceptions.ParseError("send failed, please try again later！")
        reset_pw_verify_code = models.RestPWVerifyCode(user=user, code=code)
        reset_pw_verify_code.save()
        return Response({"detail": "send successfully", "email": user.email})

    @list_route(methods=['post'], permission_classes=[], url_path="reset-password-email")
    def reset_password_email(self, request, pk=None):
        serializer = self.get_serializer(data=request.data, context={"request", request})
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"]
        user = User.objects.get(username=username)
        user.set_password(serializer.validated_data["new_password"])
        user.save()
        return Response({"detail": "Reset successfully"})

    @list_route(methods=['get'], permission_classes=[permissions.IsAuthenticated], url_path="get-my-info")
    def get_my_info(self, request, pk=None):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)

class LdapUserViewset(viewsets.GenericViewSet):
    serializer_class = app_serializers.UserSerializer
    queryset = User.objects.all()

    @list_route(methods=['get'], permission_classes=[permissions.IsAuthenticated], url_path="get-my-info")
    def get_my_info(self, request, pk=None):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)