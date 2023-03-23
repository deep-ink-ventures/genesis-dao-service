import logging
import time
from collections import defaultdict
from functools import partial
from typing import Optional

from django.conf import settings
from django.db import IntegrityError
from substrateinterface import Keypair, SubstrateInterface

from core import models
from core.event_handler import substrate_event_handler

logger = logging.getLogger("alerts")


class SubstrateException(Exception):
    pass


class OutOfSyncException(SubstrateException):
    msg = "DB and chain are unrecoverably out of sync!"

    def __init__(self, *args):
        args = (self.msg,) if not args else args
        super().__init__(*args)


class SubstrateService(object):
    substrate_interface = None

    def __init__(self):
        self.substrate_interface = SubstrateInterface(
            url=settings.BLOCKCHAIN_URL,
            type_registry_preset=settings.TYPE_REGISTRY_PRESET,
        )

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.substrate_interface.close()

    def sync_initial_accs(self):
        """
        Returns:
            None

        fetches accounts from blockchain and creates an Account table entry for each
        """
        models.Account.objects.bulk_create(
            [
                models.Account(address=acc_addr)
                for acc_addr, _ in self.substrate_interface.query_map("System", "Account")
            ],
            ignore_conflicts=True,
        )

    def create_dao(self, dao_id: str, dao_name: str, keypair: Keypair):
        """
        Args:
            dao_id: id of the new dao
            dao_name: name of the new dao
            keypair: Keypair used to sign the extrinsic
        Returns:
            None

        submits a signed extrinsic to create a new dao on the blockchain
        """
        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="create_dao",
                    call_params={"dao_id": dao_id, "dao_name": dao_name},
                ),
                keypair=keypair,
            )
        )

    def destroy_dao(self, dao_id: str, keypair: Keypair):
        """
        Args:
            dao_id: dao id to destroy
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to destroy a dao on the blockchain
        """

        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="destroy_dao",
                    call_params={"dao_id": dao_id},
                ),
                keypair=keypair,
            )
        )

    def issue_tokens(self, dao_id: str, amount: int, keypair: Keypair):
        """
        Args:
            dao_id: dao id to issue tokens for
            amount: amount of tokens to be issued
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to issue tokens for a dao on the blockchain
        (creates a new asset and links it to the dao)
        """

        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="issue_token",
                    call_params={"dao_id": dao_id, "supply": amount},
                ),
                keypair=keypair,
            )
        )

    def transfer_asset(self, asset_id: str, target: str, amount: int, keypair: Keypair):
        """
        Args:
            asset_id: asset to transfer balance from
            target: target address / account to transfer balance to
            amount: amount of balance to transfer
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to transfer balance from an asset to an address / account on the blockchain
        """
        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Assets",
                    call_function="transfer",
                    call_params={"id": asset_id, "target": target, "amount": amount},
                ),
                keypair=keypair,
            )
        )

    def transfer_balance(self, target: str, value: int, keypair: Keypair):
        """

        Args:
            target: address / account to transfer balance to
            value: amount to transfer
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to transfer balance to a target address on the blockchain
        """
        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Balances",
                    call_function="transfer",
                    call_params={"dest": target, "value": value},
                ),
                keypair=keypair,
            )
        )

    def set_balance(self, target: str, new_free: int, new_reserved: int, keypair: Keypair):
        """
        Args:
            target: address / account to set balance for
            new_free: new free balance
            new_reserved: new reserve balance
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to set new values for the balance (free and reserved)
        of the target address / account on the blockchain
        """
        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Sudo",
                    call_function="sudo",
                    call_params={
                        "call": self.substrate_interface.compose_call(
                            call_module="Balances",
                            call_function="set_balance",
                            call_params={"who": target, "new_free": new_free, "new_reserved": new_reserved},
                        )
                    },
                ),
                keypair=keypair,
            )
        )

    def dao_set_metadata(self, dao_id: str, metadata_url: str, metadata_hash: str, keypair: Keypair):
        """
        Args:
            dao_id: dao to set metadata for
            metadata_url: url of the metadata
            metadata_hash: hash of the metadata
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to set metadata on a given dao
        """
        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="set_metadata",
                    call_params={"dao_id": dao_id, "meta": metadata_url, "hash": metadata_hash},
                ),
                keypair=keypair,
            )
        )

    def set_governance_majority_vote(
        self,
        dao_id: str,
        proposal_duration: int,
        proposal_token_deposit: int,
        minimum_majority_per_256: int,
        keypair: Keypair,
    ):
        """
        Args:
            dao_id: dao to governance type for
            proposal_duration: the number of blocks a proposal is open for voting
            proposal_token_deposit: the token deposit required to create a proposal
            minimum_majority_per_256:
                how many more ayes than nays there must be for proposal acceptance
                thus proposal acceptance requires: ayes >= nays + token_supply / 256 * minimum_majority_per_256
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to set governance type to majority vote for a given dao
        """
        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="set_governance_majority_vote",
                    call_params={
                        "dao_id": dao_id,
                        "proposal_duration": proposal_duration,
                        "proposal_token_deposit": proposal_token_deposit,
                        "minimum_majority_per_256": minimum_majority_per_256,
                    },
                ),
                keypair=keypair,
            )
        )

    def create_proposal(self, dao_id: str, proposal_id: str, metadata_url: str, metadata_hash: str, keypair: Keypair):
        """
        Args:
            dao_id: dao to create proposal for
            proposal_id: id of the proposal
            metadata_url: url of the metadata
            metadata_hash: hash of the metadata
            keypair: Keypair used to sign the extrinsic

        Returns:
            None

        submits a singed extrinsic to create a proposal for a given dao
        """
        self.substrate_interface.submit_extrinsic(
            self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="create_proposal",
                    call_params={
                        "dao_id": dao_id,
                        "proposal_id": proposal_id,
                        "meta": metadata_url,
                        "hash": metadata_hash,
                    },
                ),
                keypair=keypair,
            )
        )

    def fetch_and_parse_block(
        self,
        block_hash: str = None,
        block_number: int = None,
        recreate=False,
    ) -> Optional[models.Block]:
        """
        Args:
            block_hash: hash of block to fetch, takes priority over block_number
            block_number: number of block to fetch
            recreate: recreates Block if already exists

        Returns:
            returns Block instance
        Raises:
            SubstrateException

        fetches the latest block / head of the chain
        parses the extrinsics and events
        creates a Block table entry if the does not already exist
        """
        # check if matching Block already exists in the db
        qs = models.Block.objects.all()
        if (
            _filter := {"hash": block_hash}
            if block_hash
            else {"number": block_number}
            if block_number is not None
            else {}
        ):
            qs = qs.filter(**_filter)
            if qs.exists():
                if recreate:
                    qs.delete()
                else:
                    return qs.get()
        # fetch the latest block
        try:
            # substrate_interface requires block_hash xor block_number
            if block_hash and block_number is not None:
                block_number = None
            block_data = self.substrate_interface.get_block(block_hash=block_hash, block_number=block_number)
        except Exception as exc:  # noqa E722
            err_msg = "Error while fetching block from chain."
            logger.exception(err_msg)
            raise SubstrateException(err_msg)

        if not block_data:
            raise SubstrateException("SubstrateInterface.get_block returned no data.")

        # create nested dict structure of extrinsics
        extrinsic_data = defaultdict(partial(defaultdict, list))
        for extrinsic in block_data["extrinsics"]:
            call_data = extrinsic.value["call"]
            extrinsic_data[call_data["call_module"]][call_data["call_function"]].append(
                {arg["name"]: arg["value"] for arg in call_data["call_args"]}
            )
        # create nested dict structure of events
        event_data = defaultdict(partial(defaultdict, list))
        events = self.substrate_interface.get_events(block_hash=block_data["header"]["hash"])
        for event in events:
            event_data[event.value["module_id"]][event.value["event_id"]].append(event.value["attributes"])

        block_attrs = {
            "number": block_data["header"]["number"],
            "hash": block_data["header"]["hash"],
            "parent_hash": block_data["header"]["parentHash"],
            "event_data": event_data,
            "extrinsic_data": extrinsic_data,
        }
        try:
            return models.Block.objects.get(**block_attrs)
        except models.Block.DoesNotExist:
            try:
                return models.Block.objects.create(**block_attrs)
            except IntegrityError:
                logger.error("DB and chain are unrecoverably out of sync!")
                raise OutOfSyncException

    def listen(self):
        """
        fetches and executes blocks from there chain in an endless loop

        Raises:
            SubstrateException
        """

        last_block = models.Block.objects.order_by("-number").first()
        # we can't sync with the chain if we have unprocessed blocks in the db
        if last_block and not last_block.executed:
            msg = f"Last Block was not executed! number: {last_block.number} | hash: {last_block.hash}"
            logger.error(msg)
            raise SubstrateException(msg)
        # set start value for empty db
        if not last_block:
            last_block = models.Block(number=-1)
        while True:
            start_time = time.time()
            current_block = self.fetch_and_parse_block()
            # this should not happen, unrecoverable, short of a complete resync
            # todo : clarify if we should auto resync
            if last_block.number > current_block.number:
                logger.error("DB and chain are unrecoverably out of sync!")
                raise OutOfSyncException
            # we already processed this block
            # shouldn't normally happen due BLOCK_CREATION_INTERVAL sleep time
            if last_block.number == current_block.number:
                logger.info(f"Waiting for new block | number {current_block.number} | hash: {current_block.hash}")
            # if the last processed block directly precedes the current block our db is in sync with the chain, and we
            # can directly execute the current block
            elif last_block.number + 1 == current_block.number:
                logger.info(f"Processing latest block | number: {current_block.number} | hash: {current_block.hash}")
                substrate_event_handler.execute_actions(current_block)
                last_block = current_block
            # our db is out of sync with the chain. we fetch and execute blocks until we caught up
            else:
                while current_block.number > last_block.number:
                    logger.info(f"Catching up | number: {last_block.number + 1}")
                    next_block = self.fetch_and_parse_block(block_number=last_block.number + 1)
                    substrate_event_handler.execute_actions(next_block)
                    last_block = next_block
            # ensure at least 6 seconds sleep time before fetching the next block
            elapsed_time = time.time() - start_time
            if elapsed_time < settings.BLOCK_CREATION_INTERVAL:
                time.sleep(settings.BLOCK_CREATION_INTERVAL - elapsed_time)


substrate_service = SubstrateService()
