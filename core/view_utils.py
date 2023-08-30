from collections.abc import Sequence
from itertools import chain
from types import FunctionType
from typing import Optional

from django.db.models import QuerySet
from drf_yasg import openapi
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import BasePermission
from rest_framework.viewsets import GenericViewSet

from core import models as core_models


class IsDAOOwner(BasePermission):
    message = {
        "error": "Only the DAO owner has access to this action. Header needs to contain signature=*signed-challenge*."
    }

    def has_permission(self, request, view):
        return True

    def has_object_permission(self, request, view, obj):
        from core.substrate import substrate_service

        signature = request.headers.get("Signature")
        owner_addr = obj.owner_id
        try:
            multisig = core_models.MultiSig.objects.get(address=owner_addr)
        except core_models.MultiSig.DoesNotExist:
            return substrate_service.verify(address=owner_addr, challenge_address=owner_addr, signature=signature)

        # in case the owner addr is a multisig acc we accept any of the signatories
        return any(
            substrate_service.verify(address=signatory, challenge_address=owner_addr, signature=signature)
            for signatory in [owner_addr, *multisig.signatories]
        )


class IsProposalCreator(BasePermission):
    message = {
        "error": "Only the Proposal creator has access to this action. "
        "Header needs to contain signature=*signed-challenge*."
    }

    def has_permission(self, request, view):
        return True

    def has_object_permission(self, request, view, obj):
        from core.substrate import substrate_service

        return substrate_service.verify(
            address=obj.creator_id, challenge_address=obj.dao.owner_id, signature=request.headers.get("Signature")
        )


class IsTokenHolder(BasePermission):
    message = {"error": "This request's header needs to contain signature=*signed-challenge*."}

    def has_permission(self, request, view):
        from core.models import AssetHolding, Dao
        from core.substrate import substrate_service

        proposal_id = request.parser_context["kwargs"]["pk"]
        challenge_address = Dao.objects.values_list("owner_id", flat=True).get(proposals__id=proposal_id)
        return any(
            substrate_service.verify(
                address=address, challenge_address=challenge_address, signature=request.headers.get("Signature")
            )
            for address in AssetHolding.objects.filter(asset__dao__proposals__id=proposal_id).values_list(
                "owner_id", flat=True
            )
        )


class MultiQsLimitOffsetPagination(LimitOffsetPagination):
    count = None
    counts = None
    request = None
    offset = None
    limit = None

    def paginate_querysets(self, qss: Sequence[QuerySet], request, **_) -> Optional[list]:
        """
        Args:
            qss: Sequence of Querysets
            request: request

        Returns:
            paginated list of objects

        similar to LimitOffsetPagination.paginate_queryset except that it allows a Sequence of Querysets as input
        """
        self.limit = self.get_limit(request)
        if self.limit is None:
            return None

        self.request = request
        self.counts = [self.get_count(qs) for qs in qss]
        self.count = sum(self.counts)
        self.offset = self.get_offset(request)

        page = []
        offset = self.offset
        limit = self.limit
        for idx, qs in enumerate(qss):
            # there are elements in the current qs meeting offset condition
            if (remaining_in_qs := self.counts[idx] - offset) > 0:
                # enough elements to serve query
                if limit <= remaining_in_qs:
                    page.extend(qs[offset : offset + limit])
                    return page
                # add existing elements
                # subtract count of existing elements from limit
                # remove offset and move on to the next qs
                else:
                    page.extend(qs[offset:])
                    limit -= remaining_in_qs
                    offset = 0
            # no elements meeting offset condition
            # rm current qs count from offset
            else:
                offset -= self.counts[idx]

        return page


def swagger_query_param(**kwargs):
    return openapi.Parameter(**{"in_": openapi.IN_QUERY, **kwargs})


signed_by_dao_owner = openapi.Parameter(
    name="Signature",
    in_=openapi.IN_HEADER,
    description="Current /challenge signed with the corresponding DAO's Account's private key, B64 encoded.",
    required=True,
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_BASE64,
)
signed_by_proposal_creator = openapi.Parameter(
    name="Signature",
    in_=openapi.IN_HEADER,
    description="Current /challenge signed with the Proposal creator's Account's private key, B64 encoded.",
    required=True,
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_BASE64,
)
signed_by_token_holder = openapi.Parameter(
    name="Signature",
    in_=openapi.IN_HEADER,
    description="Current /challenge signed with a corresponding DAO token holding Account's private key, B64 encoded.",
    required=True,
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_BASE64,
)


class QueryFilter:
    always_accept = ["id"]
    ignored_filter_fields = ["limit", "offset", "ordering", "search"]

    def filter_queryset(self, request, queryset, view):
        filters = {}
        filter_fields = getattr(view, "filter_fields", ())
        for field, value in request.query_params.items():
            if field in self.ignored_filter_fields:
                continue
            if field in self.always_accept:
                filters[field] = value
                continue
            if field not in filter_fields:
                raise ValidationError(
                    f"'{field}' is an invalid filter field. Choices are:"
                    f" {', '.join(chain(self.always_accept, filter_fields))}"
                )
            filters[field] = value

        return queryset.filter(**filters)


class SearchableMixin(GenericViewSet):
    filter_fields = []
    search_fields = []
    ordering_fields = []
    filter_backends = [SearchFilter, OrderingFilter, QueryFilter]

    @staticmethod
    def _copy_func(f):
        return FunctionType(f.__code__, f.__globals__, f.__name__, f.__defaults__, f.__closure__)

    def __init__(self, **kwargs):
        """
        adds swagger defaults retrieve and list views
        can be replaced by a custom swagger generator at some point
        """
        super().__init__(**kwargs)
        # we only need to update the auto_schema once
        if getattr(self.__class__, "updated_swagger_auto_schema", False):
            return

        if get_fn := getattr(self.__class__, "retrieve", None):
            if get_fn is RetrieveModelMixin.retrieve:
                get_fn = self._copy_func(get_fn)
            overrides = getattr(get_fn, "_swagger_auto_schema", {})
            name = self.queryset.model._meta.verbose_name  # noqa
            auto_schema = {
                "operation_id": f"Retrieve {name}",
                "operation_description": f"Retrieves a {name} instance.",
                "security": [{"Basic": []}],
                **overrides,
            }
            get_fn._swagger_auto_schema = auto_schema
            self.__class__.retrieve = get_fn
            self.__class__.updated_swagger_auto_schema = True

        if list_fn := getattr(self.__class__, "list", None):
            # if our function is the drf default we create a copy before altering it
            if list_fn is ListModelMixin.list:
                list_fn = self._copy_func(list_fn)

            overrides = getattr(list_fn, "_swagger_auto_schema", {})
            manual_parameters = overrides.pop("manual_parameters", [])

            if self.ordering_fields:
                manual_parameters.append(
                    swagger_query_param(
                        **{
                            "name": "ordering",
                            "description": "Comma separated list of parameters to order the results by.\n"
                            '"-" reverses the order.',
                            "type": openapi.TYPE_STRING,
                            "required": False,
                            "example": "-id,some_field",
                            "enum": self.ordering_fields,
                        }
                    )
                )
            if self.search_fields:
                manual_parameters.append(
                    swagger_query_param(
                        **{
                            "name": "search",
                            "description": f"Search for results in these fields: {','.join(self.search_fields)}",
                            "type": openapi.TYPE_STRING,
                            "required": False,
                            "example": "some_value",
                        }
                    )
                )

            if self.filter_fields:
                manual_parameters.extend(
                    [
                        swagger_query_param(
                            **{
                                "name": f"{filter_field}",
                                "description": f"Filter results by {filter_field}.",
                                "type": openapi.TYPE_STRING,
                                "required": False,
                                "example": "some_value",
                            }
                        )
                        for filter_field in self.filter_fields
                    ]
                )

            name = self.queryset.model._meta.verbose_name_plural  # noqa
            auto_schema = {
                "operation_id": f"List {name}",
                "operation_description": f"Retrieves a list of {name}.",
                "security": [{"Basic": []}],
                "manual_parameters": manual_parameters,
                **overrides,
            }

            list_fn._swagger_auto_schema = auto_schema
            self.__class__.list = list_fn
            self.__class__.updated_swagger_auto_schema = True
