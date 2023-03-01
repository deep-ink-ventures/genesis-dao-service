import collections
from functools import reduce

from django.db import transaction
from django.db.models import Q

from core import models


class SubstrateEventHandler:
    block_actions = None

    def __init__(self):
        self.block_actions = (
            self._create_accounts,
            self._create_daos,
            self._delete_daos,
            self._create_assets,
            self._transfer_assets,
            self._set_dao_metadata,
        )

    @staticmethod
    def _create_accounts(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        creates Accounts based on the Block's extrinsics and events
        """
        # event: System.NewAccount
        if accs := [
            models.Account(address=dao_event["account"])
            for dao_event in block.event_data.get("System", {}).get("NewAccount", [])
        ]:
            models.Account.objects.bulk_create(accs)

    @staticmethod
    def _create_daos(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        creates Daos based on the Block's extrinsics and events
        """
        # event: DaoCore.DaoCreated
        daos = []
        for dao_extrinsic in block.extrinsic_data.get("DaoCore", {}).get("create_dao", []):
            for dao_event in block.event_data.get("DaoCore", {}).get("DaoCreated", []):
                if dao_extrinsic["dao_id"] == dao_event["dao_id"]:
                    daos.append(
                        models.Dao(
                            id=dao_extrinsic["dao_id"],
                            name=dao_extrinsic["dao_name"],
                            owner_id=dao_event["owner"],
                        )
                    )
                    break
        if daos:
            models.Dao.objects.bulk_create(daos)

    @staticmethod
    def _delete_daos(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        deletes Daos based on the Block's extrinsics and events
        """
        # DaoCore.DaoDestroyed
        if dao_ids := [
            dao_event["dao_id"] for dao_event in block.event_data.get("DaoCore", {}).get("DaoDestroyed", [])
        ]:
            models.Dao.objects.filter(id__in=dao_ids).delete()

    @staticmethod
    def _create_assets(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        creates Assets based on the Block's extrinsics and events
        """

        # Assets.Issued
        # create Assets and assign to Daos
        assets = []
        asset_holdings = []
        for asset_issued_event in block.event_data.get("Assets", {}).get("Issued", []):
            for asset_metadata in block.event_data.get("Assets", {}).get("MetadataSet", []):
                if asset_issued_event["asset_id"] == asset_metadata["asset_id"]:
                    asset_id, owner_id, balance = (
                        asset_metadata["asset_id"],
                        asset_issued_event["owner"],
                        asset_issued_event["total_supply"],
                    )
                    assets.append(
                        models.Asset(
                            id=asset_id,
                            dao_id=asset_metadata["symbol"],
                            owner_id=owner_id,
                            total_supply=balance,
                        )
                    )
                    asset_holdings.append(
                        models.AssetHolding(
                            asset_id=asset_id,
                            owner_id=owner_id,
                            balance=balance,
                        )
                    )
        if assets:
            for asset_holding_obj, asset in zip(asset_holdings, models.Asset.objects.bulk_create(assets)):
                asset_holding_obj.asset_id = asset.id
            models.AssetHolding.objects.bulk_create(asset_holdings)

    @staticmethod
    def _transfer_assets(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        transfers Assets based on the Block's extrinsics and events
        rephrase: transfers ownership of an amount of tokens (models.AssetHolding) from one models.Account to another
        """
        # Assets.Transferred
        asset_holding_data = []  # [(asset_id, amount, from_acc, to_acc), ...]
        asset_ids_to_owner_ids = collections.defaultdict(set)  # {1 (asset_id): {1, 2, 3} (owner_ids)...}
        for asset_issued_event in block.event_data.get("Assets", {}).get("Transferred", []):
            asset_id, amount = asset_issued_event["asset_id"], asset_issued_event["amount"]
            from_acc, to_acc = asset_issued_event["from"], asset_issued_event["to"]
            asset_holding_data.append((asset_id, amount, from_acc, to_acc))
            asset_ids_to_owner_ids[asset_id].add(from_acc)
            asset_ids_to_owner_ids[asset_id].add(to_acc)

        if asset_holding_data:
            existing_holdings = collections.defaultdict(dict)
            for asset_holding in models.AssetHolding.objects.filter(
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
                # subtract transferred amount from existing models.AssetHolding
                existing_holdings[asset_id][from_acc].balance -= amount

                #  add transferred amount if models.AssetHolding already exists
                if to_acc_holding := asset_holdings_to_create.get((asset_id, to_acc)):
                    to_acc_holding.balance += amount
                elif to_acc_holding := existing_holdings.get(asset_id, {}).get(to_acc):
                    to_acc_holding.balance += amount
                # otherwise create a new models.AssetHolding with balance = transferred amount
                else:
                    asset_holdings_to_create[(asset_id, to_acc)] = models.AssetHolding(
                        owner_id=to_acc, asset_id=asset_id, balance=amount
                    )
            models.AssetHolding.objects.bulk_update(
                [holding for acc_to_holding in existing_holdings.values() for holding in acc_to_holding.values()],
                ["balance"],
            )
            models.AssetHolding.objects.bulk_create(asset_holdings_to_create.values())

    @staticmethod
    def _set_dao_metadata(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        updates Daos metadata_url and metadata_hash based on the Block's extrinsics and events
        """
        # DaoMetadataSet
        dao_metadata = set()
        for dao_event in block.event_data.get("DaoCore", {}).get("DaoMetadataSet", []):
            for dao_extrinsic in block.extrinsic_data.get("DaoCore", {}).get("set_metadata", []):
                if (dao_id := dao_event["dao_id"]) == dao_extrinsic["dao_id"]:
                    dao_metadata.add((dao_id, dao_extrinsic["meta"], dao_extrinsic["hash"]))
        if dao_metadata:
            models.Dao.objects.bulk_update(
                [
                    models.Dao(id=dao_id, metadata_url=metadata_url, metadata_hash=metadata_hash)
                    for dao_id, metadata_url, metadata_hash in dao_metadata
                ],
                fields=["metadata_url", "metadata_hash"],
            )

    @transaction.atomic
    def execute_actions(self, block: models.Block):
        """
        alters db's blockchain representation based on the Block's extrinsics and events
        """
        for block_action in self.block_actions:
            block_action(block=block)

        block.executed = True
        block.save(update_fields=["executed"])


substrate_event_handler = SubstrateEventHandler()
