from datetime import datetime, timezone

from django.db import IntegrityError
from django.utils.timezone import now

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
        self.assertEqual(
            str(models.MultiSigTransaction(call_hash="h", multisig=models.MultiSig(address="a"))), "h | a | None"
        )

    def test_transaction_last_approver(self):
        self.assertEqual(models.MultiSigTransaction(approvers=["a"]).last_approver, "a")
        self.assertIsNone(models.MultiSigTransaction(approvers=[]).last_approver)

    def test_multisig_transaction_idx_unique_with_optional(self):
        multisig1 = models.MultiSig.objects.create(address="addr1")
        timestamp = datetime(2000, 1, 1, 1, 1, 1, 1, tzinfo=timezone.utc)
        models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash1")
        models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash2")
        models.MultiSigTransaction.objects.create(
            multisig=models.MultiSig.objects.create(address="addr2"), call_hash="hash1"
        )
        models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash1", executed_at=now())
        models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash1", executed_at=timestamp)
        with self.assertRaisesMessage(
            IntegrityError,
            'duplicate key value violates unique constraint "unique_with_optional"\n'
            "DETAIL:  Key (call_hash, multisig_id, executed_at)=(hash1, addr1, 2000-01-01 01:01:01.000001+00)"
            " already exists.\n",
        ):
            models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash1", executed_at=timestamp)

    def test_multisig_transaction_idx_unique_without_optional(self):
        multisig1 = models.MultiSig.objects.create(address="addr1")
        models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash1")
        models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash2")
        models.MultiSigTransaction.objects.create(
            multisig=models.MultiSig.objects.create(address="addr2"), call_hash="hash1"
        )
        models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash1", executed_at=now())
        with self.assertRaisesMessage(
            IntegrityError,
            'duplicate key value violates unique constraint "unique_without_optional"\n'
            "DETAIL:  Key (call_hash, multisig_id)=(hash1, addr1) already exists.\n",
        ):
            models.MultiSigTransaction.objects.create(multisig=multisig1, call_hash="hash1")
