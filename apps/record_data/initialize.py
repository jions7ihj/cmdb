from elasticsearch.exceptions import NotFoundError

from django.conf import settings

from rest_framework import serializers
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework import mixins
from rest_framework import viewsets

from . import views
from utils.es import es

def add_viewset(table):
    data_index = table.name
    record_data_index = "{}.".format(table.name)
    deleted_data_index = "{}..".format(table.name)

    def retrieve(self, request, *args, **kwargs):
        try:
            res = es.search(index=record_data_index, doc_type="record-data", body={"query": {"term": {"S-data-id": kwargs["pk"]}}}, sort="S-update-time:desc")
        except NotFoundError as exc:
            raise exceptions.NotFound("Document {} was not found in Type data of Index {}".format(kwargs["pk"], record_data_index))
        return Response(res["hits"])
    viewset = type(table.name, (mixins.RetrieveModelMixin, viewsets.GenericViewSet), dict(list=list, retrieve=retrieve))
    setattr(views, table.name, viewset)
    return viewset
