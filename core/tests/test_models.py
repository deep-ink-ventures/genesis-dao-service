import random

from core import models
from core.tests.testcases import IntegrationTestCase


class BlockTest(IntegrationTestCase):
    def test_execute_action_NewAccount(self):
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

        with self.assertNumQueries(4):
            block.execute_actions()

        self.assertModelsEqual(models.Account.objects.all(), expected_accs)

    def test_execute_actions_dao_created(self):
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

        with self.assertNumQueries(4):
            block.execute_actions()

        self.assertModelsEqual(models.Dao.objects.all(), expected_daos)

    def test_execute_actions_DaoDestroyed(self):
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

        with self.assertNumQueries(6):
            block.execute_actions()

        self.assertModelsEqual(models.Dao.objects.all(), expected_daos)

    def test_execute_actions_Assets_Issued(self):
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

        with self.assertNumQueries(5):
            block.execute_actions()

        self.assertModelsEqual(models.Asset.objects.all(), expected_assets)
        self.assertModelsEqual(
            models.AssetHolding.objects.all(), expected_asset_holdings, ignore_fields=("id", "created_at", "updated_at")
        )

    def test_execute_actions_Assets_Transferred(self):
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

        with self.assertNumQueries(6):
            block.execute_actions()

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
