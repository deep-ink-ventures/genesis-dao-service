from unittest.mock import patch

from django.core.management import call_command

from core.tests.testcases import IntegrationTestCase


class CommandTest(IntegrationTestCase):
    @patch("core.substrate.substrate_service.sync_initial_accs")
    @patch("core.substrate.substrate_service.listen")
    def test_blockchain_event_listener(self, *mocks):
        call_command("blockchain_event_listener")

        for mock in mocks:
            mock.assert_called_once_with()
