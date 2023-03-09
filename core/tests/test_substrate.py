from unittest.mock import Mock, call, patch

from ddt import data, ddt
from django.conf import settings
from django.test import override_settings

from core import models
from core.tests.testcases import IntegrationTestCase


@ddt
class SubstrateServiceTests(IntegrationTestCase):
    def setUp(self):
        super().setUp()

        with patch("substrateinterface.SubstrateInterface"):
            from core.substrate import (
                OutOfSyncException,
                SubstrateException,
                substrate_service,
            )
        self.substrate_exception = SubstrateException
        self.oos_exception = OutOfSyncException
        self.substrate_service = substrate_service
        self.si = self.substrate_service.substrate_interface = Mock()

    def test___exit__(self):
        self.substrate_service.__exit__(None, None, None)

        self.si.close.assert_called_once_with()

    def assert_signed_extrinsic_submitted(self, keypair: object):
        self.si.create_signed_extrinsic.assert_called_once_with(call=self.si.compose_call(), keypair=keypair)
        self.si.submit_extrinsic.assert_called_once_with(self.si.create_signed_extrinsic())

    def assert_blocks_equal(self, block_one: models.Block, block_two: models.Block):
        self.assertEqual(block_one.number, block_two.number)
        self.assertEqual(block_one.hash, block_two.hash)
        self.assertEqual(block_one.parent_hash, block_two.parent_hash)
        self.assertDictEqual(block_one.extrinsic_data, block_two.extrinsic_data)
        self.assertDictEqual(block_one.event_data, block_two.event_data)
        self.assertEqual(block_one.executed, block_two.executed)

    def test_sync_initial_accs(self):
        self.si.query_map.return_value = (
            ("addr1", "ignored"),
            ("addr2", "ignored"),
        )

        self.substrate_service.sync_initial_accs()

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
        keypair = object()

        self.substrate_service.create_dao(dao_id=dao_id, dao_name=dao_name, keypair=keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="create_dao",
            call_params={"dao_id": dao_id, "dao_name": dao_name},
        )
        self.assert_signed_extrinsic_submitted(keypair=keypair)

    def test_destroy_dao(self):
        dao_id = "some id"
        keypair = object()

        self.substrate_service.destroy_dao(dao_id=dao_id, keypair=keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="destroy_dao",
            call_params={"dao_id": dao_id},
        )
        self.assert_signed_extrinsic_submitted(keypair=keypair)

    def test_issue_tokens(self):
        dao_id = "some id"
        amount = 123
        keypair = object()

        self.substrate_service.issue_tokens(dao_id=dao_id, amount=amount, keypair=keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="issue_token",
            call_params={"dao_id": dao_id, "supply": amount},
        )
        self.assert_signed_extrinsic_submitted(keypair=keypair)

    def test_transfer_asset(self):
        asset_id = 123
        target = "some acc addr"
        amount = 321
        keypair = object()

        self.substrate_service.transfer_asset(asset_id=asset_id, target=target, amount=amount, keypair=keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="Assets",
            call_function="transfer",
            call_params={"id": asset_id, "target": target, "amount": amount},
        )
        self.assert_signed_extrinsic_submitted(keypair=keypair)

    def test_transfer_balance(self):
        target = "some acc addr"
        value = 123
        keypair = object()

        self.substrate_service.transfer_balance(target=target, value=value, keypair=keypair)

        self.si.compose_call.assert_called_once_with(
            call_module="Balances",
            call_function="transfer",
            call_params={"dest": target, "value": value},
        )
        self.assert_signed_extrinsic_submitted(keypair=keypair)

    def test_set_balance(self):
        target = "some acc addr"
        new_free = 123
        new_reserved = 321
        keypair = object()

        self.substrate_service.set_balance(target=target, new_free=new_free, new_reserved=new_reserved, keypair=keypair)

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
        self.assert_signed_extrinsic_submitted(keypair=keypair)

    def test_dao_set_metadata(self):
        dao_id = 123
        metadata_url = "some_url"
        metadata_hash = "some_hash"
        keypair = object()

        self.substrate_service.dao_set_metadata(
            dao_id=dao_id, metadata_url=metadata_url, metadata_hash=metadata_hash, keypair=keypair
        )

        self.si.compose_call.assert_called_once_with(
            call_module="DaoCore",
            call_function="set_metadata",
            call_params={"dao_id": dao_id, "meta": metadata_url, "hash": metadata_hash},
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

    @patch("core.substrate.logger")
    def test_fetch_and_parse_error(self, logger_mock: Mock):
        self.si.get_block.side_effect = Exception("whoops")

        with self.assertRaisesMessage(self.substrate_exception, "Error while fetching block from chain."):
            self.assertIsNone(self.substrate_service.fetch_and_parse_block())

        logger_mock.exception.assert_called_once_with("Error while fetching block from chain.")
        self.assertListEqual(list(models.Block.objects.all()), [])

    @patch("core.substrate.logger")
    def test_fetch_and_parse_block_block_already_exists(self, logger_mock: Mock):
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

    def test_fetch_and_parse_error_no_block_data(self):
        self.si.get_block.return_value = None

        with self.assertRaisesMessage(self.substrate_exception, "SubstrateInterface.get_block returned no data."):
            self.assertIsNone(self.substrate_service.fetch_and_parse_block())

        self.assertListEqual(list(models.Block.objects.all()), [])

    @patch("core.substrate.logger")
    def test_listen_last_block_not_executed(self, logger_mock: Mock):
        models.Block.objects.create(number=0, executed=False, hash="some hash")
        expected_msg = "Last Block was not executed! number: 0 | hash: some hash"

        with self.assertRaisesMessage(self.substrate_exception, expected_msg):
            self.substrate_service.listen()

        logger_mock.error.assert_called_once_with(expected_msg)
        self.si.get_block.assert_not_called()

    @patch("core.substrate.logger")
    def test_listen_unrecoverably_out_of_sync(self, logger_mock: Mock):
        models.Block.objects.create(number=1, executed=True, hash="some hash")
        self.si.get_block.side_effect = (
            {"header": {"number": 0, "hash": "hash 0", "parentHash": None}, "extrinsics": []},
        )
        self.si.get_events.return_value = []
        expected_msg = "DB and chain are unrecoverably out of sync!"

        with self.assertRaisesMessage(self.oos_exception, expected_msg):
            self.substrate_service.listen()

        self.si.get_block.assert_called_once_with(block_hash=None, block_number=None)
        logger_mock.error.assert_called_once_with(expected_msg)

    @patch("core.substrate.logger")
    def test_listen_empty_db(self, logger_mock: Mock):
        self.si.get_block.side_effect = (
            {"header": {"number": 0, "hash": "hash 0", "parentHash": None}, "extrinsics": []},
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            Exception("stop loop"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(
            Exception, expected_message="Error while fetching block from chain."
        ):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 4)
        logger_mock.exception.assert_called_once_with("Error while fetching block from chain.")
        logger_mock.info.assert_has_calls(
            [
                call("processing latest block | number: 0 | hash: hash 0"),
                call("processing latest block | number: 1 | hash: hash 1"),
                call("processing latest block | number: 2 | hash: hash 2"),
            ]
        )
        expected_blocks = [
            models.Block(number=0, hash="hash 0", parent_hash=None, executed=True),
            models.Block(number=1, hash="hash 1", parent_hash="hash 0", executed=True),
            models.Block(number=2, hash="hash 2", parent_hash="hash 1", executed=True),
        ]
        self.assertModelsEqual(models.Block.objects.all(), expected_blocks)

    @patch("core.substrate.logger")
    def test_listen_in_sync(self, logger_mock: Mock):
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            {"header": {"number": 3, "hash": "hash 3", "parentHash": "hash 2"}, "extrinsics": []},
            Exception("stop loop"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(
            Exception, expected_message="Error while fetching block from chain."
        ):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 4)
        logger_mock.exception.assert_called_once_with("Error while fetching block from chain.")
        logger_mock.info.assert_has_calls(
            [
                call("processing latest block | number: 1 | hash: hash 1"),
                call("processing latest block | number: 2 | hash: hash 2"),
                call("processing latest block | number: 3 | hash: hash 3"),
            ]
        )
        expected_blocks = [
            models.Block(number=0, hash="hash 0", parent_hash=None, executed=True),
            models.Block(number=1, hash="hash 1", parent_hash="hash 0", executed=True),
            models.Block(number=2, hash="hash 2", parent_hash="hash 1", executed=True),
            models.Block(number=3, hash="hash 3", parent_hash="hash 2", executed=True),
        ]
        self.assertModelsEqual(models.Block.objects.all(), expected_blocks)

    @patch("core.substrate.logger")
    def test_listen_catching_up(self, logger_mock: Mock):
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 3, "hash": "hash 3", "parentHash": "hash 2"}, "extrinsics": []},
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            {"header": {"number": 4, "hash": "hash 4", "parentHash": "hash 3"}, "extrinsics": []},
            Exception("stop loop"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(
            Exception, expected_message="Error while fetching block from chain."
        ):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls(
            [
                call(block_hash=None, block_number=None),
                call(block_hash=None, block_number=1),
                call(block_hash=None, block_number=2),
                call(block_hash=None, block_number=None),
            ]
        )
        logger_mock.exception.assert_called_once_with("Error while fetching block from chain.")
        logger_mock.info.assert_has_calls(
            [
                call("catching up | number: 1"),
                call("catching up | number: 2"),
                call("catching up | number: 3"),
                call("processing latest block | number: 4 | hash: hash 4"),
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

    @patch("core.substrate.logger")
    def test_listen_fetching_same_block_twice(self, logger_mock):
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 1, "hash": "hash 1", "parentHash": "hash 0"}, "extrinsics": []},
            {"header": {"number": 2, "hash": "hash 2", "parentHash": "hash 1"}, "extrinsics": []},
            Exception("stop loop"),
        )
        self.si.get_events.return_value = []

        with override_settings(BLOCK_CREATION_INTERVAL=0), self.assertRaisesMessage(
            Exception, expected_message="Error while fetching block from chain."
        ):
            self.substrate_service.listen()

        self.si.get_block.assert_has_calls([call(block_hash=None, block_number=None)] * 4)
        logger_mock.exception.assert_called_once_with("Error while fetching block from chain.")
        logger_mock.info.assert_has_calls(
            [
                call("processing latest block | number: 1 | hash: hash 1"),
                call("waiting for new block | number 1 | hash: hash 1"),
                call("processing latest block | number: 2 | hash: hash 2"),
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
        models.Block.objects.create(number=0, hash="hash 0", parent_hash=None, executed=True)
        self.si.get_block.side_effect = (
            {"header": {"number": 0, "hash": "hash 0", "parentHash": None}, "extrinsics": []},
            Exception("stop loop"),
        )
        self.si.get_events.return_value = []

        with self.assertRaisesMessage(Exception, expected_message="Error while fetching block from chain."):
            self.substrate_service.listen()

        sleep_time = sleep_mock.call_args_list[0][0][0]
        self.assertLess(sleep_time, settings.BLOCK_CREATION_INTERVAL)
        self.assertGreaterEqual(sleep_time, settings.BLOCK_CREATION_INTERVAL - 0.01)
        logger_mock.assert_has_calls(
            [
                call.info("waiting for new block | number 0 | hash: hash 0"),
                call.exception("Error while fetching block from chain."),
            ]
        )
