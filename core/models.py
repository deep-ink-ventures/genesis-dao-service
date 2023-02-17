import collections
from functools import reduce

from django.db import models, transaction
from django.db.models import Q


class TimestampableMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class Account(TimestampableMixin):
    address = models.CharField(primary_key=True, max_length=128, unique=True, editable=False)


class Dao(TimestampableMixin):
    id = models.CharField(max_length=128, primary_key=True)
    name = models.CharField(max_length=128, null=True)
    owner = models.ForeignKey(Account, related_name="daos", on_delete=models.CASCADE)


class Asset(TimestampableMixin):
    id = models.BigIntegerField(primary_key=True)
    total_supply = models.BigIntegerField()
    dao = models.OneToOneField(Dao, related_name="asset", on_delete=models.CASCADE)
    owner = models.ForeignKey(Account, related_name="assets", on_delete=models.CASCADE)


class AssetHolding(TimestampableMixin):
    asset = models.ForeignKey(Asset, related_name="holdings", on_delete=models.CASCADE)
    owner = models.ForeignKey(Account, related_name="holdings", on_delete=models.CASCADE)
    balance = models.IntegerField()

    class Meta:
        db_table = "core_asset_holding"
        unique_together = ("asset", "owner")

    def __str__(self):
        return f"{self.asset_id} | {self.owner_id} | {self.balance}"


class Block(TimestampableMixin):
    hash = models.CharField(primary_key=True, max_length=128, unique=True, editable=False)
    number = models.BigIntegerField(unique=True, editable=False)
    parent_hash = models.CharField(max_length=128, unique=True, editable=False, null=True)
    extrinsic_data = models.JSONField(default=dict)
    event_data = models.JSONField(default=dict)
    executed = models.BooleanField(default=False, db_index=True)

    def __str__(self):
        return f"{self.number}"

    @transaction.atomic
    def execute_actions(self):
        """
        alters db's blockchain representation based on the Block's extrinsics and events
        """

        # System.NewAccount
        if accs := [
            Account(address=dao_event["account"])
            for dao_event in self.event_data.get("System", {}).get("NewAccount", [])
        ]:
            Account.objects.bulk_create(accs)

        # DaoCore.DaoCreated
        daos = []
        for dao_extrinsic in self.extrinsic_data.get("DaoCore", {}).get("create_dao", []):
            for dao_event in self.event_data.get("DaoCore", {}).get("DaoCreated", []):
                if dao_extrinsic["dao_id"] == dao_event["dao_id"]:
                    daos.append(
                        Dao(
                            id=dao_extrinsic["dao_id"],
                            name=dao_extrinsic["dao_name"],
                            owner_id=dao_event["owner"],
                        )
                    )
                    break
        if daos:
            Dao.objects.bulk_create(daos)

        # DaoCore.DaoDestroyed
        if dao_ids := [dao_event["dao_id"] for dao_event in self.event_data.get("DaoCore", {}).get("DaoDestroyed", [])]:
            Dao.objects.filter(id__in=dao_ids).delete()

        # Assets.Issued
        # create Assets and assign to Daos
        assets = []
        asset_holdings = []
        for asset_issued_event in self.event_data.get("Assets", {}).get("Issued", []):
            for asset_metadata in self.event_data.get("Assets", {}).get("MetadataSet", []):
                if asset_issued_event["asset_id"] == asset_metadata["asset_id"]:
                    asset_id, owner_id, balance = (
                        asset_metadata["asset_id"],
                        asset_issued_event["owner"],
                        asset_issued_event["total_supply"],
                    )
                    assets.append(
                        Asset(
                            id=asset_id,
                            dao_id=asset_metadata["symbol"],
                            owner_id=owner_id,
                            total_supply=balance,
                        )
                    )
                    asset_holdings.append(
                        AssetHolding(
                            asset_id=asset_id,
                            owner_id=owner_id,
                            balance=balance,
                        )
                    )
        if assets:
            for asset_holding_obj, asset in zip(asset_holdings, Asset.objects.bulk_create(assets)):
                asset_holding_obj.asset_id = asset.id
            AssetHolding.objects.bulk_create(asset_holdings)

        # Assets.Transferred
        # transfers ownership of an amount of tokens (AssetHolding) from one Account to another
        asset_holding_data = []  # [(asset_id, amount, from_acc, to_acc), ...]
        asset_ids_to_owner_ids = collections.defaultdict(set)  # {1 (asset_id): {1, 2, 3} (owner_ids)...}
        for asset_issued_event in self.event_data.get("Assets", {}).get("Transferred", []):
            asset_id, amount = asset_issued_event["asset_id"], asset_issued_event["amount"]
            from_acc, to_acc = asset_issued_event["from"], asset_issued_event["to"]
            asset_holding_data.append((asset_id, amount, from_acc, to_acc))
            asset_ids_to_owner_ids[asset_id].add(from_acc)
            asset_ids_to_owner_ids[asset_id].add(to_acc)

        if asset_holding_data:
            existing_holdings = collections.defaultdict(dict)
            for asset_holding in AssetHolding.objects.filter(
                # WHERE (
                #     (asset_holding.asset_id = 1 AND asset_holding.owner_id IN (1, 2))
                #     OR (asset_holding.asset_id = 2 AND asset_holding.owner_id IN (3, 4))
                #     OR ...
                # )
                reduce(
                    Q.__or__,
                    [
                        Q(asset_id=asset_id, owner_id__in=owner_ids)
                        for asset_id, owner_ids in asset_ids_to_owner_ids.items()
                    ],
                )
            ):
                existing_holdings[asset_holding.asset_id][asset_holding.owner_id] = asset_holding

            asset_holdings_to_create = {}
            for asset_id, amount, from_acc, to_acc in asset_holding_data:
                # subtract transferred amount from existing AssetHolding
                existing_holdings[asset_id][from_acc].balance -= amount

                #  add transferred amount if AssetHolding already exists
                if to_acc_holding := asset_holdings_to_create.get((asset_id, to_acc)):
                    to_acc_holding.balance += amount
                elif to_acc_holding := existing_holdings.get(asset_id, {}).get(to_acc):
                    to_acc_holding.balance += amount
                # otherwise create a new AssetHolding with balance = transferred amount
                else:
                    asset_holdings_to_create[(asset_id, to_acc)] = AssetHolding(
                        owner_id=to_acc, asset_id=asset_id, balance=amount
                    )
            AssetHolding.objects.bulk_update(
                [holding for acc_to_holding in existing_holdings.values() for holding in acc_to_holding.values()],
                ["balance"],
            )
            AssetHolding.objects.bulk_create(asset_holdings_to_create.values())

        self.executed = True
        self.save(update_fields=["executed"])
