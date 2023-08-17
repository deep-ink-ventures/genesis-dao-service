from core import models
from core.tests.testcases import IntegrationTestCase


class ModelTest(IntegrationTestCase):
    def test_asset_holding_str(self):
        self.assertEqual(str(models.AssetHolding(asset_id=1, owner_id="acc1", balance=3)), "1 | acc1 | 3")

    def test_block_str(self):
        self.assertEqual(str(models.Block(number=1)), "1")

    def test_MultiSig_str(self):
        self.assertEqual(str(models.MultiSig(address="addr1")), "addr1")

    def test_MultiSig_bulk_create(self):
        models.MultiSig.objects.bulk_create(
            [
                models.MultiSig(account_ptr_id="addr1", threshold=3),
                models.MultiSig(account_ptr_id="addr2", threshold=3),
            ],
        )

        self.assertModelsEqual(
            models.MultiSig.objects.order_by("address"),
            [
                models.MultiSig(account_ptr_id="addr1", address="addr1", threshold=3),
                models.MultiSig(account_ptr_id="addr2", address="addr2", threshold=3),
            ],
        )

    def test_MultiSig_bulk_no_objs(self):
        self.assertListEqual(models.MultiSig.objects.bulk_create([]), [])
        self.assertListEqual(list(models.MultiSig.objects.all()), [])

    def test_MultiSig_bulk_create_negative_batch_size(self):
        with self.assertRaisesMessage(ValueError, "Batch size must be a positive integer."):
            models.MultiSig.objects.bulk_create(
                [
                    models.MultiSig(account_ptr_id="addr1", threshold=2),
                    models.MultiSig(account_ptr_id="addr2", threshold=2),
                ],
                batch_size=-1,
            )

        self.assertListEqual(list(models.MultiSig.objects.all()), [])

    def test_MultiSig_bulk_create_update_fields(self):
        models.MultiSig.objects.create(address="addr1", threshold=2),
        models.MultiSig.objects.create(address="addr2", threshold=2),
        models.MultiSig.objects.bulk_create(
            [
                models.MultiSig(account_ptr_id="addr1", threshold=3),
                models.MultiSig(account_ptr_id="addr2", threshold=3),
            ],
            update_fields=("threshold",),
            unique_fields=("account_ptr_id",),
            update_conflicts=True,
        )

        self.assertModelsEqual(
            models.MultiSig.objects.order_by("address"),
            [
                models.MultiSig(account_ptr_id="addr1", address="addr1", threshold=3),
                models.MultiSig(account_ptr_id="addr2", address="addr2", threshold=3),
            ],
        )

    def test_transaction_str(self):
        self.assertEqual(str(models.Transaction(call_hash="h", multisig=models.MultiSig(address="a"))), "h | a | None")

    def test_transaction_last_approver(self):
        self.assertEqual(models.Transaction(approvers=["a"]).last_approver, "a")
        self.assertIsNone(models.Transaction(approvers=[]).last_approver)
