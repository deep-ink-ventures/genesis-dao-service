import base64
from collections.abc import Collection
from unittest.mock import PropertyMock, patch

from ddt import data, ddt
from django.urls import reverse

from core import models
from core.tests.testcases import IntegrationTestCase


def wrap_in_pagination_res(results: Collection) -> dict:
    return {"count": len(results), "next": None, "previous": None, "results": results}


@ddt
class CoreViewSetTest(IntegrationTestCase):
    def setUp(self):
        models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1")
        models.Dao.objects.create(id="dao2", name="dao2 name", owner_id="acc2")
        models.Asset.objects.create(id=1, owner_id="acc1", dao_id="dao1", total_supply=100)
        models.Asset.objects.create(id=2, owner_id="acc2", dao_id="dao2", total_supply=200)
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc1", balance=100)
        models.AssetHolding.objects.create(asset_id=2, owner_id="acc2", balance=200)

    def test_stats(self):
        expected_res = {"account_count": 2, "dao_count": 2}
        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-stats-list"))

        self.assertDictEqual(res.data, expected_res)

    def test_account_get(self):
        expected_res = {"address": "acc1"}

        with self.assertNumQueries(1):
            res = self.client.get(reverse("core-account-detail", kwargs={"pk": "acc1"}))

        self.assertDictEqual(res.data, expected_res)

    def test_account_get_list(self):
        expected_res = wrap_in_pagination_res([{"address": "acc1"}, {"address": "acc2"}])

        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-account-list"))

        self.assertDictEqual(res.data, expected_res)

    def test_dao_get(self):
        expected_res = {
            "id": "dao1",
            "name": "dao1 name",
            "owner_id": "acc1",
            "metadata_url": None,
            "metadata_hash": None,
        }

        with self.assertNumQueries(1):
            res = self.client.get(reverse("core-dao-detail", kwargs={"pk": "dao1"}))

        self.assertDictEqual(res.data, expected_res)

    def test_dao_get_list(self):
        expected_res = wrap_in_pagination_res(
            [
                {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
            ]
        )

        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-dao-list"))

        self.assertDictEqual(res.data, expected_res)

    @data(
        # query_params
        {"id": "dao2"},
        {"owner_id": "acc2"},
        {"name": "dao2 name"},
    )
    def test_dao_list_filter(self, query_params):
        expected_res = wrap_in_pagination_res(
            [
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
            ]
        )

        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-dao-list"), query_params)

        self.assertDictEqual(res.data, expected_res)

    @data(
        # query_params, expected_res
        (
            {"order_by": "id"},
            [
                {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
                {"id": "dao3", "name": "3", "owner_id": "acc2"},
            ],
        ),
        (
            {"order_by": "name"},
            [
                {"id": "dao3", "name": "3", "owner_id": "acc2"},
                {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
            ],
        ),
        (
            {"order_by": "owner_id,id"},
            [
                {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
                {"id": "dao3", "name": "3", "owner_id": "acc2"},
            ],
        ),
    )
    def test_dao_list_order_by(self, case):
        query_params, expected_res = case
        models.Dao.objects.create(id="dao3", name="3", owner_id="acc2")

        expected_res = wrap_in_pagination_res(expected_res)

        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-dao-list"), query_params)

        self.assertDictEqual(res.data, expected_res)

    @data(
        # query_params, expected_res, expected query count
        (
            {"prioritise_owner": "acc2", "order_by": "-name"},
            [
                {"id": "dao4", "name": "dao4 name", "owner_id": "acc2"},
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
                {"id": "dao3", "name": "dao3 name", "owner_id": "acc1"},
                {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
            ],
            4,
        ),
        (
            {"prioritise_holder": "acc3", "order_by": "-name"},
            [
                {"id": "dao4", "name": "dao4 name", "owner_id": "acc2"},
                {"id": "dao3", "name": "dao3 name", "owner_id": "acc1"},
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
                {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
            ],
            4,
        ),
        (
            {"prioritise_owner": "acc2", "prioritise_holder": "acc3", "order_by": "name"},
            [
                {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
                {"id": "dao4", "name": "dao4 name", "owner_id": "acc2"},
                {"id": "dao3", "name": "dao3 name", "owner_id": "acc1"},
                {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
            ],
            6,
        ),
    )
    def test_dao_list_prioritised(self, case):
        query_params, expected_res, expected_query_count = case
        models.Account.objects.create(address="acc3")
        models.Dao.objects.create(id="dao3", name="dao3 name", owner_id="acc1")
        models.Dao.objects.create(id="dao4", name="dao4 name", owner_id="acc2")
        models.Asset.objects.create(id=3, owner_id="acc1", dao_id="dao3", total_supply=100)
        models.Asset.objects.create(id=4, owner_id="acc2", dao_id="dao4", total_supply=200)
        models.AssetHolding.objects.create(asset_id=3, owner_id="acc3", balance=100)
        models.AssetHolding.objects.create(asset_id=4, owner_id="acc3", balance=200)

        expected_res = wrap_in_pagination_res(expected_res)

        with self.assertNumQueries(expected_query_count):
            res = self.client.get(reverse("core-dao-list"), query_params)

        self.assertDictEqual(res.data, expected_res)

    @patch("core.view_utils.MultiQsLimitOffsetPagination.default_limit", PropertyMock(return_value=None))
    def test_dao_list_no_limit(self):
        expected_res = [
            {"id": "dao1", "name": "dao1 name", "owner_id": "acc1"},
            {"id": "dao2", "name": "dao2 name", "owner_id": "acc2"},
        ]

        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-dao-list"), {"prioritise_owner": "acc2"})

        self.assertCountEqual(res.data, expected_res)

    def test_dao_add_metadata(self):
        with self.assertNumQueries(0):
            with open("core/tests/test_file.jpeg", "rb") as f:
                post_data = {
                    "email": "some@email.com",
                    "description": "some description",
                    "logo": base64.b64encode(f.read()).decode(),
                }
        expected_res = {
            "description": "some description",
            "email": "some@email.com",
            "images": {
                "logo": {
                    "content_type": "image/jpeg",
                    "large": {"url": "https://some_storage.some_region.com/dao1/logo_large.jpeg"},
                    "medium": {"url": "https://some_storage.some_region.com/dao1/logo_medium.jpeg"},
                    "small": {"url": "https://some_storage.some_region.com/dao1/logo_small.jpeg"},
                }
            },
            "metadata_hash": "958a03a103e4ddb3de044dea11d4e8cc946e50bfb9b6831af82a9c0054e344d6",
            "metadata_url": "https://some_storage.some_region.com/dao1/metadata.json",
        }

        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "dao1"}), post_data, content_type="application/json"
        )

        self.assertDictEqual(res.data, expected_res)

    def test_asset_get(self):
        expected_res = {"id": 1, "dao_id": "dao1", "owner_id": "acc1", "total_supply": 100}

        with self.assertNumQueries(1):
            res = self.client.get(reverse("core-asset-detail", kwargs={"pk": 1}))

        self.assertDictEqual(res.data, expected_res)

    def test_asset_get_list(self):
        expected_res = wrap_in_pagination_res(
            [
                {"id": 1, "dao_id": "dao1", "owner_id": "acc1", "total_supply": 100},
                {"id": 2, "dao_id": "dao2", "owner_id": "acc2", "total_supply": 200},
            ]
        )
        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-asset-list"))

        self.assertDictEqual(res.data, expected_res)
