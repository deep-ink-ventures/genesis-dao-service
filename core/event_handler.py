import collections
import logging
from functools import reduce

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core import models, tasks

logger = logging.getLogger("alerts")


class ParseBlockException(Exception):
    pass


class SubstrateEventHandler:
    block_actions = None

    def __init__(self):
        self.block_actions = (
            self._create_accounts,
            self._create_daos,
            self._transfer_dao_ownerships,
            self._delete_daos,
            self._create_assets,
            self._transfer_assets,
            self._set_dao_metadata,
            self._dao_set_governances,
            self._create_proposals,
            self._set_proposal_metadata,
            self._register_votes,
            self._finalize_proposals,
            self._fault_proposals,
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
        if accs := [
            models.Account(address=dao_event["account"])
            for dao_event in block.event_data.get("System", {}).get("NewAccount", [])
        ]:
            models.Account.objects.bulk_create(accs, ignore_conflicts=True)

    @staticmethod
    def _create_daos(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        creates Daos based on the Block's extrinsics and events
        """
        daos = []
        for dao_extrinsic in block.extrinsic_data.get("DaoCore", {}).get("create_dao", []):
            for dao_event in block.event_data.get("DaoCore", {}).get("DaoCreated", []):
                if dao_extrinsic["dao_id"] == dao_event["dao_id"]:
                    daos.append(
                        models.Dao(
                            id=dao_extrinsic["dao_id"],
                            name=dao_extrinsic["dao_name"],
                            creator_id=dao_event["owner"],
                            owner_id=dao_event["owner"],
                        )
                    )
                    break
        if daos:
            models.Dao.objects.bulk_create(daos)

    @staticmethod
    def _transfer_dao_ownerships(block: models.Block):
        """
        Args:
            block: Block to change Dao owners from

        Returns:
            None

        transfers ownerships of a Daos to new Accounts based on the Block's events
        """
        dao_id_to_new_owner_id = {}  # {dao_id: new_owner_id}
        for dao_event in block.event_data.get("DaoCore", {}).get("DaoOwnerChanged", []):
            dao_id_to_new_owner_id[dao_event["dao_id"]] = dao_event["new_owner"]

        for dao in (daos := list(models.Dao.objects.filter(id__in=dao_id_to_new_owner_id.keys()))):
            dao.owner_id = dao_id_to_new_owner_id[dao.id]
            dao.setup_complete = True

        if daos:
            # try creating Accounts, needed for multi signature wallets
            models.Account.objects.bulk_create(
                [models.Account(address=address) for address in dao_id_to_new_owner_id.values()],
                ignore_conflicts=True,
            )
            models.Dao.objects.bulk_update(daos, ["owner_id", "setup_complete"])

    @staticmethod
    def _delete_daos(block: models.Block):
        """
        Args:
            block: Block to create Accounts from

        Returns:
            None

        deletes Daos based on the Block's extrinsics and events
        """
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
        rephrase: transfers ownership of an amount of tokens (models.AssetHolding) from one Account to another
        """
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

        updates Daos' metadata_url and metadata_hash based on the Block's extrinsics and events
        """
        dao_metadata = {}  # {dao_id: {"metadata_url": metadata_url, "metadata_hash": metadata_hash}}
        for dao_event in block.event_data.get("DaoCore", {}).get("DaoMetadataSet", []):
            for dao_extrinsic in block.extrinsic_data.get("DaoCore", {}).get("set_metadata", []):
                if (dao_id := dao_event["dao_id"]) == dao_extrinsic["dao_id"]:
                    dao_metadata[dao_id] = {
                        "metadata_url": dao_extrinsic["meta"],
                        "metadata_hash": dao_extrinsic["hash"],
                    }
        if dao_metadata:
            tasks.update_dao_metadata.delay(dao_metadata=dao_metadata)

    @staticmethod
    def _dao_set_governances(block: models.Block):
        """
        Args:
            block: Block to set DAO's governance model from

        Returns:
            None

        updates Daos' governance based on the Block's extrinsics and events
        """
        governances = []
        dao_ids = set()
        for governance_event in block.event_data.get("Votes", {}).get("SetGovernanceMajorityVote", []):
            dao_ids.add(governance_event["dao_id"])
            governances.append(
                models.Governance(
                    dao_id=governance_event["dao_id"],
                    proposal_duration=governance_event["proposal_duration"],
                    proposal_token_deposit=governance_event["proposal_token_deposit"],
                    minimum_majority=governance_event["minimum_majority_per_1024"],
                    type=models.GovernanceType.MAJORITY_VOTE,
                )
            )

        if governances:
            models.Governance.objects.filter(dao_id__in=dao_ids).delete()
            models.Governance.objects.bulk_create(governances)

    @staticmethod
    def _create_proposals(block: models.Block):
        """
        Args:
            block: Block to create Proposals from

        Returns:
            None

        create Proposals based on the Block's extrinsics and events
        """
        proposals = []
        dao_ids = set()

        for proposal_created_event in block.event_data.get("Votes", {}).get("ProposalCreated", []):
            dao_id = proposal_created_event["dao_id"]
            dao_ids.add(dao_id)
            proposals.append(
                models.Proposal(
                    id=proposal_created_event["proposal_id"],
                    dao_id=dao_id,
                    creator_id=proposal_created_event["creator"],
                    birth_block_number=block.number,
                )
            )
        if proposals:
            dao_id_to_holding_data = collections.defaultdict(list)
            for dao_id, owner_id, balance in models.AssetHolding.objects.filter(asset__dao__id__in=dao_ids).values_list(
                "asset__dao_id", "owner_id", "balance"
            ):
                dao_id_to_holding_data[dao_id].append((owner_id, balance))

            dao_id_to_proposal_duration = {
                dao_id: proposal_duration
                for dao_id, proposal_duration in models.Governance.objects.filter(dao_id__in=dao_ids).values_list(
                    "dao_id", "proposal_duration"
                )
            }
            # set end dates for proposals
            # current time + proposal duration in block * block creation interval (6s)
            for proposal in proposals:
                proposal.ends_at = timezone.now() + timezone.timedelta(
                    seconds=dao_id_to_proposal_duration[proposal.dao_id] * settings.BLOCK_CREATION_INTERVAL
                )

            models.Proposal.objects.bulk_create(proposals)
            # for all proposals: create a Vote placeholder for each Account holding tokens (AssetHoldings) of the
            # corresponding Dao to keep track of the Account's voting power at the time of Proposal creation.
            models.Vote.objects.bulk_create(
                [
                    models.Vote(proposal_id=proposal.id, voter_id=voter_id, voting_power=balance)
                    for proposal in proposals
                    for voter_id, balance in dao_id_to_holding_data[proposal.dao_id]
                ]
            )

    @staticmethod
    def _set_proposal_metadata(block: models.Block):
        """
        Args:
            block: Block to set Proposal's metadata from

        Returns:
            None

        set Proposals' metadata based on the Block's extrinsics and events
        """
        proposal_data = {}  # proposal_id: (metadata_hash, metadata_url)
        for proposal_created_event in block.event_data.get("Votes", {}).get("ProposalMetadataSet", []):
            for proposal_created_extrinsic in block.extrinsic_data.get("Votes", {}).get("set_metadata", []):
                if (proposal_id := proposal_created_extrinsic["proposal_id"]) == proposal_created_event["proposal_id"]:
                    proposal_data[str(proposal_id)] = (
                        proposal_created_extrinsic["hash"],
                        proposal_created_extrinsic["meta"],
                    )
        if proposal_data:
            for proposal in (proposals := models.Proposal.objects.filter(id__in=proposal_data.keys())):
                proposal.metadata_hash, proposal.metadata_url = proposal_data[proposal.id]
            models.Proposal.objects.bulk_update(proposals, fields=["metadata_hash", "metadata_url"])
            tasks.update_proposal_metadata.delay(proposal_ids=list(proposal_data.keys()))

    @staticmethod
    def _register_votes(block: models.Block):
        """
        Args:
            block: Block to register votes from

        Returns:
            None

        registers Votes based on the Block's events
        """
        proposal_ids_to_voting_data = collections.defaultdict(dict)  # {proposal_id: {voter_id: in_favor}}
        for voting_event in block.event_data.get("Votes", {}).get("VoteCast", []):
            proposal_ids_to_voting_data[str(voting_event["proposal_id"])][voting_event["voter"]] = voting_event[
                "in_favor"
            ]
        if proposal_ids_to_voting_data:
            for vote in (
                votes_to_update := models.Vote.objects.filter(
                    # WHERE (
                    #     (vote.proposal_id = 1 AND vote.voter_id IN (1, 2))
                    #     OR (vote.proposal_id = 2 AND vote.voter_id IN (3, 4))
                    #     OR ...
                    # )
                    reduce(
                        Q.__or__,
                        [
                            Q(proposal_id=proposal_id, voter_id__in=voting_data.keys())
                            for proposal_id, voting_data in proposal_ids_to_voting_data.items()
                        ],
                    )
                )
            ):
                vote.in_favor = proposal_ids_to_voting_data[vote.proposal_id][vote.voter_id]
            models.Vote.objects.bulk_update(votes_to_update, ["in_favor"])

    @staticmethod
    def _finalize_proposals(block: models.Block):
        """
        Args:
            block: Block to finalize proposals from

        Returns:
            None

        finalizes Proposals based on the Block's events
        """
        votes_events = block.event_data.get("Votes", {})
        if accepted_proposal_ids := set(prop["proposal_id"] for prop in votes_events.get("ProposalAccepted", [])):
            models.Proposal.objects.filter(id__in=accepted_proposal_ids).update(status=models.ProposalStatus.PENDING)
        if rejected_proposal_ids := set(prop["proposal_id"] for prop in votes_events.get("ProposalRejected", [])):
            models.Proposal.objects.filter(id__in=rejected_proposal_ids).update(status=models.ProposalStatus.REJECTED)

    @staticmethod
    def _fault_proposals(block: models.Block):
        """
        Args:
            block: Block to fault proposals from

        Returns:
            None

        faults Proposals based on the Block's events
        """
        if faulted_proposals := {
            fault_event["proposal_id"]: fault_event["reason"]
            for fault_event in block.event_data.get("Votes", {}).get("ProposalFaulted", [])
        }:
            for proposal in (proposals := models.Proposal.objects.filter(id__in=faulted_proposals.keys())):
                proposal.fault = faulted_proposals[proposal.id]
                proposal.status = models.ProposalStatus.FAULTED
            models.Proposal.objects.bulk_update(proposals, ("fault", "status"))

    @transaction.atomic
    def execute_actions(self, block: models.Block):
        """
        Args:
             block: Block to execute

         Returns:
             None

         alters db's blockchain representation based on the Block's extrinsics and events
        """
        for block_action in self.block_actions:
            try:
                block_action(block=block)
            except IntegrityError:
                msg = f"Database error while parsing Block #{block.number}."
                logger.exception(msg)
                raise ParseBlockException(msg)
            except Exception:  # noqa E722
                msg = f"Unexpected error while parsing Block #{block.number}."
                logger.exception(msg)
                raise ParseBlockException(msg)

        block.executed = True
        block.save(update_fields=["executed"])
        cache.set(key="current_block", value=(block.number, block.hash))


substrate_event_handler = SubstrateEventHandler()
