import base64
import logging
import time
from collections import defaultdict
from functools import partial, wraps
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection
from scalecodec import GenericExtrinsic
from substrateinterface import Keypair
from websocket import WebSocketConnectionClosedException

from core import models
from core.event_handler import substrate_event_handler

logger = logging.getLogger("alerts")


def retry(description: str):
    """
    Args:
        description: short description of wrapped action, used for logging

    Returns:
        wrapped function

    wraps function in retry functionality
    """

    def wrap(f):
        @wraps(f)
        def action(*args, **kwargs):
            retry_delays = settings.RETRY_DELAYS
            max_delay = retry_delays[-1]
            retry_delays = iter(retry_delays)

            def log_and_sleep(err_msg: str, log_exception=False):
                retry_delay = next(retry_delays, max_delay)
                err_msg = f"{err_msg} while {description}. "
                if block_number := kwargs.get("block_number"):
                    err_msg += f"Block number: {block_number}. "
                if block_hash := kwargs.get("block_hash"):
                    err_msg += f"Block hash: {block_hash}. "
                err_msg += f"Retrying in {retry_delay}s ..."
                if log_exception:
                    logger.exception(err_msg)
                else:
                    logger.error(err_msg)
                time.sleep(retry_delay)

            while True:
                try:
                    return f(*args, **kwargs)
                except WebSocketConnectionClosedException:
                    log_and_sleep("WebSocketConnectionClosedException")
                except ConnectionRefusedError:
                    log_and_sleep("ConnectionRefusedError")
                except BrokenPipeError:
                    log_and_sleep("BrokenPipeError")
                except Exception:  # noqa E722
                    log_and_sleep("Unexpected error", log_exception=True)

        return action

    return wrap


class SubstrateException(Exception):
    pass


class OutOfSyncException(SubstrateException):
    msg = "DB and chain are unrecoverably out of sync!"

    def __init__(self, *args):
        args = (self.msg,) if not args else args
        super().__init__(*args)


class SubstrateService(object):
    substrate_interface = None

    @retry("initializing blockchain connection")
    def __init__(self):
        self.substrate_interface = settings.SUBSTRATE_INTERFACE(
            url=settings.BLOCKCHAIN_URL,
            type_registry_preset=settings.TYPE_REGISTRY_PRESET,
        )

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.substrate_interface.close()

    def retrieve_account_balance(self, account_address: str) -> dict:
        """
        Args:
            account_address: Account's ss58_address

        Returns:
            balance dict
            {
                "free": int,
                "reserved": int,
                "misc_frozen": int,
                "fee_frozen": int,
            }

        fetches Account's balance dict
        """
        return self.substrate_interface.query(
            module="System", storage_function="Account", params=[account_address]
        ).value["data"]

    def submit_extrinsic(self, extrinsic: GenericExtrinsic, wait_for_inclusion=True):
        """
        Args:
            extrinsic: extrinsic to submit
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits extrinsic logs errors messages if wait_for_inclusion=True
        """
        receipt = self.substrate_interface.submit_extrinsic(extrinsic=extrinsic, wait_for_inclusion=wait_for_inclusion)
        if wait_for_inclusion and not receipt.is_success:
            logger.error(f"Error during extrinsic submission: {receipt.error_message}")

    def sync_initial_accs(self):
        """
        Returns:
            None

        fetches accounts from blockchain and creates an Account table entry for each
        """
        logger.info("Syncing initial accounts...")
        models.Account.objects.bulk_create(
            [
                models.Account(address=acc_addr)
                for acc_addr, _ in self.substrate_interface.query_map("System", "Account")
            ],
            ignore_conflicts=True,
        )

    def create_dao(self, dao_id: str, dao_name: str, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            dao_id: id of the new dao
            dao_name: name of the new dao
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg
        Returns:
            None

        submits a signed extrinsic to create a new dao on the blockchain
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="create_dao",
                    call_params={"dao_id": dao_id, "dao_name": dao_name},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def transfer_dao_ownership(self, dao_id: str, new_owner_id: str, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            dao_id: dao id to change ownership for
            new_owner_id: new dao owner
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to change a dao's ownership on the blockchain
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="change_owner",
                    call_params={"dao_id": dao_id, "new_owner": new_owner_id},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def destroy_dao(self, dao_id: str, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            dao_id: dao id to destroy
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to destroy a dao on the blockchain
        """

        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="destroy_dao",
                    call_params={"dao_id": dao_id},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def issue_tokens(self, dao_id: str, amount: int, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            dao_id: dao id to issue tokens for
            amount: amount of tokens to be issued
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to issue tokens for a dao on the blockchain
        (creates a new asset and links it to the dao)
        """

        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="issue_token",
                    call_params={"dao_id": dao_id, "supply": amount},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def transfer_asset(self, asset_id: str, target: str, amount: int, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            asset_id: asset to transfer balance from
            target: target address / account to transfer balance to
            amount: amount of balance to transfer
            keypair: Keypair used to sign the
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to transfer balance from an asset to an address / account on the blockchain
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Assets",
                    call_function="transfer",
                    call_params={"id": asset_id, "target": target, "amount": amount},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def transfer_balance(self, target: str, value: int, keypair: Keypair, wait_for_inclusion=False):
        """

        Args:
            target: address / account to transfer balance to
            value: amount to transfer
            keypair: Keypair used to sign the
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to transfer balance to a target address on the blockchain
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Balances",
                    call_function="transfer",
                    call_params={"dest": target, "value": value},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def set_balance(self, target: str, new_free: int, new_reserved: int, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            target: address / account to set balance for
            new_free: new free balance
            new_reserved: new reserve balance
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to set new values for the balance (free and reserved)
        of the target address / account on the blockchain
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
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
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def dao_set_metadata(
        self, dao_id: str, metadata_url: str, metadata_hash: str, keypair: Keypair, wait_for_inclusion=False
    ):
        """
        Args:
            dao_id: dao to set metadata for
            metadata_url: url of the metadata
            metadata_hash: hash of the metadata
            keypair: Keypair used to sign the
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to set metadata on a given dao
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="DaoCore",
                    call_function="set_metadata",
                    call_params={"dao_id": dao_id, "meta": metadata_url, "hash": metadata_hash},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def set_governance_majority_vote(
        self,
        dao_id: str,
        proposal_duration: int,
        proposal_token_deposit: int,
        minimum_majority_per_1024: int,
        keypair: Keypair,
        wait_for_inclusion=False,
    ):
        """
        Args:
            dao_id: dao to governance type for
            proposal_duration: the number of blocks a proposal is open for voting
            proposal_token_deposit: the token deposit required to create a proposal
            minimum_majority_per_1024:
                how many more ayes than nays there must be for proposal acceptance
                thus proposal acceptance requires: ayes >= nays + token_supply / 1024 * minimum_majority_per_1024
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to set governance type to majority vote for a given dao
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="set_governance_majority_vote",
                    call_params={
                        "dao_id": dao_id,
                        "proposal_duration": proposal_duration,
                        "proposal_token_deposit": proposal_token_deposit,
                        "minimum_majority_per_1024": minimum_majority_per_1024,
                    },
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def create_proposal(
        self,
        dao_id: str,
        proposal_id: str,
        metadata_url: str,
        metadata_hash: str,
        keypair: Keypair,
        wait_for_inclusion=False,
    ):
        """
        Args:
            dao_id: dao to create proposal for
            proposal_id: id of the proposal
            metadata_url: url of the metadata
            metadata_hash: hash of the metadata
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits a singed extrinsic to create a proposal for a given dao
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
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
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def vote_on_proposal(self, proposal_id: str, in_favor: bool, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            proposal_id: Proposal id to vote on
            in_favor: in favor
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        Returns:
            None

        submits singed extrinsic to vote on a given proposal
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="vote",
                    call_params={
                        "proposal_id": proposal_id,
                        "in_favor": in_favor,
                    },
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def finalize_proposal(self, proposal_id: str, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
             proposal_id: Proposal id to finalize
             keypair: Keypair used to sign the extrinsic
             wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

         Returns:
             None

             submits singed extrinsic to finalize a given proposal
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="finalize_proposal",
                    call_params={"proposal_id": proposal_id},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def fault_proposal(self, proposal_id: str, reason: str, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
             proposal_id: Proposal id to fault
             reason: reason Proposal was faulted
             keypair: Keypair used to sign the extrinsic
             wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

         Returns:
             None

             submits singed extrinsic to fault a given proposal
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="fault_proposal",
                    call_params={"proposal_id": proposal_id, "reason": reason},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    @staticmethod
    def verify(address: str, signature: str) -> bool:
        """
        Args:
            address: Account.address / public key
            signature: b64 encoded, signed challenge key

        Returns:
            bool

        verifies whether the given signature matches challenge key signed by address
        """

        if not (challenge_token := cache.get(address)):
            return False
        try:
            return Keypair(address).verify(challenge_token, base64.b64decode(signature.encode()))
        except Exception:  # noqa E722
            return False

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

        # substrate_interface requires block_hash xor block_number
        if block_hash and block_number is not None:
            block_number = None

        # fetch the latest block
        block_data = retry("fetching block from chain")(self.substrate_interface.get_block)(
            block_hash=block_hash, block_number=block_number
        )

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
        events = retry("fetching events from chain")(self.substrate_interface.get_events)(
            block_hash=block_data["header"]["hash"]
        )
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
                raise OutOfSyncException

    @staticmethod
    def sleep(start_time):
        """
        Args:
            start_time: start time

        Returns:
            None

        ensure at least BLOCK_CREATION_INTERVAL sleep time
        """
        elapsed_time = time.time() - start_time
        if elapsed_time < settings.BLOCK_CREATION_INTERVAL:
            time.sleep(settings.BLOCK_CREATION_INTERVAL - elapsed_time)

    def clear_db(self, start_time: float = None) -> models.Block:
        """
        Args:
            start_time: time since last block was fetched from chain

        Returns:
            empty db start Block

        empties db, fetches seed accounts, sleeps if start_time was given, returns start Block
        """
        logger.info("DB and chain are out of sync! Recreating DB...")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                truncate core_block;
                truncate core_account cascade;
                """
            )
        self.sync_initial_accs()
        if start_time:
            self.sleep(start_time=start_time)
        return models.Block(number=-1)

    def listen(self):
        """
        fetches and executes blocks from there chain in an endless loop

        Raises:
            SubstrateException
        """

        last_block = models.Block.objects.filter(executed=False).order_by("-number").first()
        if not last_block:
            last_block = models.Block.objects.order_by("-number").first()
        # we can't sync with the chain if we have unprocessed blocks in the db
        if last_block and not last_block.executed:
            logger.error(
                f"Last Block was not executed. Retrying... number: {last_block.number} | hash: {last_block.hash}"
            )
            try:
                substrate_event_handler.execute_actions(last_block)
            except Exception:  # noqa E722
                logger.exception(f"Block not executable. number: {last_block.number} | hash: {last_block.hash}")
                last_block = self.clear_db()
        # set start value for empty db
        if not last_block:
            last_block = models.Block(number=-1)
        while True:
            start_time = time.time()
            try:
                current_block = self.fetch_and_parse_block()
            except OutOfSyncException:
                last_block = self.clear_db(start_time=start_time)
                continue

            # shouldn't currently happen since fetch_and_parse_block would raise before this.
            # might happen in the future if we decide to only keep the last x blocks.
            # this happens if the chain restarts, we have to recreate the entire DB
            if last_block.number > current_block.number:
                last_block = self.clear_db(start_time=start_time)
                continue
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
                    time.sleep(0.25)  # todo implement non shitty solution

            self.sleep(start_time=start_time)


substrate_service = SubstrateService()
