from unittest.mock import Mock

from ddt import data, ddt
from django.db import connection, models
from rest_framework.exceptions import ValidationError

from core.tests.testcases import IntegrationTestCase, UnitTestCase
from core.view_utils import MultiQsLimitOffsetPagination, QuerysetMixin, SearchableMixin


class TestModel(models.Model):
    pass


@ddt
class MultiQsLimitOffsetPaginationTest(IntegrationTestCase):
    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("create table if not exists core_testmodel (id serial not null primary key);")
        self.paginator = MultiQsLimitOffsetPagination()
        TestModel.objects.bulk_create([TestModel(id=i) for i in range(1, 11)])

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("drop table if exists core_testmodel;")

    @data(
        # addrs to filter by per qs, query_params, expected addr order of res, expected query count
        # 1 qs
        (((1, 2, 4, 7, 8, 9, 10),), {}, (1, 2, 4, 7, 8, 9, 10), 2),
        (((1, 2, 4, 7, 8, 9, 10),), {"limit": 2}, (1, 2), 2),
        (((1, 2, 4, 7, 8, 9, 10),), {"limit": 2, "offset": 5}, (9, 10), 2),
        (((1, 2, 4, 7, 8, 9, 10),), {"limit": 2, "offset": 6}, (10,), 2),
        # multi qs
        (((3,), (5, 6), (1, 2, 4, 7, 8, 9, 10)), {}, (3, 5, 6, 1, 2, 4, 7, 8, 9, 10), 6),
        (((3,), (5, 6), (1, 2, 4, 7, 8, 9, 10)), {"limit": 5}, (3, 5, 6, 1, 2), 6),
        (((3,), (5, 6), (1, 2, 4, 7, 8, 9, 10)), {"limit": 5, "offset": 2}, (6, 1, 2, 4, 7), 5),
        (((3,), (5, 6), (1, 2, 4, 7, 8, 9, 10)), {"limit": 3, "offset": 3}, (1, 2, 4), 4),
    )
    def test_paginate_querysets(self, case):
        qss_addrs, query_params, expected_order, expected_query_count = case
        qss = [TestModel.objects.filter(id__in=qs_addrs) for qs_addrs in qss_addrs]
        expected_res = [TestModel(id=expected_addr) for expected_addr in expected_order]

        # 1 count query per qs + 1 select query per used qs
        with self.assertNumQueries(expected_query_count):
            res = self.paginator.paginate_querysets(qss=qss, request=Mock(query_params=query_params))

        self.assertListEqual(res, expected_res)

    def test_paginate_queryset_no_limit(self):
        with self.assertNumQueries(0):
            self.paginator.default_limit = None
            self.assertIsNone(
                self.paginator.paginate_querysets(qss=[TestModel.objects.filter(id=1)], request=Mock(query_params={}))
            )


class SearchableMixinTest(UnitTestCase):
    @staticmethod
    def test_empty_view():
        # shouldn't raise
        SearchableMixin(nice_kwarg="idd")

    @staticmethod
    def test_no_allowed_fields():
        class _SearchableMixin(SearchableMixin):
            queryset = Mock()

            def retrieve(self, *args, **kwargs):
                pass

            def list(self, *args, **kwargs):
                pass

        # shouldn't raise
        _SearchableMixin(nice_kwarg="idd")


class QuerysetMixinTest(IntegrationTestCase):
    def test_query_fields(self):
        with connection.cursor() as cursor:
            cursor.execute("create table if not exists core_testmodel (id serial not null primary key);")
            TestModel.objects.bulk_create([TestModel(id=i) for i in range(1, 11)])

            mixin = QuerysetMixin()
            mixin.query_fields = ["id"]
            mixin.queryset = TestModel.objects.all()
            mixin.request = Mock(query_params={"id": "foo"})
            with self.assertRaises(ValidationError):
                mixin.get_queryset().__str__()

            mixin.request = Mock(query_params={"id": "1"})
            self.assertEqual(mixin.get_queryset().query.__str__(), TestModel.objects.filter(id=1).query.__str__())

            cursor.execute("drop table if exists core_testmodel;")
