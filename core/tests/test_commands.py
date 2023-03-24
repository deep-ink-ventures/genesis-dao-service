from unittest.mock import patch

from django.core.management import call_command

from core import models as core_models
from core.tests.testcases import IntegrationTestCase


class CommandTest(IntegrationTestCase):
    @patch("core.substrate.substrate_service.sync_initial_accs")
    @patch("core.substrate.substrate_service.listen")
    def test_blockchain_event_listener(self, *mocks):
        call_command("blockchain_event_listener")

        for mock in mocks:
            mock.assert_called_once_with()

    @patch("core.management.commands.refresh_challenge.time.sleep")
    @patch("core.management.commands.refresh_challenge.logger")
    def test_refresh_challenge(self, logger_mock, sleep_mock):
        sleep_mock.side_effect = Exception("staph")
        with self.assertRaises(core_models.Challenge.DoesNotExist):  # noqa
            core_models.Challenge.objects.get()

        with self.assertRaisesMessage(Exception, "staph"):
            call_command("refresh_challenge")

        logger_mock.info.assert_called_once_with("Challenge refresher started.")
        self.assertIsNotNone(core_models.Challenge.objects.get().key)

    @patch("core.management.commands.refresh_challenge.secrets.token_hex")
    @patch("core.management.commands.refresh_challenge.time.sleep")
    @patch("core.management.commands.refresh_challenge.logger")
    def test_refresh_challenge_same_key(self, logger_mock, sleep_mock, token_hex_mock):
        sleep_mock.side_effect = Exception("staph")
        core_models.Challenge.objects.create(key="some_key")
        token_hex_mock.side_effect = "some_key", "new_key"

        with self.assertRaisesMessage(Exception, "staph"):
            call_command("refresh_challenge")

        logger_mock.info.assert_called_once_with("Challenge refresher started.")
        self.assertEqual(core_models.Challenge.objects.get().key, "new_key")
