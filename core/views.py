from itertools import chain

from django.db.models import Q
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from core import models, serializers
from core.view_utils import FilterBackend, MultiQsLimitOffsetPagination


class SearchableMixin(GenericViewSet):
    filter_backends = [FilterBackend]


class AccountViewSet(ReadOnlyModelViewSet, SearchableMixin):
    queryset = models.Account.objects.all()
    serializer_class = serializers.AccountSerializer


class DaoViewSet(ReadOnlyModelViewSet, SearchableMixin):
    queryset = models.Dao.objects.all()
    serializer_class = serializers.DaoSerializer
    allowed_filter_fields = ("id", "name", "owner_id")
    allowed_order_fields = ("id", "name", "owner_id")
    pagination_class = MultiQsLimitOffsetPagination

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
        self.request._request.GET = query_params
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


class AssetViewSet(ReadOnlyModelViewSet, SearchableMixin):
    queryset = models.Asset.objects.all()
    serializer_class = serializers.AssetSerializer


class AssetHoldingViewSet(ReadOnlyModelViewSet, SearchableMixin):
    queryset = models.AssetHolding.objects.all()
    serializer_class = serializers.AssetHoldingSerializer
