import base64

from django.core.cache import cache
from substrateinterface import ContractInstance
from substrateinterface.keypair import Keypair

from core.substrate import substrate_service

# mnemonic to generate a keypair
# e.g. Keypair.generate_mnemonic()
MY_MNEM = "slam race banana carbon minor yellow machine great peace humble sorry spoon"
UNIT = 1_000_000_000_000  # polkadots native token unit
KILO_UNIT = UNIT * 1000
MEGA_UNIT = KILO_UNIT * 1000
KEYPAIR_ALICE = Keypair.create_from_uri("//Alice")
KEYPAIR_BOB = Keypair.create_from_uri("//Bob")
KEYPAIR_ME = Keypair.create_from_mnemonic(MY_MNEM)
MY_ADDR = KEYPAIR_ME.ss58_address
CONTRACT_BASE_PATH = "/absolute/path/to/genesis-dao-node/target/ink/"
DAO_ID = "DAO1"
ASSET_ID = 1  # dao asset id, obtained by e.g.: running setup_dao() and checking the core_asset table
# obtained from running deploy_contracts()
GENESIS_DAO_CONTRACT_ADDRESS = "..."
DAO_ASSETS_CONTRACT_ADDRESS = "..."
VESTING_WALLET_CONTRACT_ADDRESS = "..."


def print_storage_funcs():
    for func in substrate_service.substrate_interface.get_metadata_storage_functions():
        docs = (func["documentation"] or "").replace("\n", " ")
        print(f"module: {func['module_name']} | storage_func: {func['storage_name']} | docs: {docs}")


def example_query_map():
    print("accounts:")
    # you can use print_storage_funcs to get module and storage_function
    result = substrate_service.substrate_interface.query_map(module="System", storage_function="Account")
    for obj, data in result:
        print(f"{obj.value}: {data.value['data']['free'] / MEGA_UNIT} MUNIT")


def print_call_funcs():
    for func in substrate_service.substrate_interface.get_metadata_call_functions():
        print(f"module: {func['module_name']} | {func['call_name']} | args: {func['call_args']}")


def multisig_example():
    """
    create multisig acc for our 3 users
    transfer ownership of the dao to it
    create call to transfer ownership back to us
    approve by all 3 users
    """
    signatories = [KEYPAIR_ME.ss58_address, KEYPAIR_ALICE.ss58_address, KEYPAIR_BOB.ss58_address]
    multisig_acc = substrate_service.create_multisig_account(signatories=signatories, threshold=3)
    substrate_service.transfer_dao_ownership(
        DAO_ID=DAO_ID, new_owner_id=multisig_acc.ss58_address, keypair=KEYPAIR_ME, wait_for_inclusion=True
    )
    dao_to_me = substrate_service.substrate_interface.compose_call(
        call_module="DaoCore",
        call_function="change_owner",
        call_params={"DAO_ID": DAO_ID, "new_owner": KEYPAIR_ME.ss58_address},
    )
    substrate_service.approve_multisig(
        multisig_account=multisig_acc, call=dao_to_me, keypair=KEYPAIR_ALICE, wait_for_inclusion=True
    )
    substrate_service.approve_multisig(
        multisig_account=multisig_acc, call=dao_to_me, keypair=KEYPAIR_BOB, wait_for_inclusion=True
    )
    substrate_service.approve_multisig(
        multisig_account=multisig_acc, call=dao_to_me, keypair=KEYPAIR_ME, wait_for_inclusion=True
    )


def multisig_batch_example():
    """
    same as multisig_example but using batch
    """
    signatories = [KEYPAIR_ME.ss58_address, KEYPAIR_ALICE.ss58_address, KEYPAIR_BOB.ss58_address]
    multisig_acc = substrate_service.create_multisig_account(signatories=signatories, threshold=3)
    dao_to_me = substrate_service.substrate_interface.compose_call(
        call_module="DaoCore",
        call_function="change_owner",
        call_params={"DAO_ID": DAO_ID, "new_owner": KEYPAIR_ME.ss58_address},
    )
    substrate_service.batch_as_multisig(
        calls=[dao_to_me], multisig_account=multisig_acc, keypair=KEYPAIR_ALICE, wait_for_inclusion=True
    )
    substrate_service.batch_as_multisig(
        calls=[dao_to_me], multisig_account=multisig_acc, keypair=KEYPAIR_BOB, wait_for_inclusion=True
    )
    substrate_service.batch_as_multisig(
        calls=[dao_to_me], multisig_account=multisig_acc, keypair=KEYPAIR_ME, wait_for_inclusion=True
    )


def signature_example():
    challenge = "some challenge"  # e.g. from dao/challenge
    cache.set(MY_ADDR, challenge)
    signed = base64.b64encode(KEYPAIR_ME.sign(challenge)).decode()
    print(substrate_service.verify(address=MY_ADDR, challenge_address=MY_ADDR, signature=signed))
    cache.delete(MY_ADDR)


def setup_dao():
    """
    gives your address some balance, creates a dao, issues a token and set the dao's governance
    """
    substrate_service.set_balance_deprecated(MY_ADDR, UNIT * 1000, 0, keypair=KEYPAIR_ALICE, wait_for_inclusion=True)
    print("balance set")
    substrate_service.create_dao(dao_id=DAO_ID, dao_name="dao1 name", keypair=KEYPAIR_ME, wait_for_inclusion=True)
    print(f"{DAO_ID}: created")
    substrate_service.issue_token(dao_id=DAO_ID, amount=1000, keypair=KEYPAIR_ME, wait_for_inclusion=True)
    print(f"{DAO_ID}: token issued")
    substrate_service.set_governance_majority_vote(
        dao_id=DAO_ID,
        proposal_duration=100,
        proposal_token_deposit=50,
        minimum_majority_per_1024=50,
        keypair=KEYPAIR_ME,
        wait_for_inclusion=True,
    )
    print(f"{DAO_ID}: set governance to majority vote")


def deploy_contracts():
    """
    deploys genesis_dao_contact, dao_assets_contact and vesting_wallet_contact and prints their addresses
    """
    genesis_dao_contract_addr = substrate_service.deploy_contract(
        contract_base_path=CONTRACT_BASE_PATH,
        contract_name="genesis_dao_contract",
        keypair=KEYPAIR_ME,
    ).contract_address
    print(f'GENESIS_DAO_CONTRACT_ADDRESS = "{genesis_dao_contract_addr}"')
    dao_assets_contract_addr = substrate_service.deploy_contract(
        contract_base_path=CONTRACT_BASE_PATH,
        contract_name="dao_assets_contract",
        keypair=KEYPAIR_ME,
        contract_constructor_args={"asset_id": ASSET_ID},
    ).contract_address
    print(f'DAO_ASSETS_CONTRACT_ADDRESS = "{dao_assets_contract_addr}"')
    vesting_wallet_contract_addr = substrate_service.deploy_contract(
        contract_base_path=CONTRACT_BASE_PATH,
        contract_name="vesting_wallet_contract",
        keypair=KEYPAIR_ME,
        contract_constructor_args={"token": dao_assets_contract_addr},
    ).contract_address
    print(f'VESTING_WALLET_CONTRACT_ADDRESS = "{vesting_wallet_contract_addr}"')


def create_vesting_wallet():
    # todo @chp
    # run setup_dao()
    # set ASSET_ID var if necessary
    # run deploy_contracts()
    # set set ..._CONTRACT_ADDRESS vars

    dao_assets_contract = ContractInstance.create_from_address(
        contract_address=DAO_ASSETS_CONTRACT_ADDRESS,
        metadata_file=f"{CONTRACT_BASE_PATH}dao_assets_contract/dao_assets_contract.json",
        substrate=substrate_service.substrate_interface,
    )
    vesting_wallet_contract = ContractInstance.create_from_address(
        contract_address=VESTING_WALLET_CONTRACT_ADDRESS,
        metadata_file=f"{CONTRACT_BASE_PATH}vesting_wallet_contract/vesting_wallet_contract.json",
        substrate=substrate_service.substrate_interface,
    )
    receipt = dao_assets_contract.exec(
        method="PSP22::approve",
        args={
            "spender": VESTING_WALLET_CONTRACT_ADDRESS,
            "value": 123123123,
        },
        # if gas_limit is omitted exec tries to predict it, which fails.
        # gas_required is already set tho, so i used that value
        gas_limit={"proof_size": 53793, "ref_time": 2764148614},
        keypair=KEYPAIR_ME,
    )
    print(receipt.is_success or receipt.error_message)
    receipt = vesting_wallet_contract.exec(
        method="create_vesting_wallet_for",
        args={
            "account": KEYPAIR_BOB.ss58_address,
            "amount": 123,
            "duration": 234,
        },
        gas_limit={"proof_size": 72337, "ref_time": 7846201261},
        keypair=KEYPAIR_ME,
    )
    print(receipt.is_success or receipt.error_message)
