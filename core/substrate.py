import base64
import hashlib
import logging
import time
from collections import defaultdict
from functools import partial, wraps
from typing import Collection, List, Optional
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection
from scalecodec import GenericCall, GenericExtrinsic, MultiAccountId
from scalecodec.base import ScaleBytes
from substrateinterface import ContractCode, ContractEvent, ContractInstance
from substrateinterface.keypair import Keypair
from websocket import WebSocketConnectionClosedException

from core import models
from core.event_handler import substrate_event_handler
from core.models import Dao

logger = logging.getLogger("alerts")
slack_logger = logging.getLogger("alerts.slack")

INK_DEFAULT_GAS_LIMIT = {"ref_time": 2599000000, "proof_size": 1199038364791120855}


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

            def log_and_sleep(err_msg: str, log_exception=False, log_to_slack=True):
                _logger = slack_logger if log_to_slack else logger
                retry_delay = next(retry_delays, max_delay)
                err_msg = f"{err_msg} while {description}. "
                if block_number := kwargs.get("block_number"):
                    err_msg += f"Block number: {block_number}. "
                if block_hash := kwargs.get("block_hash"):
                    err_msg += f"Block hash: {block_hash}. "
                err_msg += f"Retrying in {retry_delay}s ..."
                if log_exception:
                    _logger.exception(err_msg)
                else:
                    _logger.error(err_msg)
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
            url=settings.BLOCKCHAIN_URL, type_registry_preset=settings.TYPE_REGISTRY_PRESET
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
                "frozen": int,
                "flags": int,
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

        submits extrinsic logs errors messages if wait_for_inclusion=True
        """
        receipt = self.substrate_interface.submit_extrinsic(extrinsic=extrinsic, wait_for_inclusion=wait_for_inclusion)
        if wait_for_inclusion and not receipt.is_success:
            logger.error(f"Error during extrinsic submission: {receipt.error_message}")

    def batch(self, calls: Collection[GenericCall], keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            calls: calls to batch
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to batch a sequence of calls
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Utility", call_function="batch_all", call_params={"calls": calls}
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def batch_as_multisig(self, calls, multisig_account, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            calls: calls to batch
            multisig_account: corresponding multisig account
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to batch a sequence of multisig calls
        """

        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_multisig_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Utility", call_function="batch_all", call_params={"calls": calls}
                ),
                keypair=keypair,
                multisig_account=multisig_account,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def initiate_dao_on_ink(self, dao: Dao):
        kp = Keypair.create_from_uri(settings.SUBSTRATE_FUNDING_KEYPAIR_URI)

        one_year_in_seconds = 365 * 24 * 60 * 60
        blocks_per_year = one_year_in_seconds / settings.BLOCK_CREATION_INTERVAL
        print("create dao asset contract")
        dao_asset_contract = self.deploy_contract(
            contract_base_path=f"{settings.BASE_DIR}/wasm/",
            contract_name="dao_asset_contract",
            keypair=kp,
            constructor_name="new",
            contract_constructor_args={"asset_id": dao.asset.id},
        )
        print("create genesis dao contract")
        genesis_dao_contract = self.deploy_contract(
            contract_base_path=f"{settings.BASE_DIR}/wasm/",
            contract_name="genesis_dao_contract",
            keypair=kp,
            constructor_name="new",
            contract_constructor_args={"owner": kp.ss58_address, "asset_id": dao.asset.id},
        )

        print("create vesting wallet contract")
        vesting_wallet_contract = self.deploy_contract(
            contract_base_path=f"{settings.BASE_DIR}/wasm/",
            contract_name="vesting_wallet_contract",
            keypair=kp,
            constructor_name="new",
            contract_constructor_args={"token": dao_asset_contract.contract_address},
        )

        vote_escrow_contract = self.deploy_contract(
            contract_base_path=f"{settings.BASE_DIR}/wasm/",
            contract_name="vote_escrow_contract",
            keypair=kp,
            constructor_name="new",
            contract_constructor_args={
                "token": dao_asset_contract.contract_address, "max_time": blocks_per_year, "boost": 4
            },
        )

        print("register vote plugins")
        calls = [
            self.substrate_interface.compose_call(
                call_module='Contracts',
                call_function='call',
                call_params={
                    'dest': genesis_dao_contract.contract_address,
                    'value': 0,
                    'gas_limit': INK_DEFAULT_GAS_LIMIT,
                    'storage_deposit_limit': None,
                    'data': genesis_dao_contract.metadata.generate_message_data(
                        name="register_vote_plugin",
                        args={"vote_plugin": vesting_wallet_contract.contract_address}
                    ).to_hex()
                },
            ),
            self.substrate_interface.compose_call(
                call_module='Contracts',
                call_function='call',
                call_params={
                    'dest': genesis_dao_contract.contract_address,
                    'value': 0,
                    'gas_limit': INK_DEFAULT_GAS_LIMIT,
                    'storage_deposit_limit': None,
                    'data': genesis_dao_contract.metadata.generate_message_data(
                        name="register_vote_plugin",
                        args={"vote_plugin": vote_escrow_contract.contract_address}
                    ).to_hex()
                },
            ),
            self.substrate_interface.compose_call(
                call_module='Contracts',
                call_function='call',
                call_params={
                    'dest': genesis_dao_contract.contract_address,
                    'value': 0,
                    'gas_limit': INK_DEFAULT_GAS_LIMIT,
                    'storage_deposit_limit': None,
                    'data': genesis_dao_contract.metadata.generate_message_data(
                        name="transfer_ownership",
                        args={"new_owner": dao.owner.address}
                    ).to_hex()
                },
            ),
        ]
        self.batch(calls, kp, wait_for_inclusion=False)
        dao.ink_asset_contract = dao_asset_contract.contract_address
        dao.ink_registry_contract = genesis_dao_contract.contract_address
        dao.ink_vesting_wallet_contract = vesting_wallet_contract.contract_address
        dao.ink_vote_escrow_contract = vote_escrow_contract.contract_address
        dao.save()
        print("done")

    def deploy_contract(
        self,
        contract_base_path: str,
        contract_name: str,
        keypair: Keypair,
        constructor_name: str = "new",
        contract_constructor_args: dict = None,
    ) -> ContractInstance:
        """
        Args:
            contract_base_path: absolute path to .../target/ink/
            contract_name: name of the contract
            keypair: Keypair used to sign the txn to deploy the contract
            constructor_name: defaults to "new"
            contract_constructor_args:  args for the contract constructor
        Returns:
            the contract instance
        """
        path = contract_base_path + contract_name
        return ContractCode.create_from_contract_files(
            wasm_file=path + ".wasm",
            metadata_file=path + ".json",
            substrate=self.substrate_interface,
        ).deploy(
            constructor=constructor_name,
            args=contract_constructor_args,
            keypair=keypair,
            upload_code=True,
            gas_limit=INK_DEFAULT_GAS_LIMIT,
            deployment_salt=uuid4().hex
        )

    def sync_initial_accs(self):
        """
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

        submits a signed extrinsic to change a dao's ownership on the blockchain
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

        submits a signed extrinsic to destroy a dao on the blockchain
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

    def issue_token(self, dao_id: str, amount: int, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            dao_id: dao id to issue tokens for
            amount: amount of tokens to be issued
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to issue tokens for a dao on the blockchain
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
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to transfer balance from an asset to an address / account on the blockchain
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

    def delegate_asset(self, asset_id: str, target_id: str, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            asset_id: asset to delegate
            target_id: target
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to delegate tokens from an asset to an address / account on the blockchain
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Assets",
                    call_function="delegate",
                    call_params={"id": asset_id, "target": target_id},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def revoke_asset_delegation(self, asset_id: str, target_id: str, keypair: Keypair, wait_for_inclusion=False):
        """
        Args:
            asset_id: asset to delegate
            target_id: target
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to revoke delegation of tokens on the blockchain
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Assets",
                    call_function="revoke_delegation",
                    call_params={"id": asset_id, "source": target_id},
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

        submits a signed extrinsic to transfer balance to a target address on the blockchain
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

    def set_balance_deprecated(
        self, target: str, new_free: int, old_reserved: int, keypair: Keypair, wait_for_inclusion=False
    ):
        """
        Args:
            target: address / account to set balance for
            new_free: new free balance
            old_reserved: old reserve balance
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to set new values for the balance (free and reserved)
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
                            call_function="set_balance_deprecated",
                            call_params={"who": target, "new_free": new_free, "old_reserved": old_reserved},
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

        submits a signed extrinsic to set metadata on a given dao
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

        submits a signed extrinsic to set governance type to majority vote for a given dao
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
        keypair: Keypair,
        wait_for_inclusion=False,
    ):
        """
        Args:
            dao_id: dao to create proposal for
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to create a proposal for a given dao
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="create_proposal",
                    call_params={"dao_id": dao_id},
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def proposal_set_metadata(
        self,
        proposal_id: str,
        metadata_url: str,
        metadata_hash: str,
        keypair: Keypair,
        wait_for_inclusion=False,
    ):
        """
        Args:
            proposal_id: id of the proposal
            metadata_url: url of the metadata
            metadata_hash: hash of the metadata
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits a signed extrinsic to set metadata for a given proposal
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Votes",
                    call_function="set_metadata",
                    call_params={
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

        submits signed extrinsic to vote on a given proposal
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

             submits signed extrinsic to finalize a given proposal
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


             submits signed extrinsic to fault a given proposal
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

    def create_multisig_account(self, signatories: List[str] = None, threshold: int = None) -> MultiAccountId:
        """
        Args:
            signatories: List of signatory addresses.
            threshold: Number of signatories needed to execute the transaction.
        Returns:
             a MultiSig Account
        """
        return self.substrate_interface.generate_multisig_account(signatories=signatories, threshold=threshold)

    def create_multisig_transaction_call_hash(self, module: str, function: str, args: dict, *_, **__) -> str:
        """
        Args:
            module : name of the call module
            function : name of the call function
            args : args for the call function

        Returns:
            The transaction call hash as a hexadecimal string.

        """
        call_hex = self.substrate_interface.compose_call(
            call_module=module, call_function=function, call_params=args
        ).call_hash.hex()
        return f"0x{call_hex}"

    def approve_multisig(
        self,
        multisig_account,
        call: GenericCall,
        keypair: Keypair,
        wait_for_inclusion=False,
    ):
        """
        Args:
            multisig_account: The multisig account from which the funds will be transferred.
            call: GenericCall to sign and submit
            keypair: Keypair used to sign the extrinsic
            wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits signed extrinsic to approve a multisig transaction
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_multisig_extrinsic(
                call=call,
                keypair=keypair,
                multisig_account=multisig_account,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    def cancel_multisig(
        self,
        multisig_account: MultiAccountId,
        call: GenericCall,
        keypair: Keypair,
        wait_for_inclusion=False,
    ):
        """
        Args:
             multisig_account: corresponding multisig acc
             call: call of the multisig transaction to cancel
             keypair: Keypair used to sign the extrinsic
             wait_for_inclusion: wait for inclusion of extrinsic in block, required for error msg

        submits signed extrinsic to cancel a multisig transaction
        """
        self.submit_extrinsic(
            extrinsic=self.substrate_interface.create_signed_extrinsic(
                call=self.substrate_interface.compose_call(
                    call_module="Multisig",
                    call_function="cancel_as_multi",
                    call_params={
                        "call_hash": call.call_hash,
                        "other_signatories": [
                            signatory
                            for signatory in multisig_account.signatories
                            if signatory != f"0x{keypair.public_key.hex()}"
                        ],
                        "threshold": multisig_account.threshold,
                        "timepoint": self.substrate_interface.query(
                            module="Multisig",
                            storage_function="Multisigs",
                            params=[multisig_account.value, call.call_hash],
                        ).value["when"],
                        "max_weight": self.substrate_interface.get_payment_info(call, keypair)["weight"],
                    },
                ),
                keypair=keypair,
            ),
            wait_for_inclusion=wait_for_inclusion,
        )

    @staticmethod
    def parse_call_data(call_data: dict) -> dict:
        """
        Args:
            call_data: keys: hash, module, function, args

        Returns:
            dict containing the keys asset_id, dao_id, proposal_id
            all values are optional

        parses call_data and returns a dict of affected model ids
        used to populate corresponding models during MultiSigTransaction creation
        """
        corresponding_model_ids = {
            "asset_id": None,
            "dao_id": None,
            "proposal_id": None,
        }
        module, args = call_data["module"], call_data["args"]
        # set direct references
        for model_id in corresponding_model_ids.keys():
            if model_id in args:
                corresponding_model_ids[model_id] = args[model_id]
        # set ambiguous references
        match module:
            case "Assets":
                corresponding_model_ids["asset_id"] = corresponding_model_ids["asset_id"] or args.get("id")
        return corresponding_model_ids

    @staticmethod
    def verify(address: str, challenge_address: str, signature: str) -> bool:
        """
        Args:
            address: Account.address / public key to verify signature for
            challenge_address: Account.address / public key the challenge has been created for
            signature: b64 encoded, signed challenge key

        Returns:
            bool

        verifies whether the given signature matches challenge key signed by address
        """

        if not (challenge_token := cache.get(challenge_address)):
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
            attributes = event.value["attributes"] or {}
            try:
                attributes["raw_data"] = event.value_object['event'][1][1]["data"].value_object
            except (KeyError, TypeError):
                attributes["raw_data"] = None
            event_data[event.value["module_id"]][event.value["event_id"]].append(attributes)

        # require contract event parsing
        if "Contracts" in event_data:
            for ix, event in enumerate(event_data["Contracts"].get("ContractEmitted", [])):
                for contract in get_supported_contracts():
                    data = ScaleBytes(event["raw_data"])
                    try:
                        decoded_event = ContractEvent(
                            data=data,
                            contract_metadata=contract.metadata,
                            runtime_config=self.substrate_interface.runtime_config,
                        ).decode()
                    except Exception:  # noqa E722
                        continue
                    contract = event_data["Contracts"]["ContractEmitted"][ix]["contract"]
                    event_data["Contracts"]["ContractEmitted"][ix] = decoded_event
                    event_data["Contracts"]["ContractEmitted"][ix]["contract"] = contract

        block_attrs = {
            "number": block_data["header"]["number"],
            "hash": block_data["header"]["hash"],
            "parent_hash": block_data["header"]["parentHash"],
            "event_data": event_data,
            "extrinsic_data": extrinsic_data,
        }

        try:
            return models.Block.objects.get(hash=block_data["header"]["hash"])
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
        slack_logger.info("DB and chain are out of sync! Recreating DB...")
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

        last_block = models.Block.objects.filter(executed=False).order_by("number").first()
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
                slack_logger.exception(f"Block not executable. number: {last_block.number} | hash: {last_block.hash}")
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

SUPPORTED_CONTRACTS = [
    "genesis_dao_contract",
    "dao_asset_contract",
    "vesting_wallet_contract",
    "vote_escrow_contract",
]


def get_supported_contracts() -> List[ContractCode]:
    contracts = []

    for contract_str in SUPPORTED_CONTRACTS:
        contract_code = ContractCode.create_from_contract_files(
            wasm_file=f"{settings.BASE_DIR}/wasm/{contract_str}.wasm",
            metadata_file=f"{settings.BASE_DIR}/wasm/{contract_str}.json",
            substrate=substrate_service.substrate_interface,
        )
        contracts.append(contract_code)
    return contracts


def contract_address(deploying_address, code_hash, input_data, salt):
    """
    Computes the address of the contract.

    :param deploying_address: The address of the deploying account.
    :param code_hash: The hash of the contract's code.
    :param input_data: The input data for the contract creation.
    :param salt: A salt value for the contract creation.
    :return: The account address.
    """
    concatenated_inputs = (
        b"contract_addr_v1" +
        deploying_address +
        code_hash +
        input_data +
        salt
    )

    # Compute the H256 hash using keccak
    return hashlib.blake2b(concatenated_inputs, digest_size=32).hexdigest()

