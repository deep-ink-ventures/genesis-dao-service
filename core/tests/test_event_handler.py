import json
import random
from io import BytesIO
from unittest.mock import call, patch

from core import models
from core.event_handler import SubstrateEventHandler, substrate_event_handler
from core.file_handling.file_handler import file_handler
from core.tests.testcases import IntegrationTestCase


class EventHandlerTest(IntegrationTestCase):
    def test__create_accounts(self):
        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            event_data={
                "not": "interesting",
                "System": {
                    "not": "interesting",
                    "NewAccount": [
                        {"account": "acc1", "not": "interesting"},
                        {"account": "acc2"},
                    ],
                },
            },
        )
        expected_accs = [
            models.Account(address="acc1"),
            models.Account(address="acc2"),
        ]

        with self.assertNumQueries(1):
            substrate_event_handler._create_accounts(block)

        self.assertModelsEqual(models.Account.objects.all(), expected_accs)

    def test__create_daos(self):
        models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            extrinsic_data={
                "not": "interesting",
                "DaoCore": {
                    "create_dao": [
                        {"dao_id": "dao1", "dao_name": "dao1 name", "not": "interesting"},
                        {"dao_id": "dao2", "dao_name": "dao2 name"},
                        # should not be created cause of missing corresponding event
                        {"dao_id": "dao3", "dao_name": "dao3 name"},
                    ]
                },
            },
            event_data={
                "not": "interesting",
                "DaoCore": {
                    "DaoCreated": [
                        {"owner": "acc1", "dao_id": "dao1", "not": "interesting"},
                        {"owner": "acc2", "dao_id": "dao2", "not": "interesting"},
                    ]
                },
            },
        )
        expected_daos = [
            models.Dao(id="dao1", name="dao1 name", owner_id="acc1"),
            models.Dao(id="dao2", name="dao2 name", owner_id="acc2"),
        ]

        with self.assertNumQueries(1):
            substrate_event_handler._create_daos(block)

        self.assertModelsEqual(models.Dao.objects.all(), expected_daos)

    def test__delete_daos(self):
        models.Dao.objects.create(id="dao1", name="dao1 name", owner=models.Account.objects.create(address="acc1"))
        models.Dao.objects.create(id="dao2", name="dao2 name", owner=models.Account.objects.create(address="acc2"))
        models.Dao.objects.create(id="dao3", name="dao3 name", owner_id="acc1")
        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            event_data={
                "not": "interesting",
                "DaoCore": {
                    "DaoDestroyed": [
                        {"dao_id": "dao1", "not": "interesting"},
                        {"dao_id": "dao3", "not": "interesting"},
                    ]
                },
            },
        )
        expected_daos = [
            models.Dao(id="dao2", name="dao2 name", owner_id="acc2"),
        ]

        with self.assertNumQueries(3):
            substrate_event_handler._delete_daos(block)

        self.assertModelsEqual(models.Dao.objects.all(), expected_daos)

    def test__create_assets(self):
        models.Dao.objects.create(id="dao1", name="dao1 name", owner=models.Account.objects.create(address="acc1"))
        models.Dao.objects.create(id="dao2", name="dao2 name", owner=models.Account.objects.create(address="acc2"))
        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            event_data={
                "not": "interesting",
                "Assets": {
                    "not": "interesting",
                    "Issued": [
                        {"asset_id": 1, "total_supply": 100, "owner": "acc1", "not": "interesting"},
                        {"asset_id": 2, "total_supply": 200, "owner": "acc2", "not": "interesting"},
                    ],
                    "MetadataSet": [
                        {"name": "dao1 name", "symbol": "dao1", "asset_id": 1, "not": "interesting"},
                        {"name": "dao2 name", "symbol": "dao2", "asset_id": 2, "not": "interesting"},
                    ],
                },
            },
        )
        expected_assets = [
            models.Asset(id=1, total_supply=100, owner_id="acc1", dao_id="dao1"),
            models.Asset(id=2, total_supply=200, owner_id="acc2", dao_id="dao2"),
        ]
        expected_asset_holdings = [
            models.AssetHolding(asset_id=1, owner_id="acc1", balance=100),
            models.AssetHolding(asset_id=2, owner_id="acc2", balance=200),
        ]

        with self.assertNumQueries(2):
            substrate_event_handler._create_assets(block)

        self.assertModelsEqual(models.Asset.objects.all(), expected_assets)
        self.assertModelsEqual(
            models.AssetHolding.objects.all(), expected_asset_holdings, ignore_fields=("id", "created_at", "updated_at")
        )

    def test__transfer_assets(self):
        models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        models.Account.objects.create(address="acc3")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1")
        models.Dao.objects.create(id="dao2", name="dao2 name", owner_id="acc2")
        models.Dao.objects.create(id="dao3", name="dao3 name", owner_id="acc3")
        models.Dao.objects.create(id="dao4", name="dao4 name", owner_id="acc3")
        models.Asset.objects.create(id=1, total_supply=150, owner_id="acc1", dao_id="dao1"),
        models.Asset.objects.create(id=2, total_supply=250, owner_id="acc2", dao_id="dao2"),
        models.Asset.objects.create(id=3, total_supply=300, owner_id="acc3", dao_id="dao3"),
        models.Asset.objects.create(id=4, total_supply=400, owner_id="acc3", dao_id="dao4"),
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc1", balance=100),
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc3", balance=50),
        models.AssetHolding.objects.create(asset_id=2, owner_id="acc2", balance=200),
        models.AssetHolding.objects.create(asset_id=2, owner_id="acc3", balance=50),
        models.AssetHolding.objects.create(asset_id=3, owner_id="acc2", balance=50),
        models.AssetHolding.objects.create(asset_id=3, owner_id="acc3", balance=300),
        models.AssetHolding.objects.create(asset_id=4, owner_id="acc3", balance=400),
        transfers = [
            {"asset_id": 1, "amount": 10, "from": "acc1", "to": "acc2", "not": "interesting"},
            {"asset_id": 1, "amount": 15, "from": "acc1", "to": "acc2", "not": "interesting"},
            {"asset_id": 1, "amount": 25, "from": "acc3", "to": "acc2", "not": "interesting"},
            {"asset_id": 2, "amount": 20, "from": "acc2", "to": "acc1", "not": "interesting"},
            {"asset_id": 3, "amount": 50, "from": "acc3", "to": "acc2", "not": "interesting"},
        ]
        random.shuffle(transfers)  # order mustn't matter
        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            event_data={
                "not": "interesting",
                "Assets": {
                    "not": "interesting",
                    "Transferred": transfers,
                },
            },
        )

        with self.assertNumQueries(3):
            substrate_event_handler._transfer_assets(block)

        expected_asset_holdings = [
            models.AssetHolding(asset_id=1, owner_id="acc1", balance=75),  # 100 - 10 - 15
            models.AssetHolding(asset_id=1, owner_id="acc2", balance=50),  # 0 + 10 + 15 + 25
            models.AssetHolding(asset_id=1, owner_id="acc3", balance=25),  # 50 - 25
            models.AssetHolding(asset_id=2, owner_id="acc1", balance=20),  # 0 + 20
            models.AssetHolding(asset_id=2, owner_id="acc2", balance=180),  # 200 - 20
            models.AssetHolding(asset_id=2, owner_id="acc3", balance=50),  # 50
            models.AssetHolding(asset_id=3, owner_id="acc2", balance=100),  # 50 + 50
            models.AssetHolding(asset_id=3, owner_id="acc3", balance=250),  # 300 - 50
            models.AssetHolding(asset_id=4, owner_id="acc3", balance=400),  # 300
        ]
        self.assertModelsEqual(
            models.AssetHolding.objects.order_by("asset_id", "owner_id"),
            expected_asset_holdings,
            ignore_fields=("id", "created_at", "updated_at"),
        )

    @patch("core.file_handling.file_handler.urlopen")
    def test__set_dao_metadata(self, urlopen_mock):
        models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        models.Account.objects.create(address="acc3")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1")
        models.Dao.objects.create(id="dao2", name="dao2 name", owner_id="acc2")
        models.Dao.objects.create(id="dao3", name="dao3 name", owner_id="acc3")
        metadata_1 = {"a": 1}
        file_1 = BytesIO(json.dumps(metadata_1).encode())
        metadata_hash_1 = file_handler._hash(file_1.getvalue())
        metadata_2 = {"a": 2}
        file_2 = BytesIO(json.dumps(metadata_2).encode())
        metadata_hash_2 = file_handler._hash(file_2.getvalue())

        urlopen_mock.side_effect = lambda url: {"url1": file_1, "url2": file_2}.get(url)
        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            extrinsic_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "set_metadata": [
                        {"dao_id": "dao1", "hash": metadata_hash_1, "meta": "url1", "not": "interesting"},
                        {"dao_id": "dao2", "hash": metadata_hash_2, "meta": "url2", "not": "interesting"},
                        # should not be updated cause of missing corresponding event
                        {"dao_id": "dao3", "hash": "hash33", "meta": "url3", "not": "interesting"},
                    ],
                },
            },
            event_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "DaoMetadataSet": [
                        {"dao_id": "dao1", "not": "interesting"},
                        {"dao_id": "dao2", "not": "interesting"},
                    ],
                },
            },
        )
        expected_daos = [
            models.Dao(
                id="dao1",
                name="dao1 name",
                owner_id="acc1",
                metadata_hash=metadata_hash_1,
                metadata_url="url1",
                metadata=metadata_1,
            ),
            models.Dao(
                id="dao2",
                name="dao2 name",
                owner_id="acc2",
                metadata_hash=metadata_hash_2,
                metadata_url="url2",
                metadata=metadata_2,
            ),
            models.Dao(id="dao3", name="dao3 name", owner_id="acc3", metadata_hash=None, metadata_url=None),
        ]

        with self.assertNumQueries(2):
            substrate_event_handler._set_dao_metadata(block)

        urlopen_mock.assert_has_calls([call("url1"), call("url2")], any_order=True)
        self.assertModelsEqual(models.Dao.objects.order_by("id"), expected_daos)

    @patch("core.file_handling.file_handler.urlopen")
    @patch("core.tasks.logger")
    def test__set_dao_metadata_hash_mismatch(self, logger_mock, urlopen_mock):
        models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        models.Account.objects.create(address="acc3")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1")
        models.Dao.objects.create(id="dao2", name="dao2 name", owner_id="acc2")
        models.Dao.objects.create(id="dao3", name="dao3 name", owner_id="acc3")
        metadata_1 = {"a": 1}
        file_1 = BytesIO(json.dumps(metadata_1).encode())
        metadata_hash_1 = file_handler._hash(file_1.getvalue())
        metadata_2 = {"a": 2}
        file_2 = BytesIO(json.dumps(metadata_2).encode())

        urlopen_mock.side_effect = lambda url: {"url1": file_1, "url2": file_2}.get(url)

        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            extrinsic_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "set_metadata": [
                        {"dao_id": "dao1", "hash": metadata_hash_1, "meta": "url1", "not": "interesting"},
                        {"dao_id": "dao2", "hash": "wrong hash", "meta": "url2", "not": "interesting"},
                        # should not be updated cause of missing corresponding event
                        {"dao_id": "dao3", "hash": "hash33", "meta": "url3", "not": "interesting"},
                    ],
                },
            },
            event_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "DaoMetadataSet": [
                        {"dao_id": "dao1", "not": "interesting"},
                        {"dao_id": "dao2", "not": "interesting"},
                    ],
                },
            },
        )
        expected_daos = [
            models.Dao(
                id="dao1",
                name="dao1 name",
                owner_id="acc1",
                metadata_url="url1",
                metadata_hash=metadata_hash_1,
                metadata=metadata_1,
            ),
            models.Dao(id="dao2", name="dao2 name", owner_id="acc2", metadata_url="url2", metadata_hash="wrong hash"),
            models.Dao(id="dao3", name="dao3 name", owner_id="acc3"),
        ]

        with self.assertNumQueries(2):
            substrate_event_handler._set_dao_metadata(block)

        urlopen_mock.assert_has_calls([call("url1"), call("url2")], any_order=True)
        logger_mock.error.assert_called_once_with("Hash mismatch while fetching DAO metadata from provided url.")
        self.assertModelsEqual(models.Dao.objects.order_by("id"), expected_daos)

    @patch("core.file_handling.file_handler.FileHandler.download_metadata")
    @patch("core.tasks.logger")
    def test__set_dao_metadata_exception(self, logger_mock, download_metadata_mock):
        models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        models.Account.objects.create(address="acc3")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1")
        models.Dao.objects.create(id="dao2", name="dao2 name", owner_id="acc2")
        models.Dao.objects.create(id="dao3", name="dao3 name", owner_id="acc3")
        metadata_1 = {"a": 1}
        file_1 = BytesIO(json.dumps(metadata_1).encode())
        metadata_hash_1 = file_handler._hash(file_1.getvalue())
        metadata_2 = {"a": 2}
        file_2 = BytesIO(json.dumps(metadata_2).encode())
        metadata_hash_2 = file_handler._hash(file_2.getvalue())

        def download_metadata(url, **_):
            if url == "url1":
                raise Exception("roar")
            return metadata_2

        download_metadata_mock.side_effect = download_metadata
        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            extrinsic_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "set_metadata": [
                        {"dao_id": "dao1", "hash": metadata_hash_1, "meta": "url1", "not": "interesting"},
                        {"dao_id": "dao2", "hash": metadata_hash_2, "meta": "url2", "not": "interesting"},
                        # should not be updated cause of missing corresponding event
                        {"dao_id": "dao3", "hash": "hash33", "meta": "url3", "not": "interesting"},
                    ],
                },
            },
            event_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "DaoMetadataSet": [
                        {"dao_id": "dao1", "not": "interesting"},
                        {"dao_id": "dao2", "not": "interesting"},
                    ],
                },
            },
        )
        expected_daos = [
            models.Dao(
                id="dao1",
                name="dao1 name",
                owner_id="acc1",
                metadata_url="url1",
                metadata_hash=metadata_hash_1,
            ),
            models.Dao(
                id="dao2",
                name="dao2 name",
                owner_id="acc2",
                metadata_url="url2",
                metadata_hash=metadata_hash_2,
                metadata=metadata_2,
            ),
            models.Dao(id="dao3", name="dao3 name", owner_id="acc3"),
        ]

        with self.assertNumQueries(2):
            substrate_event_handler._set_dao_metadata(block)

        download_metadata_mock.assert_has_calls(
            [
                call(url="url1", metadata_hash=metadata_hash_1),
                call(url="url2", metadata_hash=metadata_hash_2),
            ],
            any_order=True,
        )
        logger_mock.exception.assert_called_once_with("Unexpected error while fetching DAO metadata from provided url.")
        self.assertModelsEqual(models.Dao.objects.order_by("id"), expected_daos)

    @patch("core.file_handling.file_handler.urlopen")
    def test__set_dao_metadata_nothing_to_update(self, urlopen_mock):
        models.Account.objects.create(address="acc1")
        models.Account.objects.create(address="acc2")
        models.Account.objects.create(address="acc3")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1", metadata_hash="hash1")
        models.Dao.objects.create(id="dao2", name="dao2 name", owner_id="acc2", metadata_hash="hash2")
        models.Dao.objects.create(id="dao3", name="dao3 name", owner_id="acc3")

        block = models.Block.objects.create(
            hash="hash 0",
            number=0,
            extrinsic_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "set_metadata": [
                        {"dao_id": "dao1", "hash": "hash1", "meta": "url1", "not": "interesting"},
                        {"dao_id": "dao2", "hash": "hash2", "meta": "url2", "not": "interesting"},
                        # should not be updated cause of missing corresponding event
                        {"dao_id": "dao3", "hash": "hash33", "meta": "url3", "not": "interesting"},
                    ],
                },
            },
            event_data={
                "not": "interesting",
                "DaoCore": {
                    "not": "interesting",
                    "DaoMetadataSet": [
                        {"dao_id": "dao1", "not": "interesting"},
                        {"dao_id": "dao2", "not": "interesting"},
                    ],
                },
            },
        )
        expected_daos = [
            models.Dao(id="dao1", name="dao1 name", owner_id="acc1", metadata_hash="hash1"),
            models.Dao(id="dao2", name="dao2 name", owner_id="acc2", metadata_hash="hash2"),
            models.Dao(id="dao3", name="dao3 name", owner_id="acc3"),
        ]

        with self.assertNumQueries(1):
            substrate_event_handler._set_dao_metadata(block)

        urlopen_mock.assert_not_called()
        self.assertModelsEqual(models.Dao.objects.order_by("id"), expected_daos)

    @patch("core.event_handler.SubstrateEventHandler._create_accounts")
    @patch("core.event_handler.SubstrateEventHandler._create_daos")
    @patch("core.event_handler.SubstrateEventHandler._delete_daos")
    @patch("core.event_handler.SubstrateEventHandler._create_assets")
    @patch("core.event_handler.SubstrateEventHandler._transfer_assets")
    @patch("core.event_handler.SubstrateEventHandler._set_dao_metadata")
    def test_execute_actions(self, *mocks):
        event_handler = SubstrateEventHandler()
        block = models.Block.objects.create(hash="hash 0", number=0)

        with self.assertNumQueries(3):
            event_handler.execute_actions(block)

        block.refresh_from_db()
        self.assertTrue(block.executed)
        for mock in mocks:
            mock.assert_called_once_with(block=block)
