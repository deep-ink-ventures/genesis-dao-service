from collections.abc import Sequence
from itertools import chain
from typing import Optional

from django.core.exceptions import FieldError
from django.db.models import QuerySet
from rest_framework.pagination import LimitOffsetPagination


class FilterBackend:
    order_kw = "order_by"
    ignored_filter_fields = ("limit", "offset", order_kw)
    always_accept = ("id", "pk")
    order_separator = ","

    def filter_queryset(self, request, queryset, view):
        filters = {}
        allowed_filter_fields = getattr(view, "allowed_filter_fields", ())
        allowed_order_fields = getattr(view, "allowed_order_fields", ())
        for field, value in request.query_params.items():
            if field in self.ignored_filter_fields:
                continue
            if field in self.always_accept:
                filters[field] = value
                continue
            if field not in allowed_filter_fields:
                raise FieldError(
                    f"'{field}' is an invalid filter field. Choices are:"
                    f" {', '.join(chain(self.always_accept, allowed_filter_fields))}"
                )
            filters[field] = value

        qs = queryset.filter(**filters)
        if order_by_fields := request.query_params.get(self.order_kw):
            order_by_fields = order_by_fields.split(self.order_separator)
            for order_field in order_by_fields:
                # allow for leading "-" / reverse ordering
                order_field = order_field[1:] if order_field.startswith("-") else order_field
                if order_field in self.always_accept:
                    continue
                if order_field not in allowed_order_fields:
                    raise FieldError(
                        f"'{order_field}' is an invalid order field. Choices are:"
                        f" {', '.join(chain(self.always_accept, allowed_order_fields))}"
                    )
            qs = qs.order_by(*order_by_fields)
        return qs


class MultiQsLimitOffsetPagination(LimitOffsetPagination):
    count = None
    counts = None
    request = None
    offset = None
    limit = None

    def paginate_querysets(self, qss: Sequence[QuerySet], request, view=None) -> Optional[list]:
        """
        Args:
            qss: Sequence of Querysets
            request: request
            view: view (not required)

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
