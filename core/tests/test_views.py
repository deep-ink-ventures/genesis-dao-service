import base64
import secrets
from collections.abc import Collection
from functools import partial
from unittest.mock import Mock, PropertyMock, patch

from ddt import data, ddt
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils.timezone import now
from rest_framework.exceptions import ErrorDetail
from rest_framework.fields import DateTimeField
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_429_TOO_MANY_REQUESTS,
)
from substrateinterface import Keypair

from core import models
from core.serializers import MultiSigTransactionSerializer
from core.tests.testcases import IntegrationTestCase


def wrap_in_pagination_res(results: Collection) -> dict:
    return {"count": len(results), "next": None, "previous": None, "results": results}


def fmt_dt(value):
    return DateTimeField().to_representation(value=value)


expected_dao1_res = {
    "id": "dao1",
    "name": "dao1 name",
    "creator_id": "acc1",
    "owner_id": "acc1",
    "asset_id": 1,
    "proposal_duration": 10,
    "proposal_token_deposit": 123,
    "minimum_majority_per_1024": 50,
    "setup_complete": False,
    "metadata": {"some": "data"},
    "metadata_url": None,
    "metadata_hash": None,
    "number_of_token_holders": 4,
    "number_of_open_proposals": 1,
    "most_recent_proposals": [1],
}
expected_dao2_res = {
    "id": "dao2",
    "name": "dao2 name",
    "creator_id": "acc2",
    "owner_id": "acc2",
    "asset_id": 2,
    "proposal_duration": 15,
    "proposal_token_deposit": 234,
    "minimum_majority_per_1024": 45,
    "setup_complete": False,
    "metadata": None,
    "metadata_url": None,
    "metadata_hash": None,
    "number_of_token_holders": 1,
    "number_of_open_proposals": 0,
    "most_recent_proposals": [2],
}


@ddt
class CoreViewSetTest(IntegrationTestCase):
    def setUp(self):
        self.challenge_key = secrets.token_hex(64)
        cache.set(key="acc1", value=self.challenge_key, timeout=60)
        self.acc1 = models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        models.Account.objects.create(address="acc3")
        models.Account.objects.create(address="acc4")
        models.Dao.objects.create(
            id="dao1", name="dao1 name", creator_id="acc1", owner_id="acc1", metadata={"some": "data"}
        )
        models.Governance.objects.create(
            dao_id="dao1", proposal_duration=10, proposal_token_deposit=123, minimum_majority=50
        )
        models.Dao.objects.create(id="dao2", name="dao2 name", creator_id="acc2", owner_id="acc2")
        models.Governance.objects.create(
            dao_id="dao2", proposal_duration=15, proposal_token_deposit=234, minimum_majority=45
        )
        models.Asset.objects.create(id=1, owner_id="acc1", dao_id="dao1", total_supply=1000)
        models.Asset.objects.create(id=2, owner_id="acc2", dao_id="dao2", total_supply=200)
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc1", balance=500)
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc2", balance=300)
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc3", balance=100)
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc4", balance=100)
        models.AssetHolding.objects.create(asset_id=2, owner_id="acc2", balance=200)
        models.Proposal.objects.create(
            id=1,
            dao_id="dao1",
            creator_id="acc1",
            metadata_url="url1",
            metadata_hash="hash1",
            metadata={"a": 1},
            birth_block_number=10,
            title="t1",
        )
        models.Proposal.objects.create(
            id=2,
            dao_id="dao2",
            creator_id="acc2",
            metadata_url="url2",
            metadata_hash="hash2",
            metadata={"a": 2},
            fault="some reason",
            status=models.ProposalStatus.FAULTED,
            birth_block_number=15,
            setup_complete=True,
        )
        models.Vote.objects.create(proposal_id=1, voter_id="acc1", in_favor=True, voting_power=500)
        models.Vote.objects.create(proposal_id=1, voter_id="acc2", in_favor=True, voting_power=300)
        models.Vote.objects.create(proposal_id=1, voter_id="acc3", in_favor=False, voting_power=100)
        models.Vote.objects.create(proposal_id=1, voter_id="acc4", voting_power=100)
        models.Vote.objects.create(proposal_id=2, voter_id="acc2", in_favor=False, voting_power=200)

    def test_queryset_filter(self):
        res = self.client.get(reverse("core-proposal-list") + "?dao_id=dao2")
        self.assertEqual(res.json()["results"][0]["dao_id"], "dao2")

    def test_welcome(self):
        expected_res = {"success": True, "message": "Welcome traveler."}
        with self.assertNumQueries(0):
            res = self.client.get(reverse("core-welcome"))

        self.assertDictEqual(res.data, expected_res)

    def test_block_metadata_header(self):
        cache.set(key="current_block", value=(1, "some hash"))

        with self.assertNumQueries(0):
            res = self.client.get(reverse("core-welcome"))

        self.assertEqual(res.headers["Block-Number"], "1")
        self.assertEqual(res.headers["Block-Hash"], "some hash")

    def test_stats(self):
        expected_res = {"account_count": 4, "dao_count": 2, "proposal_count": 2, "vote_count": 4}

        with self.assertNumQueries(4):
            res = self.client.get(reverse("core-stats"))

        self.assertDictEqual(res.data, expected_res)

    def test_config(self):
        expected_res = {
            "deposit_to_create_dao": settings.DEPOSIT_TO_CREATE_DAO,
            "deposit_to_create_proposal": settings.DEPOSIT_TO_CREATE_PROPOSAL,
            "block_creation_interval": settings.BLOCK_CREATION_INTERVAL,
        }

        with self.assertNumQueries(0):
            res = self.client.get(reverse("core-config"))

        self.assertDictEqual(res.data, expected_res)

    def test_account_get(self):
        expected_balance = {"free": 1, "reserved": 2, "frozen": 3, "flags": 4}

        with patch("substrateinterface.SubstrateInterface"):
            from core.substrate import substrate_service

            substrate_service.retrieve_account_balance = Mock(return_value=expected_balance)

        expected_res = {"address": "acc1", "balance": expected_balance}
        self.client.get(reverse("core-account-detail", kwargs={"pk": "acc1"}))

        with self.assertNumQueries(1):
            res = self.client.get(reverse("core-account-detail", kwargs={"pk": "acc1"}))

        self.assertDictEqual(res.data, expected_res)

    def test_account_get_list(self):
        expected_res = wrap_in_pagination_res(
            [{"address": "acc1"}, {"address": "acc2"}, {"address": "acc3"}, {"address": "acc4"}]
        )

        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-account-list"))

        self.assertDictEqual(res.data, expected_res)

    def test_dao_get(self):
        with self.assertNumQueries(4):
            res = self.client.get(reverse("core-dao-detail", kwargs={"pk": "dao1"}))

        self.assertDictEqual(res.data, expected_dao1_res)

    def test_dao_get_list(self):
        expected_res = wrap_in_pagination_res([expected_dao1_res, expected_dao2_res])

        with self.assertNumQueries(8):
            res = self.client.get(reverse("core-dao-list"))

        self.assertDictEqual(res.data, expected_res)

    @data(
        # query_params
        {"search": "dao2"},
        {"search": "acc2"},
        {"search": "dao2 name"},
    )
    def test_dao_list_filter(self, query_params):
        expected_res = wrap_in_pagination_res([expected_dao2_res])

        with self.assertNumQueries(5):
            res = self.client.get(reverse("core-dao-list"), query_params)

        self.assertDictEqual(res.data, expected_res)

    @data(
        # query_params, expected_res
        (
            {"ordering": "id"},
            [
                expected_dao1_res,
                expected_dao2_res,
                {
                    "id": "dao3",
                    "name": "3",
                    "creator_id": "acc1",
                    "owner_id": "acc2",
                    "asset_id": None,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": True,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 0,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
            ],
        ),
        (
            {"ordering": "name"},
            [
                {
                    "id": "dao3",
                    "name": "3",
                    "creator_id": "acc1",
                    "owner_id": "acc2",
                    "asset_id": None,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": True,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 0,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
                expected_dao1_res,
                expected_dao2_res,
            ],
        ),
        (
            {"ordering": "owner_id,id"},
            [
                expected_dao1_res,
                expected_dao2_res,
                {
                    "id": "dao3",
                    "name": "3",
                    "creator_id": "acc1",
                    "owner_id": "acc2",
                    "asset_id": None,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": True,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 0,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
            ],
        ),
    )
    def test_dao_list_order_by(self, case):
        query_params, expected_res = case
        models.Dao.objects.create(id="dao3", name="3", creator_id="acc1", owner_id="acc2", setup_complete=True)

        expected_res = wrap_in_pagination_res(expected_res)

        with self.assertNumQueries(10):
            res = self.client.get(reverse("core-dao-list"), query_params)

        self.assertDictEqual(res.data, expected_res)

    @data(
        # query_params, expected_res, expected query count
        (
            {"prioritise_owner": "acc2", "ordering": "-name"},
            [
                {
                    "id": "dao4",
                    "name": "dao4 name",
                    "creator_id": "acc2",
                    "owner_id": "acc2",
                    "asset_id": 4,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": False,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 1,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
                expected_dao2_res,
                {
                    "id": "dao3",
                    "name": "dao3 name",
                    "creator_id": "acc1",
                    "owner_id": "acc1",
                    "asset_id": 3,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": False,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 1,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
                expected_dao1_res,
            ],
            16,
        ),
        (
            {"prioritise_holder": "acc3", "ordering": "-name"},
            [
                {
                    "id": "dao4",
                    "name": "dao4 name",
                    "creator_id": "acc2",
                    "owner_id": "acc2",
                    "asset_id": 4,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": False,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 1,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
                {
                    "id": "dao3",
                    "name": "dao3 name",
                    "creator_id": "acc1",
                    "owner_id": "acc1",
                    "asset_id": 3,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": False,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 1,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
                expected_dao1_res,
                expected_dao2_res,
            ],
            16,
        ),
        (
            {"prioritise_owner": "acc2", "prioritise_holder": "acc3", "ordering": "name"},
            [
                expected_dao2_res,
                {
                    "id": "dao4",
                    "name": "dao4 name",
                    "creator_id": "acc2",
                    "owner_id": "acc2",
                    "asset_id": 4,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": False,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 1,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
                expected_dao1_res,
                {
                    "id": "dao3",
                    "name": "dao3 name",
                    "creator_id": "acc1",
                    "owner_id": "acc1",
                    "asset_id": 3,
                    "proposal_duration": None,
                    "proposal_token_deposit": None,
                    "minimum_majority_per_1024": None,
                    "setup_complete": False,
                    "metadata": None,
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 1,
                    "number_of_open_proposals": 0,
                    "most_recent_proposals": [],
                },
            ],
            17,
        ),
    )
    def test_dao_list_prioritised(self, case):
        query_params, expected_res, expected_query_count = case
        models.Dao.objects.create(id="dao3", name="dao3 name", creator_id="acc1", owner_id="acc1")
        models.Dao.objects.create(id="dao4", name="dao4 name", creator_id="acc2", owner_id="acc2")
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
        expected_res = [expected_dao1_res, expected_dao2_res]

        with self.assertNumQueries(8):
            res = self.client.get(reverse("core-dao-list"), {"prioritise_owner": "acc2"})

        self.assertCountEqual(res.data, expected_res)

    def test_dao_challenge(self):
        with self.assertNumQueries(1):
            res = self.client.get(reverse("core-dao-challenge", kwargs={"pk": "dao1"}))

        self.assertEqual(res.data["challenge"], cache.get("acc1"))

    def test_dao_add_metadata(self):
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.Account.objects.create(address=keypair.ss58_address)
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=acc)

        with open("core/tests/test_file.jpeg", "rb") as f:
            post_data = {
                "email": "some@email.com",
                "description_short": "short description",
                "description_long": "long description",
                "logo": base64.b64encode(f.read()).decode(),
            }
        expected_res = {
            "metadata": {
                "description_short": "short description",
                "description_long": "long description",
                "email": "some@email.com",
                "images": {
                    "logo": {
                        "content_type": "image/jpeg",
                        "large": {"url": "https://some_storage.some_region.com/DAO1/logo_large.jpeg"},
                        "medium": {"url": "https://some_storage.some_region.com/DAO1/logo_medium.jpeg"},
                        "small": {"url": "https://some_storage.some_region.com/DAO1/logo_small.jpeg"},
                    }
                },
            },
            "metadata_hash": "a1a0591662255e72aba330746eee9a50815d4580efaf3e60aa687c7ac12d473d",
            "metadata_url": "https://some_storage.some_region.com/DAO1/metadata.json",
        }

        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "DAO1"}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_201_CREATED)
        self.assertDictEqual(res.data, expected_res)

    def test_dao_add_metadata_multisig_signatory(self):
        multisig_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        signatory_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=multisig_kp.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(signatory_kp.sign(data=self.challenge_key)).decode()
        acc = models.MultiSig.objects.create(
            address=multisig_kp.ss58_address, signatories=[signatory_kp.ss58_address, "sig2"]
        )
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=acc)

        with open("core/tests/test_file.jpeg", "rb") as f:
            post_data = {
                "email": "some@email.com",
                "description_short": "short description",
                "description_long": "long description",
                "logo": base64.b64encode(f.read()).decode(),
            }
        expected_res = {
            "metadata": {
                "description_short": "short description",
                "description_long": "long description",
                "email": "some@email.com",
                "images": {
                    "logo": {
                        "content_type": "image/jpeg",
                        "large": {"url": "https://some_storage.some_region.com/DAO1/logo_large.jpeg"},
                        "medium": {"url": "https://some_storage.some_region.com/DAO1/logo_medium.jpeg"},
                        "small": {"url": "https://some_storage.some_region.com/DAO1/logo_small.jpeg"},
                    }
                },
            },
            "metadata_hash": "a1a0591662255e72aba330746eee9a50815d4580efaf3e60aa687c7ac12d473d",
            "metadata_url": "https://some_storage.some_region.com/DAO1/metadata.json",
        }

        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "DAO1"}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_201_CREATED)
        self.assertDictEqual(res.data, expected_res)

    def test_dao_add_metadata_multisig_addr(self):
        multisig_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=multisig_kp.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(multisig_kp.sign(data=self.challenge_key)).decode()
        acc = models.MultiSig.objects.create(address=multisig_kp.ss58_address, signatories=["sig1", "sig2"])
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=acc)

        with open("core/tests/test_file.jpeg", "rb") as f:
            post_data = {
                "email": "some@email.com",
                "description_short": "short description",
                "description_long": "long description",
                "logo": base64.b64encode(f.read()).decode(),
            }
        expected_res = {
            "metadata": {
                "description_short": "short description",
                "description_long": "long description",
                "email": "some@email.com",
                "images": {
                    "logo": {
                        "content_type": "image/jpeg",
                        "large": {"url": "https://some_storage.some_region.com/DAO1/logo_large.jpeg"},
                        "medium": {"url": "https://some_storage.some_region.com/DAO1/logo_medium.jpeg"},
                        "small": {"url": "https://some_storage.some_region.com/DAO1/logo_small.jpeg"},
                    }
                },
            },
            "metadata_hash": "a1a0591662255e72aba330746eee9a50815d4580efaf3e60aa687c7ac12d473d",
            "metadata_url": "https://some_storage.some_region.com/DAO1/metadata.json",
        }

        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "DAO1"}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_201_CREATED)
        self.assertDictEqual(res.data, expected_res)

    def test_dao_add_metadata_invalid_image_file(self):
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.Account.objects.create(address=keypair.ss58_address)
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=acc)

        post_data = {
            "email": "some@email.com",
            "description_short": "short description",
            "description_long": "long description",
            "logo": base64.b64encode(b"not an image").decode(),
        }
        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "DAO1"}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_400_BAD_REQUEST)
        self.assertDictEqual(
            res.data,
            {
                "logo": [
                    ErrorDetail(
                        string="Invalid image file. Allowed image types are: jpeg, jpg, png, gif.", code="invalid"
                    )
                ]
            },
        )

    def test_dao_add_metadata_logo_too_big(self):
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.Account.objects.create(address=keypair.ss58_address)
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=acc)

        with open("core/tests/test_file_5mb.jpeg", "rb") as f:
            post_data = {
                "email": "some@email.com",
                "description_short": "short description",
                "description_long": "long description",
                "logo": base64.b64encode(f.read()).decode(),
            }
        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "DAO1"}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_400_BAD_REQUEST)
        self.assertDictEqual(
            res.data, {"logo": [ErrorDetail(string="The uploaded file is too big. Max size: 2.0 mb.", code="invalid")]}
        )

    def test_dao_add_metadata_403(self):
        with open("core/tests/test_file.jpeg", "rb") as f:
            post_data = {
                "email": "some@email.com",
                "description": "some description",
                "logo": base64.b64encode(f.read()).decode(),
            }

        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "dao1"}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE="wrong signature",
        )

        self.assertEqual(res.status_code, HTTP_403_FORBIDDEN)
        self.assertEqual(
            res.data,
            {
                "error": ErrorDetail(
                    code="permission_denied",
                    string="Only the DAO owner has access to this action. "
                    "Header needs to contain signature=*signed-challenge*.",
                )
            },
        )

    def test_dao_add_metadata_403_not_a_signatory(self):
        multisig_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        other_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=multisig_kp.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(other_kp.sign(data=self.challenge_key)).decode()
        acc = models.MultiSig.objects.create(address=multisig_kp.ss58_address, signatories=["sig1", "sig2"])
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=acc)

        with open("core/tests/test_file.jpeg", "rb") as f:
            post_data = {
                "email": "some@email.com",
                "description": "some description",
                "logo": base64.b64encode(f.read()).decode(),
            }

        res = self.client.post(
            reverse("core-dao-add-metadata", kwargs={"pk": "DAO1"}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_403_FORBIDDEN)
        self.assertEqual(
            res.data,
            {
                "error": ErrorDetail(
                    code="permission_denied",
                    string="Only the DAO owner has access to this action. "
                    "Header needs to contain signature=*signed-challenge*.",
                )
            },
        )

    def test_asset_get(self):
        expected_res = {"id": 1, "dao_id": "dao1", "owner_id": "acc1", "total_supply": 1000}

        with self.assertNumQueries(1):
            res = self.client.get(reverse("core-asset-detail", kwargs={"pk": 1}))

        self.assertDictEqual(res.data, expected_res)

    def test_asset_get_list(self):
        expected_res = wrap_in_pagination_res(
            [
                {"id": 1, "dao_id": "dao1", "owner_id": "acc1", "total_supply": 1000},
                {"id": 2, "dao_id": "dao2", "owner_id": "acc2", "total_supply": 200},
            ]
        )
        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-asset-list"))

        self.assertDictEqual(res.data, expected_res)

    def test_proposal_get(self):
        expected_res = {
            "id": 1,
            "dao_id": "dao1",
            "creator_id": "acc1",
            "metadata": {"a": 1},
            "metadata_url": "url1",
            "metadata_hash": "hash1",
            "fault": None,
            "status": models.ProposalStatus.RUNNING,
            "title": "t1",
            "votes": {"pro": 800, "contra": 100, "abstained": 100, "total": 1000},
            "birth_block_number": 10,
            "setup_complete": False,
        }

        with self.assertNumQueries(2):
            res = self.client.get(reverse("core-proposal-detail", kwargs={"pk": 1}))

        self.assertDictEqual(res.data, expected_res)

    def test_proposal_list(self):
        expected_res = wrap_in_pagination_res(
            [
                {
                    "id": 1,
                    "dao_id": "dao1",
                    "creator_id": "acc1",
                    "metadata": {"a": 1},
                    "metadata_url": "url1",
                    "metadata_hash": "hash1",
                    "fault": None,
                    "status": models.ProposalStatus.RUNNING,
                    "title": "t1",
                    "votes": {"pro": 800, "contra": 100, "abstained": 100, "total": 1000},
                    "birth_block_number": 10,
                    "setup_complete": False,
                },
                {
                    "id": 2,
                    "dao_id": "dao2",
                    "creator_id": "acc2",
                    "metadata": {"a": 2},
                    "metadata_url": "url2",
                    "metadata_hash": "hash2",
                    "fault": "some reason",
                    "status": models.ProposalStatus.FAULTED,
                    "title": None,
                    "votes": {"pro": 0, "contra": 200, "abstained": 0, "total": 200},
                    "birth_block_number": 15,
                    "setup_complete": True,
                },
            ]
        )

        with self.assertNumQueries(3):
            res = self.client.get(reverse("core-proposal-list"))

        self.assertDictEqual(res.data, expected_res)

    def test_proposal_add_metadata(self):
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.Account.objects.create(address=keypair.ss58_address)
        models.Proposal.objects.create(id=3, dao_id="dao1", creator=acc, birth_block_number=10)
        cache.set(key="acc1", value=self.challenge_key, timeout=5)
        post_data = {
            "title": "some title",
            "description": '<p><u>asd\t<a href="https://google.com" '
            'rel="noopener noreferrer" target="_blank">werwerwerwerwer</a></u></p>',
            "url": "https://www.some-url.com/",
        }
        expected_res = {
            "metadata": post_data,
            "metadata_hash": "d22aaac3a17b4510ef9bd8ed67188bb6fbb29a75347aed1d23b6dcf3cf6e6c7b",
            "metadata_url": "https://some_storage.some_region.com/dao1/proposals/3/metadata.json",
        }

        with self.assertNumQueries(4):
            res = self.client.post(
                reverse("core-proposal-add-metadata", kwargs={"pk": 3}),
                post_data,
                content_type="application/json",
                HTTP_SIGNATURE=signature,
            )

        self.assertEqual(res.status_code, HTTP_201_CREATED, res.data)
        self.assertDictEqual(res.data, expected_res)

    def test_proposal_add_metadata_403(self):
        post_data = {
            "title": "some title",
            "description": "short description",
            "url": "https://www.some-url.com/",
        }

        with self.assertNumQueries(3):
            res = self.client.post(
                reverse("core-proposal-add-metadata", kwargs={"pk": 1}),
                post_data,
                content_type="application/json",
                HTTP_SIGNATURE="wrong signature",
            )

        self.assertEqual(res.status_code, HTTP_403_FORBIDDEN)
        self.assertEqual(
            res.data,
            {
                "error": ErrorDetail(
                    code="permission_denied",
                    string="Only the Proposal creator has access to this action. "
                    "Header needs to contain signature=*signed-challenge*.",
                )
            },
        )

    @patch("core.substrate.substrate_service")
    def test_proposal_report_faulted(self, substrate_mock):
        substrate_mock.create_multisig_transaction_call_hash.return_value = "some_hash"
        cache.clear()
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.MultiSig.objects.create(address=keypair.ss58_address)
        dao = models.Dao.objects.create(owner=acc)
        prop = models.Proposal.objects.create(id=3, dao=dao, birth_block_number=0)
        asset = models.Asset.objects.create(id=3, dao=dao, owner=acc, total_supply=10)
        models.AssetHolding.objects.create(owner=acc, asset=asset, balance=10)
        post_data = {"reason": "very good reason"}
        expected_transactions = [
            models.MultiSigTransaction(
                multisig=acc,
                dao=dao,
                proposal=prop,
                call={
                    "call_hash": "some_hash",
                    "module": "Votes",
                    "function": "fault_proposal",
                    "args": {"proposal_id": prop.id, **post_data},
                },
                call_hash="some_hash",
            )
        ]

        with self.assertNumQueries(7):
            res = self.client.post(
                reverse("core-proposal-report-faulted", kwargs={"pk": prop.id}),
                post_data,
                content_type="application/json",
                HTTP_SIGNATURE=signature,
            )

        self.assertEqual(res.status_code, HTTP_201_CREATED)
        self.assertEqual(res.data, {**post_data, "proposal_id": prop.id})
        self.assertModelsEqual(
            models.MultiSigTransaction.objects.all(),
            expected_transactions,
            ignore_fields=("id", "updated_at", "created_at"),
        )
        substrate_mock.create_multisig_transaction_call_hash.assert_called_once_with(
            module="Votes", function="fault_proposal", args={"proposal_id": prop.id, "reason": "very good reason"}
        )

    @patch("core.substrate.substrate_service")
    def test_proposal_report_faulted_no_multisig(self, substrate_mock):
        cache.clear()
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.Account.objects.create(address=keypair.ss58_address)
        dao = models.Dao.objects.create(owner=acc)
        prop = models.Proposal.objects.create(id=3, dao=dao, birth_block_number=0)
        asset = models.Asset.objects.create(id=3, dao=dao, owner=acc, total_supply=10)
        models.AssetHolding.objects.create(owner=acc, asset=asset, balance=10)
        post_data = {"reason": "very good reason"}

        with self.assertNumQueries(5):
            res = self.client.post(
                reverse("core-proposal-report-faulted", kwargs={"pk": prop.id}),
                post_data,
                content_type="application/json",
                HTTP_SIGNATURE=signature,
            )

        self.assertEqual(res.status_code, HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data, {"detail": "The corresponding DAO is not managed by a MultiSig Account."})
        self.assertListEqual(list(models.MultiSigTransaction.objects.all()), [])
        substrate_mock.create_multisig_transaction_call_hash.assert_not_called()

    @patch("core.substrate.substrate_service")
    def test_proposal_report_faulted_no_holdings(self, substrate_mock):
        cache.clear()
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.MultiSig.objects.create(address=keypair.ss58_address)
        dao = models.Dao.objects.create(owner=acc)
        prop = models.Proposal.objects.create(id=3, dao=dao, birth_block_number=0)
        post_data = {"reason": "very good reason"}

        with self.assertNumQueries(2):
            res = self.client.post(
                reverse("core-proposal-report-faulted", kwargs={"pk": prop.id}),
                post_data,
                content_type="application/json",
                HTTP_SIGNATURE=signature,
            )

        self.assertEqual(res.status_code, HTTP_403_FORBIDDEN)
        self.assertEqual(
            res.data,
            {
                "error": ErrorDetail(
                    string="This request's header needs to contain signature=*signed-challenge*.",
                    code="permission_denied",
                )
            },
        )
        substrate_mock.create_multisig_transaction_call_hash.assert_not_called()

    @patch("core.substrate.substrate_service")
    def test_proposal_report_faulted_throttle(self, substrate_mock):
        substrate_mock.create_multisig_transaction_call_hash.return_value = "some_hash"
        cache.clear()
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(keypair.sign(data=self.challenge_key)).decode()
        acc = models.MultiSig.objects.create(address=keypair.ss58_address)
        dao = models.Dao.objects.create(owner=acc)
        prop = models.Proposal.objects.create(id=3, dao=dao, birth_block_number=0)
        asset = models.Asset.objects.create(id=3, dao=dao, owner=acc, total_supply=10)
        models.AssetHolding.objects.create(owner=acc, asset=asset, balance=10)
        post_data = {"reason": "very good reason"}

        call_view = partial(
            self.client.post,
            reverse("core-proposal-report-faulted", kwargs={"pk": prop.id}),
            post_data,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )
        for count in range(7):
            substrate_mock.create_multisig_transaction_call_hash.reset_mock()
            if count < 3:
                with self.assertNumQueries(7):
                    res = call_view()
                self.assertEqual(res.status_code, HTTP_201_CREATED)
                self.assertEqual(res.data, {**post_data, "proposal_id": prop.id})
                substrate_mock.create_multisig_transaction_call_hash.assert_called_once_with(
                    module="Votes",
                    function="fault_proposal",
                    args={"proposal_id": 3, "reason": "very good reason"},
                )
            elif count < 5:
                with self.assertNumQueries(4):
                    res = call_view()
                self.assertEqual(res.status_code, HTTP_400_BAD_REQUEST)
                self.assertEqual(res.data, {"detail": "The proposal report maximum has already been reached."})
                substrate_mock.create_multisig_transaction_call_hash.assert_not_called()
            else:
                with self.assertNumQueries(2):
                    res = call_view()
                self.assertEqual(res.status_code, HTTP_429_TOO_MANY_REQUESTS)
                self.assertEqual(
                    res.data,
                    {
                        "detail": ErrorDetail(
                            "Request was throttled. Expected available in 3600 seconds.", code="throttled"
                        )
                    },
                )
                substrate_mock.create_multisig_transaction_call_hash.assert_not_called()

    def test_reports(self):
        models.ProposalReport.objects.create(proposal_id=1, reason="reason 1")
        models.ProposalReport.objects.create(proposal_id=1, reason="reason 2")
        models.ProposalReport.objects.create(proposal_id=2, reason="reason 3")  # should not appear
        expected_res = [
            {"proposal_id": 1, "reason": "reason 1"},
            {"proposal_id": 1, "reason": "reason 2"},
        ]
        with self.assertNumQueries(1):
            res = self.client.get(reverse("core-proposal-reports", kwargs={"pk": 1}))

        self.assertCountEqual(res.data, expected_res)

    def test_get_multisig(self):
        addr = "some_addr"
        models.MultiSig.objects.create(address=addr, signatories=["sig1", "sig2"], threshold=2)
        expected_res = {"address": addr, "signatories": ["sig1", "sig2"], "threshold": 2, "dao_id": None}

        res = self.client.get(reverse("core-multisig-detail", kwargs={"address": addr}))

        self.assertEqual(res.status_code, HTTP_200_OK)
        self.assertDictEqual(res.data, expected_res)

    def test_get_list_multisig(self):
        models.MultiSig.objects.create(address="addr1", signatories=["sig1", "sig2"], threshold=2, dao_id="dao1")
        models.MultiSig.objects.create(address="addr2", signatories=["sig1", "sig2", "sig3"], threshold=3)
        expected_multisigs = [
            {"address": "addr1", "signatories": ["sig1", "sig2"], "threshold": 2, "dao_id": "dao1"},
            {"address": "addr2", "signatories": ["sig1", "sig2", "sig3"], "threshold": 3, "dao_id": None},
        ]

        res = self.client.get(reverse("core-multisig-list"), {"ordering": "address"})

        self.assertEqual(res.status_code, HTTP_200_OK)
        self.assertDictEqual(res.data, wrap_in_pagination_res(expected_multisigs))

    @patch("core.substrate.substrate_service")
    def test_create_multisig(self, substrate_mock):
        addr = "some_addr"
        substrate_mock.create_multisig_account.return_value = Mock(ss58_address=addr)
        payload = {"signatories": ["sig1", "sig2"], "threshold": 2}
        expected_res = {"address": addr, "signatories": ["sig1", "sig2"], "threshold": 2, "dao_id": None}

        res = self.client.post(reverse("core-multisig-list"), data=payload, content_type="application/json")

        self.assertEqual(res.status_code, HTTP_201_CREATED)
        self.assertDictEqual(res.data, expected_res)

    @patch("core.substrate.substrate_service")
    def test_create_multisig_existing(self, substrate_mock):
        addr = "some_addr"
        substrate_mock.create_multisig_account.return_value = Mock(ss58_address=addr)
        payload = {"signatories": ["sig1", "sig2"], "threshold": 3}
        models.MultiSig.objects.create(threshold=2, address=addr, dao_id="dao1")
        expected_res = {"address": addr, "signatories": ["sig1", "sig2"], "threshold": 3, "dao_id": "dao1"}
        expected_multisigs = [
            models.MultiSig(signatories=["sig1", "sig2"], threshold=3, account_ptr_id=addr, address=addr, dao_id="dao1")
        ]

        res = self.client.post(reverse("core-multisig-list"), data=payload, content_type="application/json")

        self.assertEqual(res.status_code, HTTP_200_OK)
        self.assertDictEqual(res.data, expected_res)
        self.assertModelsEqual(models.MultiSig.objects.order_by("address"), expected_multisigs)

    def test_get_multisig_transaction(self):
        call_hash = "some_call_hash"
        call_data = "call_data_test"
        call = {
            "hash": call_hash,
            "data": call_data,
            "module": "some_module",
            "function": "some_function",
            "args": {"some": "args"},
        }
        txn1 = models.MultiSigTransaction.objects.create(
            multisig=models.MultiSig.objects.create(address="addr1", signatories=["sig1", "sig2"], threshold=2),
            dao_id="dao1",
            asset_id=1,
            proposal_id=1,
            call_data=call_data,
            call_hash=call_hash,
            call=call,
            approvers=["sig1", "sig2"],
        )
        expected_res = {
            "id": txn1.id,
            "multisig_address": "addr1",
            "dao_id": "dao1",
            "call": call,
            "call_data": call_data,
            "call_hash": call_hash,
            "corresponding_models": {
                "asset": {"id": 1, "dao_id": "dao1", "owner_id": "acc1", "total_supply": 1000},
                "dao": {
                    "id": "dao1",
                    "name": "dao1 name",
                    "creator_id": "acc1",
                    "owner_id": "acc1",
                    "asset_id": 1,
                    "proposal_duration": 10,
                    "proposal_token_deposit": 123,
                    "minimum_majority_per_1024": 50,
                    "setup_complete": False,
                    "metadata": {"some": "data"},
                    "metadata_url": None,
                    "metadata_hash": None,
                    "number_of_token_holders": 4,
                    "number_of_open_proposals": 1,
                    "most_recent_proposals": [1],
                },
                "proposal": {
                    "id": 1,
                    "dao_id": "dao1",
                    "creator_id": "acc1",
                    "status": models.ProposalStatus.RUNNING,
                    "title": "t1",
                    "fault": None,
                    "votes": {"pro": 800, "contra": 100, "abstained": 100, "total": 1000},
                    "metadata": {"a": 1},
                    "metadata_url": "url1",
                    "metadata_hash": "hash1",
                    "birth_block_number": 10,
                    "setup_complete": False,
                },
            },
            "status": models.TransactionStatus.PENDING,
            "threshold": 2,
            "approvers": ["sig1", "sig2"],
            "last_approver": "sig2",
            "executed_at": None,
            "canceled_by": None,
            "created_at": fmt_dt(txn1.created_at),
            "updated_at": fmt_dt(txn1.updated_at),
        }

        res = self.client.get(reverse("core-multisig-transaction-detail", kwargs={"pk": txn1.id}))

        self.assertEqual(res.status_code, HTTP_200_OK)
        self.assertDictEqual(res.data, expected_res)

    def test_list_multisig_transactions(self):
        txn1 = models.MultiSigTransaction.objects.create(
            multisig=models.MultiSig.objects.create(address="addr1", signatories=["sig1", "sig2"], threshold=2),
            dao_id="dao1",
            call_data="call_data1",
            call_hash="call_hash1",
            call={
                "hash": "call_hash1",
                "module": "some_module1",
                "function": "some_function1",
                "args": {"some1": "args1"},
                "data": "call_data1",
            },
            approvers=["sig1", "sig2"],
            executed_at=now(),
            status=models.TransactionStatus.EXECUTED,
        )
        txn2 = models.MultiSigTransaction.objects.create(
            multisig=models.MultiSig.objects.create(address="addr2", signatories=["sig3", "sig4"], threshold=3),
            call_hash="call_hash2",
            call_data="call_data2",
        )
        expected_res = wrap_in_pagination_res(
            [
                {
                    "id": txn1.id,
                    "multisig_address": "addr1",
                    "dao_id": "dao1",
                    "call": {
                        "hash": "call_hash1",
                        "module": "some_module1",
                        "function": "some_function1",
                        "args": {"some1": "args1"},
                        "data": "call_data1",
                    },
                    "call_data": "call_data1",
                    "call_hash": "call_hash1",
                    "corresponding_models": {
                        "asset": None,
                        "dao": {
                            "id": "dao1",
                            "name": "dao1 name",
                            "creator_id": "acc1",
                            "owner_id": "acc1",
                            "asset_id": 1,
                            "proposal_duration": 10,
                            "proposal_token_deposit": 123,
                            "minimum_majority_per_1024": 50,
                            "setup_complete": False,
                            "metadata": {"some": "data"},
                            "metadata_url": None,
                            "metadata_hash": None,
                            "number_of_token_holders": 4,
                            "number_of_open_proposals": 1,
                            "most_recent_proposals": [1],
                        },
                        "proposal": None,
                    },
                    "status": models.TransactionStatus.EXECUTED,
                    "threshold": 2,
                    "approvers": ["sig1", "sig2"],
                    "last_approver": "sig2",
                    "executed_at": fmt_dt(txn1.executed_at),
                    "canceled_by": None,
                    "created_at": fmt_dt(txn1.created_at),
                    "updated_at": fmt_dt(txn1.updated_at),
                },
                {
                    "id": txn2.id,
                    "multisig_address": "addr2",
                    "dao_id": None,
                    "call": None,
                    "call_hash": "call_hash2",
                    "call_data": "call_data2",
                    "corresponding_models": {
                        "asset": None,
                        "dao": None,
                        "proposal": None,
                    },
                    "status": models.TransactionStatus.PENDING,
                    "threshold": 3,
                    "approvers": [],
                    "last_approver": None,
                    "executed_at": None,
                    "canceled_by": None,
                    "created_at": fmt_dt(txn2.created_at),
                    "updated_at": fmt_dt(txn2.updated_at),
                },
            ]
        )

        res = self.client.get(reverse("core-multisig-transaction-list"))

        self.assertEqual(res.status_code, HTTP_200_OK)
        self.assertDictEqual(res.data, expected_res)

    @patch("core.substrate.substrate_service.create_multisig_transaction_call_hash")
    def test_create_multisig_transaction(self, create_multisig_transaction_call_hash_mock):
        create_multisig_transaction_call_hash_mock.return_value = "some_call_hash"
        multisig_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        signatory_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=multisig_kp.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(signatory_kp.sign(data=self.challenge_key)).decode()
        multisig = models.MultiSig.objects.create(
            address=multisig_kp.ss58_address, signatories=[signatory_kp.ss58_address, "sig2"], threshold=2
        )
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=multisig)
        payload = {
            "hash": "some_call_hash",
            "module": "some_module",
            "function": "some_func",
            "data": "call_data_test",
            "args": {"a": "1", "b": 2},
        }
        expected_transactions = [
            models.MultiSigTransaction(
                multisig=multisig,
                dao_id="DAO1",
                call_data="call_data_test",
                call_hash="some_call_hash",
                call_function="some_func",
                call=payload,
            )
        ]

        res = self.client.post(
            reverse("core-dao-create-multisig-transaction", kwargs={"pk": "DAO1"}),
            payload,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_201_CREATED)
        self.assertDictEqual(res.data, MultiSigTransactionSerializer(models.MultiSigTransaction.objects.get()).data)
        self.assertModelsEqual(
            models.MultiSigTransaction.objects.all(),
            expected_transactions,
            ignore_fields=("created_at", "updated_at", "id"),
        )

    @patch("core.substrate.substrate_service.create_multisig_transaction_call_hash")
    def test_create_multisig_transaction_wrong_hash(self, create_multisig_transaction_call_hash_mock):
        create_multisig_transaction_call_hash_mock.return_value = "different_hash"
        multisig_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        signatory_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=multisig_kp.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(signatory_kp.sign(data=self.challenge_key)).decode()
        multisig = models.MultiSig.objects.create(
            address=multisig_kp.ss58_address, signatories=[signatory_kp.ss58_address, "sig2"], threshold=2
        )
        models.Dao.objects.create(id="DAO1", name="dao1 name", owner=multisig)
        payload = {
            "hash": "some_call_hash",
            "module": "some_module",
            "function": "some_func",
            "args": {"a": "1", "b": 2},
            "data": "call_data_test",
        }

        res = self.client.post(
            reverse("core-dao-create-multisig-transaction", kwargs={"pk": "DAO1"}),
            payload,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_400_BAD_REQUEST)
        self.assertDictEqual(res.data, {"message": "Invalid call hash."})
        self.assertListEqual(list(models.MultiSigTransaction.objects.all()), [])

    @patch("core.substrate.substrate_service.create_multisig_transaction_call_hash")
    def test_create_multisig_transaction_wrong_call_data(self, create_multisig_transaction_call_hash_mock):
        create_multisig_transaction_call_hash_mock.side_effect = ValueError()
        multisig_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        signatory_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=multisig_kp.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(signatory_kp.sign(data=self.challenge_key)).decode()

        payload = {
            "hash": "another_hash",
            "module": "another_module",
            "function": "different_func",
            "args": {},
            "data": "another_call_data_test",
        }

        res = self.client.post(
            reverse("core-dao-create-multisig-transaction", kwargs={"pk": "DAO1"}),
            payload,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_400_BAD_REQUEST)
        self.assertDictEqual(res.data, {"message": "Invalid call data."})
        self.assertListEqual(list(models.MultiSigTransaction.objects.all()), [])

    @patch("core.substrate.substrate_service.create_multisig_transaction_call_hash")
    def test_create_multisig_transaction_missing_multisig(self, create_multisig_transaction_call_hash_mock):
        create_multisig_transaction_call_hash_mock.return_value = "some_call_hash"
        signatory_kp = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=signatory_kp.ss58_address, value=self.challenge_key, timeout=5)
        signature = base64.b64encode(signatory_kp.sign(data=self.challenge_key)).decode()
        models.Dao.objects.create(
            id="DAO1",
            name="dao1 name",
            owner=models.Account.objects.create(address=signatory_kp.ss58_address),
        )
        payload = {
            "hash": "some_call_hash",
            "module": "some_module",
            "function": "some_func",
            "args": {"a": "1", "b": 2},
            "data": "call_data_test",
        }

        res = self.client.post(
            reverse("core-dao-create-multisig-transaction", kwargs={"pk": "DAO1"}),
            payload,
            content_type="application/json",
            HTTP_SIGNATURE=signature,
        )

        self.assertEqual(res.status_code, HTTP_400_BAD_REQUEST)
        self.assertDictEqual(res.data, {"message": "No MultiSig Account exists for the given Dao."})
        self.assertListEqual(list(models.MultiSigTransaction.objects.all()), [])
