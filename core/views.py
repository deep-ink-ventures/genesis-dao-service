import secrets
from itertools import chain

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils.decorators import method_decorator
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_403_FORBIDDEN
from rest_framework.viewsets import ReadOnlyModelViewSet

from core import models, serializers
from core.file_handling.file_handler import file_handler
from core.substrate import substrate_service
from core.view_utils import (
    MultiQsLimitOffsetPagination,
    SearchableMixin,
    signature_in_header,
    swagger_query_param,
)


@swagger_auto_schema(
    method="GET",
    operation_id="Retrieve stats",
    operation_description="Retrieves some stats.",
    responses=openapi.Responses(responses={HTTP_200_OK: openapi.Response("", serializers.StatsSerializer)}),
    security=[{"Basic": []}],
)
@api_view()
def stats(request, *args, **kwargs):
    serializer = serializers.StatsSerializer(
        data={
            "account_count": models.Account.objects.count(),
            "dao_count": models.Dao.objects.count(),
        }
    )
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data)


@swagger_auto_schema(
    method="GET",
    operation_id="Retrieve config",
    operation_description="Retrieves config.",
    responses=openapi.Responses(responses={HTTP_200_OK: openapi.Response("", serializers.ConfigSerializer)}),
    security=[{"Basic": []}],
)
@api_view()
def config(request, *args, **kwargs):
    serializer = serializers.ConfigSerializer(
        data={
            "deposit_to_create_dao": settings.DEPOSIT_TO_CREATE_DAO,
            "deposit_to_create_proposal": settings.DEPOSIT_TO_CREATE_PROPOSAL,
            "block_creation_interval": settings.BLOCK_CREATION_INTERVAL,
        }
    )
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data)


@method_decorator(swagger_auto_schema(operation_description="Retrieves an Account."), "retrieve")
class AccountViewSet(ReadOnlyModelViewSet, SearchableMixin):
    allowed_filter_fields = ("id",)
    allowed_order_fields = ("id",)
    queryset = models.Account.objects.all()

    def get_serializer_class(self):
        return {
            "retrieve": serializers.AccountSerializerDetail,
            "list": serializers.AccountSerializerList,
        }.get(self.action)

    def retrieve(self, request, *args, **kwargs):
        account = self.get_object()
        account.balance = substrate_service.retrieve_account_balance(account_address=account.address)
        return Response(self.get_serializer(account).data)


class DaoViewSet(ReadOnlyModelViewSet, SearchableMixin):
    queryset = models.Dao.objects.all()
    allowed_filter_fields = ("id", "name", "owner_id")
    allowed_order_fields = ("id", "name", "owner_id")
    pagination_class = MultiQsLimitOffsetPagination

    def get_queryset(self):
        return self.queryset.select_related("asset")

    def get_serializer_class(self):
        return {
            "retrieve": serializers.DaoSerializerDetail,
            "list": serializers.DaoSerializerList,
            "add_metadata": serializers.MetadataSerializer,
        }.get(self.action)

    @swagger_auto_schema(
        manual_parameters=[
            swagger_query_param(
                **{
                    "name": "prioritise_owner",
                    "description": "owner_id to return first.",
                    "type": openapi.TYPE_STRING,
                    "required": False,
                }
            ),
            swagger_query_param(
                **{
                    "name": "prioritise_holder",
                    "description": "holder_id to return first.",
                    "type": openapi.TYPE_STRING,
                    "required": False,
                }
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        # nothing special to do here
        if "prioritise_owner" not in request.query_params and "prioritise_holder" not in request.query_params:
            return super().list(request, *args, **kwargs)

        # override query_params
        query_params = request.query_params.copy()
        owner_prio = query_params.pop("prioritise_owner", [])
        owner_prio = owner_prio[-1] if owner_prio else owner_prio
        holder_prio = query_params.pop("prioritise_holder", [])
        holder_prio = holder_prio[-1] if holder_prio else holder_prio
        self.request._request.GET = query_params  # noqa
        qs = self.filter_queryset(self.get_queryset())
        qss = []
        if owner_prio:
            qss.append(qs.filter(owner_id=owner_prio))
        # if we also have a prioritised owner we need to exclude these entries to avoid duplicates
        if holder_prio:
            qss.append(qs.filter(~Q(owner_id=owner_prio) if owner_prio else Q(), asset__holdings__owner_id=holder_prio))
        # rest of the qs, not prioritized. we need to exclude the entries from the 2 previous qss
        qss.append(
            qs.exclude(
                (Q(owner_id=owner_prio) if owner_prio else Q())
                | (Q(asset__holdings__owner_id=holder_prio) if holder_prio else Q())
            )
        )
        page = self.paginator.paginate_querysets(qss, request, view=self)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(chain(*qss), many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        operation_id="Add DAO Metadata",
        operation_description="Adds metadata to a DAO.",
        manual_parameters=[signature_in_header],
        security=[{"Signature": []}],
        responses={201: openapi.Response("", serializers.MetadataResponseSerializer)},
    )
    @action(
        methods=["POST"],
        detail=True,
        url_path="metadata",
    )
    def add_metadata(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dao = self.get_object()
        if not substrate_service.verify(address=dao.owner_id, signature=request.headers.get("Signature")):
            return Response(status=HTTP_403_FORBIDDEN)

        metadata = file_handler.upload_metadata(metadata=serializer.validated_data, storage_destination=dao.id)
        dao.metadata = metadata["metadata"]
        dao.metadata_url = metadata["metadata_url"]
        dao.metadata_hash = metadata["metadata_hash"]
        dao.save(update_fields=["metadata", "metadata_url", "metadata_hash"])
        return Response(metadata, status=HTTP_201_CREATED)

    @swagger_auto_schema(
        method="GET",
        operation_id="Challenge",
        operation_description="Retrieves current challenge.",
        responses=openapi.Responses(responses={HTTP_200_OK: openapi.Response("", serializers.ChallengeSerializer)}),
        security=[{"Basic": []}],
    )
    @action(
        methods=["GET"],
        detail=True,
        url_path="challenge",
    )
    def challenge(self, request, **_):
        challenge_token = secrets.token_hex(64)
        cache.set(key=self.get_object().owner_id, value=challenge_token, timeout=settings.CHALLENGE_LIFETIME)
        return Response(status=HTTP_200_OK, data={"challenge": challenge_token})


@method_decorator(swagger_auto_schema(operation_description="Retrieves an Asset."), "retrieve")
class AssetViewSet(ReadOnlyModelViewSet, SearchableMixin):
    allowed_filter_fields = ("id", "owner_id", "dao_id")
    allowed_order_fields = ("id", "owner_id", "dao_id")
    queryset = models.Asset.objects.all()
    serializer_class = serializers.AssetSerializer


@method_decorator(swagger_auto_schema(operation_description="Retrieves an Asset Holding."), "retrieve")
class AssetHoldingViewSet(ReadOnlyModelViewSet, SearchableMixin):
    allowed_filter_fields = ("id", "owner_id", "asset_id")
    allowed_order_fields = ("id", "owner_id", "asset_id")
    queryset = models.AssetHolding.objects.all()
    serializer_class = serializers.AssetHoldingSerializer


class ProposalViewSet(ReadOnlyModelViewSet, SearchableMixin):
    queryset = models.Proposal.objects.all()
    serializer_class = serializers.ProposalSerializer
    allowed_filter_fields = ("id", "dao_id")
    allowed_order_fields = ("id", "dao_id")
