import base64
from unittest.mock import ANY, Mock, call, patch

from ddt import data, ddt
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from substrateinterface import Keypair
from websocket import WebSocketConnectionClosedException

from core import models
from core.substrate import (
    OutOfSyncException,
    SubstrateException,
    retry,
    substrate_service,
)
from core.tests.testcases import IntegrationTestCase


@ddt
class SubstrateServiceTest(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        self.substrate_service = substrate_service
        self.substrate_exception = SubstrateException
        self.oos_exception = OutOfSyncException
        self.si = self.substrate_service.substrate_interface = Mock()
        self.keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        self.retry_msg = "Unexpected error while fetching block from chain. Retrying in 0s ..."

    def assert_signed_extrinsic_submitted(self, keypair: object):
        self.si.create_signed_extrinsic.assert_called_once_with(call=self.si.compose_call(), keypair=keypair)
        self.si.submit_extrinsic.assert_called_once_with(
            extrinsic=self.si.create_signed_extrinsic(),
            wait_for_inclusion=False,
        )

    def test___exit__(self):
        self.substrate_service.__exit__(None, None, None)

        self.si.close.assert_called_once_with()

    @data(
        # exception type
        WebSocketConnectionClosedException,
        ConnectionRefusedError,
        BrokenPipeError,
        Exception,
    )
    @patch("core.substrate.logger")
    @patch("core.substrate.time.sleep")
    def test_retry(self, exception_type, sleep_mock, logger_mock):
        sleep_mock.side_effect = None, None, Exception("break retry")

        def _test():
            raise exception_type("roar")

        with override_settings(RETRY_DELAYS=(1, 2, 3)), self.assertRaisesMessage(Exception, "break retry"):
            retry("some description")(_test)()

        logger_mock = logger_mock.exception if exception_type == Exception else logger_mock.error
        error_description = "Unexpected error" if exception_type == Exception else exception_type.__name__
        logger_mock.assert_has_calls(
            [
                call(f"{error_description} while some description. Retrying in 1s ..."),
                call(f"{error_description} while some description. Retrying in 2s ..."),
                call(f"{error_description} while some description. Retrying in 3s ..."),
            ]
        )

    @patch("core.substrate.logger")
    def test_submit_extrinsic(self, logger_mock):
        extrinsic = Mock()
        receipt = Mock(is_success=False, error_message={"name": "some error"})
        self.si.submit_extrinsic.return_value = receipt

        self.substrate_service.submit_extrinsic(extrinsic=extrinsic, wait_for_inclusion=True)

        logger_mock.error.assert_called_once_with("Error during extrinsic submission: {'name': 'some error'}")

    def assert_blocks_equal(self, block_one: models.Block, block_two: models.Block):
        self.assertEqual(block_one.number, block_two.number)
        self.assertEqual(block_one.hash, block_two.hash)
        self.assertEqual(block_one.parent_hash, block_two.parent_hash)
        self.assertDictEqual(block_one.extrinsic_data, block_two.extrinsic_data)
        self.assertDictEqual(block_one.event_data, block_two.event_data)
        self.assertEqual(block_one.executed, block_two.executed)

    def test_retrieve_account_balance(self):
        account_address = "some_address"
        expected_balance = {"free": 1, "reserved": 2, "misc_frozen": 3, "fee_frozen": 4}
        self.si.query.return_value = Mock(value={"data": expected_balance})

        self.assertEqual(
            self.substrate_service.retrieve_account_balance(account_address=account_address), expected_balance
        )
        self.si.query.assert_called_once_with(module="System", storage_function="Account", params=[account_address])

    @patch("core.substrate.logger")
    def test_sync_initial_accs(self, logger_mock):
        self.si.query_map.return_value = (
            ("addr1", "ignored"),
            ("addr2", "ignored"),
        )

        self.substrate_service.sync_initial_accs()

        logger_mock.info.assert_called_once_with("Syncing initial accounts...")
        self.si.query_map.assert_called_once_with("System", "Account")
        self.assertCountEqual(
            models.Account.objects.all(),
            [
                models.Account(address="addr1"),
                models.Account(address="addr2"),
            ],
        )

    def test_create_dao(self):
        dao_id = "some id"
        dao_name = "some name"

        self.substrate_service.create_dao(dao_id=dao_id, dao_name=dao_name, keypair=self.keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="create_dao",
            call_params={"dao_id": dao_id, "dao_name": dao_name},
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_transfer_dao_ownership(self):
        dao_id = "some id"
        new_owner_id = "new id"

        self.substrate_service.transfer_dao_ownership(dao_id=dao_id, new_owner_id=new_owner_id, keypair=self.keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="change_owner",
            call_params={"dao_id": dao_id, "new_owner": new_owner_id},
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_destroy_dao(self):
        dao_id = "some id"

        self.substrate_service.destroy_dao(dao_id=dao_id, keypair=self.keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="destroy_dao",
            call_params={"dao_id": dao_id},
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_issue_tokens(self):
        dao_id = "some id"
        amount = 123

        self.substrate_service.issue_tokens(dao_id=dao_id, amount=amount, keypair=self.keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="issue_token",
            call_params={"dao_id": dao_id, "supply": amount},
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_transfer_asset(self):
        asset_id = "123"
        target = "some acc addr"
        amount = 321

        self.substrate_service.transfer_asset(asset_id=asset_id, target=target, amount=amount, keypair=self.keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="Assets",
            call_function="transfer",
            call_params={"id": asset_id, "target": target, "amount": amount},
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_transfer_balance(self):
        target = "some acc addr"
        value = 123

        self.substrate_service.transfer_balance(target=target, value=value, keypair=self.keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="Balances",
            call_function="transfer",
            call_params={"dest": target, "value": value},
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_set_balance(self):
        target = "some acc addr"
        new_free = 123
        new_reserved = 321

        self.substrate_service.set_balance(
            target=target, new_free=new_free, new_reserved=new_reserved, keypair=self.keypair
        )

        self.si.compose_call.assert_has_calls(
            [
                call(
                    call_module="Balances",
                    call_function="set_balance",
                    call_params={"who": target, "new_free": new_free, "new_reserved": new_reserved},
                ),
                call(
                    call_module="Sudo",
                    call_function="sudo",
                    call_params={"call": self.si.compose_call()},
                ),
            ]
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_dao_set_metadata(self):
        dao_id = "abc"
        metadata_url = "some_url"
        metadata_hash = "some_hash"

        self.substrate_service.dao_set_metadata(
            dao_id=dao_id, metadata_url=metadata_url, metadata_hash=metadata_hash, keypair=self.keypair
        )

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="set_metadata",
            call_params={"dao_id": dao_id, "meta": metadata_url, "hash": metadata_hash},
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_set_governance_majority_vote(self):
        dao_id = "abc"
        proposal_duration = 123
        proposal_token_deposit = 234
        minimum_majority_per_1024 = 345

        self.substrate_service.set_governance_majority_vote(
            dao_id=dao_id,
            proposal_duration=proposal_duration,
            proposal_token_deposit=proposal_token_deposit,
            minimum_majority_per_1024=minimum_majority_per_1024,
            keypair=self.keypair,
        )

        self.si.compose_call.assert_called_once_with(
            call_module="Votes",
            call_function="set_governance_majority_vote",
            call_params={
                "dao_id": dao_id,
                "proposal_duration": proposal_duration,
                "proposal_token_deposit": proposal_token_deposit,
                "minimum_majority_per_1024": minimum_majority_per_1024,
            },
        )
        self.assert_signed_extrinsic_submitted(keypair=self.keypair)

    def test_verify(self):
        challenge_token = "something_to_sign"
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=challenge_token, timeout=1)
        signature = base64.b64encode(keypair.sign(data=challenge_token)).decode()

        self.assertTrue(self.substrate_service.verify(address=keypair.ss58_address, signature=signature))

    def test_verify_fail(self):
        challenge_token = "something_to_sign"
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        cache.set(key=keypair.ss58_address, value=challenge_token, timeout=1)
        signature = "wrong"

        self.assertFalse(self.substrate_service.verify(address=keypair.ss58_address, signature=signature))

    def test_verify_no_key(self):
        challenge_token = "something_to_sign"
        keypair = Keypair.create_from_mnemonic(Keypair.generate_mnemonic())
        signature = base64.b64encode(keypair.sign(data=challenge_token)).decode()

        self.assertFalse(self.substrate_service.verify(address=keypair.ss58_address, signature=signature))

    def test_create_proposal(self):
        dao_id = "abc"
        proposal_id = "cba"
        metadata_url = "some_url"
        metadata_hash = "some_hash"
        keypair = object()

        self.substrate_service.create_proposal(
            dao_id=dao_id,
            proposal_id=proposal_id,
            metadata_url=metadata_url,
            metadata_hash=metadata_hash,
            keypair=keypair,  # noqa
        )

        self.si.compose_call.assert_called_once_with(
            call_module="Votes",
            call_function="create_proposal",
            call_params={"dao_id": dao_id, "proposal_id": proposal_id, "meta": metadata_url, "hash": metadata_hash},
        )
        self.assert_signed_extrinsic_submitted(keypair=keypair)

    @data(
        # block_data, event_data expected_block
        # 0 extrinsics, 0 events
        (
            {
                "not": "interesting",
                "header": {
                    "number": 0,
                    "hash": "block hash",
                    "parentHash": "parent hash",
                },
                "extrinsics": [],
            },
            [],
            models.Block(
                hash="block hash",
                number=0,
                parent_hash="parent hash",
                extrinsic_data={},
                event_data={},
            ),
        ),
        # 1 extrinsic, 0 events
        (
            {
                "not": "interesting",
                "header": {
                    "number": 0,
                    "hash": "block hash",
                    "parentHash": "parent hash",
                },
                "extrinsics": [
                    Mock(
                        value={
                            "call": {
                                "call_module": "call_module 1",
                                "call_function": "call_function 1",
                                "call_args": [
                                    {
                                        "name": "name arg 1",
                                        "value": "val arg 1",
                                    }
                                ],
                            }
                        }
                    )
                ],
            },
            [],
            models.Block(
                hash="block hash",
                number=0,
                parent_hash="parent hash",
                extrinsic_data={
                    "call_module 1": {
                        "call_function 1": [
                            {"name arg 1": "val arg 1"},
                        ]
                    }
                },
                event_data={},
            ),
        ),
        # 0 extrinsics, 1 event
        (
            {
                "not": "interesting",
                "header": {
                    "number": 0,
                    "hash": "block hash",
                    "parentHash": "parent hash",
                },
                "extrinsics": [],
            },
            [
                Mock(
                    value={
                        "module_id": "module_id 1",
                        "event_id": "event_id 1",
                        "attributes": {
                            "all": "the",
                            "keys": "and vals",
                        },
                    }
                )
            ],
            models.Block(
                hash="block hash",
                number=0,
                parent_hash="parent hash",
                extrinsic_data={},
                event_data={
                    "module_id 1": {
                        "event_id 1": [
                            {
                                "all": "the",
                                "keys": "and vals",
                            }
                        ]
                    }
                },
            ),
        ),
        # 3 extrinsics, 3 event
        (
            {
                "not": "interesting",
                "header": {
                    "number": 0,
                    "hash": "block hash",
                    "parentHash": "parent hash",
                },
                "extrinsics": [
                    Mock(
                        value={
                            "call": {
                                "call_module": "call_module 1",
                                "call_function": "call_function 1",
                                "call_args": [
                                    {
                                        "name": "name arg 1",
                                        "value": "val arg 1",
                                    }
                                ],
                            }
                        }
                    ),
                    Mock(
                        value={
                            "call": {
                                "call_module": "call_module 1",
                                "call_function": "call_function 1",
                                "call_args": [
                                    {
                                        "name": "name arg 1",
                                        "value": "val arg 1",
                                    }
                                ],
                            }
                        }
                    ),
                    Mock(
                        value={
                            "call": {
                                "call_module": "call_module 2",
                                "call_function": "call_function 2",
                                "call_args": [
                                    {
                                        "name": "name arg 2",
                                        "value": "val arg 3",
                                    }
                                ],
                            }
                        }
                    ),
                ],
            },
            [
                Mock(
                    value={
                        "module_id": "module_id 1",
                        "event_id": "event_id 1",
                        "attributes": {
                            "all": "the",
                            "keys": "and vals",
                        },
                    }
                ),
                Mock(
                    value={
                        "module_id": "module_id 1",
                        "event_id": "event_id 1",
                        "attributes": {
                            "all": "the",
                            "keys": "and vals",
                        },
                    }
                ),
                Mock(
                    value={
                        "module_id": "module_id 2",
                        "event_id": "event_id 2",
                        "attributes": {
                            "all": "the",
                            "keys": "and vals",
                        },
                    }
                ),
            ],
            models.Block(
                hash="block hash",
                number=0,
                parent_hash="parent hash",
                extrinsic_data={
                    "call_module 1": {
                        "call_function 1": [
                            {"name arg 1": "val arg 1"},
                            {"name arg 1": "val arg 1"},
                        ]
                    },
                    "call_module 2": {
                        "call_function 2": [
                            {
                                "name arg 2": "val arg 3",
                            }
                        ]
                    },
                },
                event_data={
                    "module_id 1": {
                        "event_id 1": [
                            {
                                "all": "the",
                                "keys": "and vals",
                            },
                            {
                                "all": "the",
                                "keys": "and vals",
                            },
                        ]
                    },
                    "module_id 2": {
                        "event_id 2": [
                            {
                                "all": "the",
                                "keys": "and vals",
                            }
                        ]
                    },
                },
            ),
        ),
    )
    def test_fetch_and_parse_block(self, input_data):
        block_data, event_data, expected_block = input_data
        self.si.get_block.return_value = block_data
        self.si.get_events.return_value = event_data

        with self.assertNumQueries(2):
            block = self.substrate_service.fetch_and_parse_block()

        self.si.get_block.assert_called_once_with(block_hash=None, block_number=None)
        self.si.get_events.assert_called_once_with(block_hash="block hash")
        self.assert_blocks_equal(block, expected_block)

    def test_fetch_and_parse_block_existing_block_number(self):
        block_data = {
            "not": "interesting",
            "header": {
                "number": 0,
                "hash": "block hash 1",
                "parentHash": None,
            },
            "extrinsics": [],
        }
        event_data = {}
        self.si.get_block.return_value = block_data
        self.si.get_events.return_value = event_data
        existing_block = models.Block.objects.create(
            number=0, hash="block hash", parent_hash=None, extrinsic_data={}, event_data={}
        )

        with self.assertNumQueries(2):
            block = self.substrate_service.fetch_and_parse_block(block_number=0)

        self.si.get_block.assert_not_called()
        self.assert_blocks_equal(block, existing_block)

        with self.assertNumQueries(4):
            block = self.substrate_service.fetch_and_parse_block(block_number=0, recreate=True)

        expected_block = models.Block(number=0, hash="block hash 1", parent_hash=None, extrinsic_data={}, event_data={})
        self.si.get_block.assert_called_once_with(block_hash=None, block_number=0)
        self.assert_blocks_equal(block, expected_block)

    def test_fetch_and_parse_block_existing_block_hash(self):
        block_data = {
            "not": "interesting",
            "header": {
                "number": 1,
                "hash": "block hash",
                "parentHash": "parent hash",
            },
            "extrinsics": [],
        }
        event_data = {}
        self.si.get_block.return_value = block_data
        self.si.get_events.return_value = event_data
        existing_block = models.Block.objects.create(
            number=0, hash="block hash", parent_hash=None, extrinsic_data={}, event_data={}
        )

        with self.assertNumQueries(2):
            block = self.substrate_service.fetch_and_parse_block(block_hash="block hash")

        self.si.get_block.assert_not_called()
        self.assert_blocks_equal(block, existing_block)

        with self.assertNumQueries(4):
            block = self.substrate_service.fetch_and_parse_block(block_hash="block hash", recreate=True)

        expected_block = models.Block(
            number=1, hash="block hash", parent_hash="parent hash", extrinsic_data={}, event_data={}
        )
        self.si.get_block_hash.assert_not_called()
        self.si.get_block.assert_called_once_with(block_hash="block hash", block_number=None)
        self.assert_blocks_equal(block, expected_block)

    def test_fetch_and_parse_block_hash_takes_priority(self):
        block_data = {
            "not": "interesting",
            "header": {
                "number": 1,
                "hash": "block hash",
                "parentHash": "parent hash",
            },
            "extrinsics": [],
        }
        event_data = {}
        self.si.get_block.return_value = block_data
        self.si.get_events.return_value = event_data

        with self.assertNumQueries(3):
            self.substrate_service.fetch_and_parse_block(block_hash="block hash", block_number=0)

        self.si.get_block_hash.assert_not_called()
        self.si.get_block.assert_called_once_with(block_hash="block hash", block_number=None)

    @patch("core.substrate.time.sleep")
    @patch("core.substrate.logger")
    def test_fetch_and_parse_block_error(self, logger_mock, sleep_mock):
        self.si.get_block.side_effect = Exception("whoops")
        sleep_mock.side_effect = Exception("break")

        with self.assertRaisesMessage(Exception, "break"):
            self.assertIsNone(self.substrate_service.fetch_and_parse_block())

        logger_mock.exception.assert_called_once_with(
            "Unexpected error while fetching block from chain. Retrying in 0s ..."
        )
        self.assertListEqual(list(models.Block.objects.all()), [])

    def test_fetch_and_parse_block_block_already_exists(self):
        models.Block.objects.create(number=1, hash="block hash")
        block_data = {
            "not": "interesting",
            "header": {
                "number": 1,
                "hash": "block hash",
                "parentHash": "parent hash",
            },
            "extrinsics": [],
        }
        event_data = {}
        self.si.get_block.return_value = block_data
        self.si.get_events.return_value = event_data

        with self.assertNumQueries(2), self.assertRaises(self.oos_exception):
            self.assertIsNone(self.substrate_service.fetch_and_parse_block())

    def test_fetch_and_parse_block_error_no_block_data(self):
        self.si.get_block.return_value = None

        with self.assertRaisesMessage(self.substrate_exception, "SubstrateInterface.get_block returned no data."):
            self.assertIsNone(self.substrate_service.fetch_and_parse_block())

        self.assertListEqual(list(models.Block.objects.all()), [])

    @patch("core.substrate.SubstrateService.sleep")
    @patch("core.substrate.SubstrateService.sync_initial_accs")
    @patch("core.substrate.logger")
    def test_clear_db(self, logger_mock, sync_initial_accs_mock, sleep_mock):
        models.Account.objects.create(address="acc1")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1")
        models.Asset.objects.create(id=1, owner_id="acc1", dao_id="dao1", total_supply=100)
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc1", balance=100)
        models.Proposal.objects.create(
            id="prop1", dao_id="dao1", metadata_url="url1", metadata_hash="hash1", metadata={"a": 1}
        )
        models.Governance.objects.create(
            dao_id="dao1",
            proposal_duration=1,
            proposal_token_deposit=2,
            minimum_majority=3,
            type=models.GovernanceType.MAJORITY_VOTE,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE;")

        with self.assertNumQueries(1):
            self.substrate_service.clear_db(start_time=1)

        sync_initial_accs_mock.assert_called_once_with()
        sleep_mock.assert_called_once_with(start_time=1)
        logger_mock.info.assert_called_once_with("DB and chain are out of sync! Recreating DB...")
        self.assertListEqual(list(models.Account.objects.all()), [])
        self.assertListEqual(list(models.Dao.objects.all()), [])
        self.assertListEqual(list(models.Asset.objects.all()), [])
        self.assertListEqual(list(models.AssetHolding.objects.all()), [])
        self.assertListEqual(list(models.Proposal.objects.all()), [])
        self.assertListEqual(list(models.Governance.objects.all()), [])

    @patch("core.substrate.SubstrateService.sleep")
    @patch("core.substrate.SubstrateService.sync_initial_accs")
    @patch("core.substrate.logger")
    def test_clear_db_no_start_time(self, logger_mock, sync_initial_accs_mock, sleep_mock):
        models.Account.objects.create(address="acc1")
        models.Dao.objects.create(id="dao1", name="dao1 name", owner_id="acc1")
        models.Asset.objects.create(id=1, owner_id="acc1", dao_id="dao1", total_supply=100)
        models.AssetHolding.objects.create(asset_id=1, owner_id="acc1", balance=100)
        models.Proposal.objects.create(
            id="prop1", dao_id="dao1", metadata_url="url1", metadata_hash="hash1", metadata={"a": 1}
        )
        models.Governance.objects.create(
            dao_id="dao1",
            proposal_duration=1,
            proposal_token_deposit=2,
            minimum_majority=3,
            type=models.GovernanceType.MAJORITY_VOTE,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE;")

        with self.assertNumQueries(1):
            self.substrate_service.clear_db()

        sync_initial_accs_mock.assert_called_once_with()
        sleep_mock.assert_not_called()
        logger_mock.info.assert_called_once_with("DB and chain are out of sync! Recreating DB...")
        self.assertListEqual(list(models.Account.objects.all()), [])
        self.assertListEqual(list(models.Dao.objects.all()), [])
        self.assertListEqual(list(models.Asset.objects.all()), [])
        self.assertListEqual(list(models.AssetHolding.objects.all()), [])
        self.assertListEqual(list(models.Proposal.objects.all()), [])
        self.assertListEqual(list(models.Governance.objects.all()), [])

    @patch("core.substrate.time.time")
    @patch("core.substrate.time.sleep")
    def test_sleep_longer_than_block_creation_interval(self, sleep_mock, time_mock):
        start_time = 10
        time_mock.return_value = start_time + settings.BLOCK_CREATION_INTERVAL

        self.substrate_service.sleep(start_time=start_time)
        sleep_mock.assert_not_called()

    @patch("core.substrate.time.time")
    @patch("core.substrate.time.sleep")
    def test_sleep_shorter_than_block_creation_interval(self, sleep_mock, time_mock):
        start_time = 10
        time_mock.return_value = start_time + settings.BLOCK_CREATION_INTERVAL - 1

        self.substrate_service.sleep(start_time=start_time)
        sleep_mock.called_once_with(1)

    @patch("core.substrate.time.time")
    @patch("core.substrate.substrate_event_handler.execute_actions")
    @patch("core.substrate.logger")
    def test_listen_last_block_not_executed_success(self, logger_mock, execute_actions_mock, time_mock):
        time_mock.side_effect = Exception("break")
        block = models.Block.objects.create(number=0, executed=False, hash="some hash")
        expected_msg = "Last Block was not executed. Retrying... number: 0 | hash: some hash"

        with self.assertRaisesMessage(Exception, "break"):
            self.substrate_service.listen()

        logger_mock.error.assert_called_once_with(expected_msg)
        execute_actions_mock.assert_called_once_with(block)

    @patch("core.substrate.substrate_event_handler.execute_actions")
    @patch("core.substrate.logger")
    def test_listen_last_block_not_executed_failure(self, logger_mock, execute_actions_mock):
        self.substrate_service.clear_db = Mock(side_effect=Exception("break"))
        execute_actions_mock.side_effect = Exception("failure")
        block = models.Block.objects.create(number=0, executed=False, hash="some hash")
        expected_msg = "Last Block was not executed. Retrying... number: 0 | hash: some hash"

        with self.assertRaisesMessage(Exception, "break"):
            self.substrate_service.listen()

        logger_mock.error.assert_called_once_with(expected_msg)
        execute_actions_mock.assert_called_once_with(block)

    @patch("core.substrate.time.sleep")
    @patch("core.substrate.logger")
    def test_listen_oos(self, logger_mock, sleep_mock):
        sleep_mock.side_effect = Exception("break retry")
        self.substrate_service.clear_db = Mock()
        models.Block.objects.create(number=0, hash="hash 0", executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 0, "hash": "new hash", "parentHash": None}, "extrinsics": []},
            Exception("break"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(Exception, "break retry"):
            self.substrate_service.listen()

        logger_mock.exception.assert_called_once_with(self.retry_msg)
        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 2)
        self.substrate_service.clear_db.assert_called_once_with(start_time=ANY)

    @patch("core.substrate.time.sleep")
    @patch("core.substrate.logger")
    def test_listen_last_block_greater_current_block(self, logger_mock, sleep_mock):
        sleep_mock.side_effect = Exception("break retry")
        self.substrate_service.clear_db = Mock()
        models.Block.objects.create(number=1, executed=True, hash="some hash")
        self.si.get_block.side_effect = (
            {"header": {"number": 0, "hash": "hash 0", "parentHash": None}, "extrinsics": []},
            Exception("break"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(Exception, "break retry"):
            self.substrate_service.listen()

        logger_mock.exception.assert_called_once_with(self.retry_msg)
        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 2)
        self.substrate_service.clear_db.assert_called_once_with(start_time=ANY)

    @patch("core.substrate.time.sleep")
    @patch("core.substrate.logger")
    def test_listen_empty_db(self, logger_mock, sleep_mock):
        sleep_mock.side_effect = Exception("break retry")
        self.si.get_block.side_effect = (
            {"header": {"number": 0, "hash": "hash 0", "parentHash": None}, "extrinsics": []},
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            Exception("break"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(Exception, "break retry"):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 4)
        logger_mock.exception.assert_called_once_with(self.retry_msg)
        logger_mock.info.assert_has_calls(
            [
                call("Processing latest block | number: 0 | hash: hash 0"),
                call("Processing latest block | number: 1 | hash: hash 1"),
                call("Processing latest block | number: 2 | hash: hash 2"),
            ]
        )
        expected_blocks = [
            models.Block(number=0, hash="hash 0", parent_hash=None, executed=True),
            models.Block(number=1, hash="hash 1", parent_hash="hash 0", executed=True),
            models.Block(number=2, hash="hash 2", parent_hash="hash 1", executed=True),
        ]
        self.assertModelsEqual(models.Block.objects.all(), expected_blocks)

    @patch("core.substrate.time.sleep")
    @patch("core.substrate.logger")
    def test_listen_in_sync(self, logger_mock, sleep_mock):
        sleep_mock.side_effect = Exception("break retry")
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            {"header": {"number": 3, "hash": "hash 3", "parentHash": "hash 2"}, "extrinsics": []},
            Exception("break"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(Exception, "break retry"):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 4)
        logger_mock.exception.assert_called_once_with(self.retry_msg)
        logger_mock.info.assert_has_calls(
            [
                call("Processing latest block | number: 1 | hash: hash 1"),
                call("Processing latest block | number: 2 | hash: hash 2"),
                call("Processing latest block | number: 3 | hash: hash 3"),
            ]
        )
        expected_blocks = [
            models.Block(number=0, hash="hash 0", parent_hash=None, executed=True),
            models.Block(number=1, hash="hash 1", parent_hash="hash 0", executed=True),
            models.Block(number=2, hash="hash 2", parent_hash="hash 1", executed=True),
            models.Block(number=3, hash="hash 3", parent_hash="hash 2", executed=True),
        ]
        self.assertModelsEqual(models.Block.objects.all(), expected_blocks)

    @patch("core.substrate.time.sleep")
    @patch("core.substrate.logger")
    def test_listen_catching_up(
        self,
        logger_mock,
        sleep_mock,
    ):
        sleep_mock.side_effect = None, None, None, Exception("break retry")  # 3 sleeps while catching up + 1 in retry
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 3, "hash": "hash 3", "parentHash": "hash 2"}, "extrinsics": []},
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            {"header": {"number": 4, "hash": "hash 4", "parentHash": "hash 3"}, "extrinsics": []},
            Exception("break"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(Exception, "break"):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls(
            [
                call(block_hash=None, block_number=None),
                call(block_hash=None, block_number=1),
                call(block_hash=None, block_number=2),
                call(block_hash=None, block_number=None),
            ]
        )
        logger_mock.exception.assert_called_once_with(self.retry_msg)
        logger_mock.info.assert_has_calls(
            [
                call("Catching up | number: 1"),
                call("Catching up | number: 2"),
                call("Catching up | number: 3"),
                call("Processing latest block | number: 4 | hash: hash 4"),
            ]
        )
        expected_blocks = [
            models.Block(number=0, hash="hash 0", parent_hash=None, executed=True),
            models.Block(number=1, hash="hash 1", parent_hash="hash 0", executed=True),
            models.Block(number=2, hash="hash 2", parent_hash="hash 1", executed=True),
            models.Block(number=3, hash="hash 3", parent_hash="hash 2", executed=True),
            models.Block(number=4, hash="hash 4", parent_hash="hash 3", executed=True),
        ]
        self.assertModelsEqual(models.Block.objects.all(), expected_blocks)

    @patch("core.substrate.time.sleep")
    @patch("core.substrate.logger")
    def test_listen_fetching_same_block_twice(self, logger_mock, sleep_mock):
        sleep_mock.side_effect = Exception("break retry")
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            Exception("break"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(Exception, "break retry"):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 4)
        logger_mock.exception.assert_called_once_with(self.retry_msg)
        logger_mock.info.assert_has_calls(
            [
                call("Processing latest block | number: 1 | hash: hash 1"),
                call("Waiting for new block | number 1 | hash: hash 1"),
                call("Processing latest block | number: 2 | hash: hash 2"),
            ]
        )
        expected_blocks = [
            models.Block(number=0, hash="hash 0", parent_hash=None, executed=True),
            models.Block(number=1, hash="hash 1", parent_hash="hash 0", executed=True),
            models.Block(number=2, hash="hash 2", parent_hash="hash 1", executed=True),
        ]
        self.assertModelsEqual(models.Block.objects.all(), expected_blocks)

    @patch("core.substrate.logger")
    @patch("core.substrate.time.sleep")
    def test_listen_sleep(self, sleep_mock, logger_mock):
        sleep_mock.side_effect = None, Exception("break retry")
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 0, "hash": "hash 0", "parentHash": None}, "extrinsics": []},
            Exception("break"),
        )
        self.si.get_events.return_value = []

        with self.assertRaisesMessage(Exception, "break retry"):
            self.substrate_service.listen()

        sleep_time = sleep_mock.call_args_list[0][0][0]
        self.assertLess(sleep_time, settings.BLOCK_CREATION_INTERVAL)
        self.assertGreaterEqual(sleep_time, settings.BLOCK_CREATION_INTERVAL - 0.01)
        logger_mock.assert_has_calls(
            [
                call.info("Waiting for new block | number 0 | hash: hash 0"),
                call.exception(self.retry_msg),
            ]
        )
