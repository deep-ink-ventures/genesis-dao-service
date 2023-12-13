import logging
from collections import defaultdict
from functools import partial, reduce
from typing import DefaultDict

from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Q
from django.db.transaction import atomic
from django.utils import timezone

from core import models, tasks
from core.models import Dao

logger = logging.getLogger("alerts")


class ParseBlockException(Exception):
    pass


class SubstrateEventHandler:
    block_actions: tuple = None

    def __init__(self):
        self.block_actions = (
            self._instantiate_contracts,
            self._create_accounts,
            self._create_daos,
            self._transfer_dao_ownerships,
            self._delete_daos,
            self._create_assets,
            self._transfer_assets,
            self._delegate_assets,
            self._revoke_asset_delegations,
            self._set_dao_metadata,
            self._dao_set_governances,
            self._create_proposals,
            self._set_proposal_metadata,
            self._register_votes,
            self._finalize_proposals,
            self._fault_proposals,
            self._handle_new_transactions,
            self._approve_transactions,
            self._execute_transactions,
            self._cancel_transactions,
        )

    @staticmethod
    def _instantiate_contracts(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events
        """
        if ctr_event := block.event_data.get("Contracts", {}):
            for event in ctr_event.get("ContractEmitted", []):
                print(event["name"])
                print(event['args'])

    @staticmethod
    def _create_accounts(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

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
            block: Block containing extrinsics and events

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
            block: Block containing extrinsics and events

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
            # update MultiSig accs
            if multisigs := models.MultiSig.objects.filter(address__in=dao_id_to_new_owner_id.values()):
                owner_id_to_dao_id = {v: k for k, v in dao_id_to_new_owner_id.items()}
                for multisig in multisigs:
                    multisig.dao_id = owner_id_to_dao_id[multisig.address]
                models.MultiSig.objects.bulk_update(multisigs, ["dao_id"])

    @staticmethod
    def _delete_daos(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

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
            block: Block containing extrinsics and events

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
            block: Block containing extrinsics and events

        transfers Assets based on the Block's extrinsics and events
        rephrase: transfers ownership of an amount of tokens (models.AssetHolding) from one Account to another
        """
        asset_holding_data = []  # [(asset_id, amount, from_acc, to_acc), ...]
        asset_ids_to_owner_ids = defaultdict(set)  # {1 (asset_id): {1, 2, 3} (owner_ids)...}
        for asset_issued_event in block.event_data.get("Assets", {}).get("Transferred", []):
            asset_id, amount = asset_issued_event["asset_id"], asset_issued_event["amount"]
            from_acc, to_acc = asset_issued_event["from"], asset_issued_event["to"]
            asset_holding_data.append((asset_id, amount, from_acc, to_acc))
            asset_ids_to_owner_ids[asset_id].add(from_acc)
            asset_ids_to_owner_ids[asset_id].add(to_acc)

        if asset_holding_data:
            existing_holdings = defaultdict(dict)
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
    def _delegate_assets(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        delegates votes to another account based on the Block's extrinsics and events
        """
        if data := {
            (event["asset_id"], event["from"]): event["to"]
            for event in block.event_data.get("Assets", {}).get("Delegated", [])
        }:
            for asset_holding in (
                asset_holdings := list(
                    models.AssetHolding.objects.filter(
                        # WHERE (
                        #     (asset_holding.asset_id = 1 AND asset_holding.owner_id = 2)
                        #     OR (asset_holding.asset_id =3 AND asset_holding.owner_id = 4)
                        #     OR ...
                        # )
                        reduce(
                            Q.__or__,
                            [Q(asset_id=asset_id, owner_id=owner_id) for asset_id, owner_id in data.keys()],
                        )
                    )
                )
            ):
                asset_holding.delegated_to_id = data[(asset_holding.asset_id, asset_holding.owner_id)]

            models.AssetHolding.objects.bulk_update(asset_holdings, ["delegated_to_id"])

    @staticmethod
    def _revoke_asset_delegations(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        revokes delegation of votes to another account based on the Block's extrinsics and events
        """

        if data := [
            (event["asset_id"], event["delegated_by"], event["revoked_from"])
            for event in block.event_data.get("Assets", {}).get("DelegationRevoked", [])
        ]:
            models.AssetHolding.objects.filter(
                # WHERE (
                #     (holding.asset_id = 1 AND holding.owner_id = 2 AND holding.delegated_to_id = 3)
                #     OR (holding.asset_id = 4 AND holding.owner_id = 5 AND holding.delegated_to_id = 6)
                #     OR ...
                # )
                reduce(
                    Q.__or__,
                    [
                        Q(asset_id=asset_id, owner_id=owner_id, delegated_to_id=delegated_to_id)
                        for asset_id, owner_id, delegated_to_id in data
                    ],
                )
            ).update(delegated_to_id=None)

    @staticmethod
    def _set_dao_metadata(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

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
            block: Block containing extrinsics and events

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
            block: Block containing extrinsics and events

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
            dao_id_to_voter_id_to_balance: DefaultDict = defaultdict(partial(defaultdict, int))
            for dao_id, owner_id, delegated_to_id, balance in models.AssetHolding.objects.filter(
                asset__dao__id__in=dao_ids
            ).values_list("asset__dao_id", "owner_id", "delegated_to_id", "balance"):
                dao_id_to_voter_id_to_balance[dao_id][delegated_to_id or owner_id] += balance

            models.Proposal.objects.bulk_create(proposals)
            # for all proposals: create a Vote placeholder for each Account holding tokens (AssetHoldings) of the
            # corresponding Dao to keep track of the Account's voting power at the time of Proposal creation.
            models.Vote.objects.bulk_create(
                [
                    models.Vote(proposal_id=proposal.id, voter_id=voter_id, voting_power=balance)
                    for proposal in proposals
                    for voter_id, balance in dao_id_to_voter_id_to_balance[proposal.dao_id].items()
                ]
            )

    @staticmethod
    def _set_proposal_metadata(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        set Proposals' metadata based on the Block's extrinsics and events
        """
        proposal_data = {}  # proposal_id: (metadata_hash, metadata_url)
        for proposal_created_event in block.event_data.get("Votes", {}).get("ProposalMetadataSet", []):
            for proposal_created_extrinsic in block.extrinsic_data.get("Votes", {}).get("set_metadata", []):
                if (proposal_id := proposal_created_extrinsic["proposal_id"]) == proposal_created_event["proposal_id"]:
                    proposal_data[proposal_id] = (
                        proposal_created_extrinsic["hash"],
                        proposal_created_extrinsic["meta"],
                    )
        if proposal_data:
            for proposal in (proposals := models.Proposal.objects.filter(id__in=proposal_data.keys())):
                proposal.metadata_hash, proposal.metadata_url = proposal_data[proposal.id]
                proposal.setup_complete = True
            models.Proposal.objects.bulk_update(proposals, fields=["metadata_hash", "metadata_url", "setup_complete"])
            tasks.update_proposal_metadata.delay(proposal_ids=list(proposal_data.keys()))

    @staticmethod
    def _register_votes(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        registers Votes based on the Block's events
        """
        proposal_ids_to_voting_data = defaultdict(dict)  # {proposal_id: {voter_id: in_favor}}
        for voting_event in block.event_data.get("Votes", {}).get("VoteCast", []):
            proposal_ids_to_voting_data[voting_event["proposal_id"]][voting_event["voter"]] = voting_event["in_favor"]
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
            block: Block containing extrinsics and events

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
            block: Block containing extrinsics and events

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

    @staticmethod
    def _handle_new_transactions(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        creates / updates Transactions based on the Block's events
        """
        # update existing Transactions
        if transaction_data := {
            (multisig_event["call_hash"], multisig_event["multisig"]): multisig_event["approving"]
            for multisig_event in block.event_data.get("Multisig", {}).get("NewMultisig", [])
        }:
            for transaction in (
                transactions_to_update := models.MultiSigTransaction.objects.filter(
                    # WHERE (
                    #     (call_hash = 1 AND multisig_id 2)
                    #     OR (call_hash = 3 AND multisig_id 4)
                    #     OR ...
                    #     AND executed_at is null
                    # )
                    reduce(
                        Q.__or__,
                        [
                            Q(call_hash=call_hash, multisig_id=multisig)
                            for (call_hash, multisig) in transaction_data.keys()
                        ],
                    ),
                    executed_at__isnull=True,
                )
            ):
                transaction.approvers.append(transaction_data.pop((transaction.call_hash, transaction.multisig_id)))
            if transactions_to_update:
                models.MultiSigTransaction.objects.bulk_update(transactions_to_update, ("approvers",))

        # create new Transactions
        if transaction_data:
            multisigs_to_create = []
            transactions_to_create = []
            for (call_hash, multisig), approver in transaction_data.items():
                multisigs_to_create.append(models.MultiSig(account_ptr_id=multisig))
                transactions_to_create.append(
                    models.MultiSigTransaction(multisig_id=multisig, call_hash=call_hash, approvers=[approver])
                )
            models.MultiSig.objects.bulk_create(multisigs_to_create, ignore_conflicts=True)
            models.MultiSigTransaction.objects.bulk_create(transactions_to_create)

    @staticmethod
    def _approve_transactions(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        approves Transactions based on the Block's events
        """
        data_by_call_hash: defaultdict = defaultdict(list)
        for multisig_event in block.event_data.get("Multisig", {}).get("MultisigApproval", []):
            data_by_call_hash[(multisig_event["call_hash"], multisig_event["multisig"])].append(
                multisig_event["approving"]
            )

        if data_by_call_hash:
            for transaction in (
                transaction_to_update := models.MultiSigTransaction.objects.filter(
                    # WHERE (
                    #     (call_hash = 1 AND multisig_id 2)
                    #     OR (call_hash = 3 AND multisig_id 4)
                    #     OR ...
                    #     AND executed_at is null
                    # )
                    reduce(
                        Q.__or__,
                        [
                            Q(call_hash=call_hash, multisig_id=multisig)
                            for (call_hash, multisig) in data_by_call_hash.keys()
                        ],
                    ),
                    executed_at__isnull=True,
                )
            ):
                transaction.approvers.extend(data_by_call_hash[(transaction.call_hash, transaction.multisig_id)])
            if transaction_to_update:
                models.MultiSigTransaction.objects.bulk_update(transaction_to_update, ("approvers",))

    @staticmethod
    def _execute_transactions(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        executes Transactions based on the Block's events
        """
        from core.substrate import substrate_service

        if data_by_call_hash := {
            (multisig_event["call_hash"], multisig_event["multisig"]): multisig_event["approving"]
            for multisig_event in block.event_data.get("Multisig", {}).get("MultisigExecuted", [])
        }:
            extrinsic_data_by_call_hash = {}
            for multisig_extrinsic in block.extrinsic_data.get("Multisig", {}).get("as_multi", []):
                call = multisig_extrinsic["call"]
                call_data = {
                    "module": call["call_module"],
                    "function": call["call_function"],
                    "args": {call_arg["name"]: call_arg["value"] for call_arg in call.get("call_args", [])},
                    "timepoint": multisig_extrinsic["maybe_timepoint"],
                }
                call_data["hash"] = substrate_service.create_multisig_transaction_call_hash(**call_data)
                extrinsic_data_by_call_hash[call_data["hash"]] = call_data
            for transaction in (
                transaction_to_update := models.MultiSigTransaction.objects.filter(
                    # WHERE (
                    #     (call_hash = 1 AND multisig_id 2)
                    #     OR (call_hash = 3 AND multisig_id 4)
                    #     OR ...
                    #     AND executed_at is null
                    # )
                    reduce(
                        Q.__or__,
                        [
                            Q(call_hash=call_hash, multisig_id=multisig)
                            for (call_hash, multisig) in data_by_call_hash.keys()
                        ],
                    ),
                    executed_at__isnull=True,
                )
            ):
                if call_data := extrinsic_data_by_call_hash.get(transaction.call_hash):
                    corresponding_model_ids = substrate_service.parse_call_data(call_data=call_data)
                    transaction.call = call_data
                    transaction.call_function = call_data["function"]
                    transaction.timepoint = call_data["timepoint"]
                    transaction.asset_id = corresponding_model_ids["asset_id"]
                    transaction.dao_id = corresponding_model_ids["dao_id"]
                    transaction.proposal_id = corresponding_model_ids["proposal_id"]

                transaction.approvers.append(data_by_call_hash[(transaction.call_hash, transaction.multisig_id)])
                transaction.status = models.TransactionStatus.EXECUTED
                transaction.executed_at = timezone.now()

            if transaction_to_update:
                models.MultiSigTransaction.objects.bulk_update(
                    transaction_to_update,
                    (
                        "approvers",
                        "call",
                        "call_function",
                        "timepoint",
                        "status",
                        "executed_at",
                        "asset_id",
                        "dao_id",
                        "proposal_id",
                    ),
                )

    @staticmethod
    def _cancel_transactions(block: models.Block):
        """
        Args:
            block: Block containing extrinsics and events

        cancels Transactions based on the Block's events
        """
        if data_by_call_hash := {
            (multisig_event["call_hash"], multisig_event["multisig"]): multisig_event["cancelling"]
            for multisig_event in block.event_data.get("Multisig", {}).get("MultisigCancelled", [])
        }:
            for transaction in (
                transaction_to_update := models.MultiSigTransaction.objects.filter(
                    # WHERE (
                    #     (call_hash = 1 AND multisig_id 2)
                    #     OR (call_hash = 3 AND multisig_id 4)
                    #     OR ...
                    #     AND executed_at is null
                    # )
                    reduce(
                        Q.__or__,
                        [
                            Q(call_hash=call_hash, multisig_id=multisig)
                            for (call_hash, multisig) in data_by_call_hash.keys()
                        ],
                    ),
                    executed_at__isnull=True,
                )
            ):
                transaction.canceled_by = data_by_call_hash[(transaction.call_hash, transaction.multisig_id)]
                transaction.status = models.TransactionStatus.CANCELLED
            if transaction_to_update:
                models.MultiSigTransaction.objects.bulk_update(transaction_to_update, ("canceled_by", "status"))

    @atomic
    def execute_actions(self, block: models.Block):
        """
        Args:
             block: Block to execute

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
