import logging
from unittest.mock import ANY, patch

from django.conf import settings

from core.tests.testcases import UnitTestCase


class LoggerTest(UnitTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.slack_logger = logging.getLogger("alerts.slack")
        self.slack_logger.propagate = False
        self.attachments = [
            {
                "fields": [
                    {
                        "title": "BLOCKCHAIN_URL",
                        "value": settings.BLOCKCHAIN_URL,
                    },
                    {
                        "title": "APPLICATION_STAGE",
                        "value": settings.APPLICATION_STAGE,
                    },
                ]
            }
        ]

    @patch("core.management.logger.slack.requests.post")
    def test_slack_logger_no_url(self, post_mock):
        handler = self.slack_logger.handlers[0]
        handler.url = None
        self.slack_logger.handlers = [handler]

        self.assertIsNone(self.slack_logger.info("testing"))

        post_mock.assert_not_called()

    @patch("core.management.logger.slack.requests.post")
    def test_slack_logger_emit_info(self, post_mock):
        self.slack_logger.info("testing")

        post_mock.assert_called_once_with(
            settings.SLACK_DEFAULT_URL,
            json={
                "text": "*INFO*:\n```testing```\n*Config*:\n",
                "attachments": self.attachments,
            },
            headers={"Content-Type": "application/json"},
        )

    @patch("core.management.logger.slack.requests.post")
    def test_slack_logger_emit_exception(self, post_mock):
        try:
            raise Exception("Test Exception")
        except:  # noqa
            self.slack_logger.exception("testing")

        call_kwargs = post_mock.call_args.kwargs["json"]["text"]
        self.assertIn(
            "*ERROR*:\n```testing```\n*Traceback*:\n```\nTraceback (most recent call last):\n  File", call_kwargs
        )
        self.assertIn('raise Exception("Test Exception")\nException: Test Exception```\n*Config*:\n', call_kwargs)
        post_mock.assert_called_once_with(
            settings.SLACK_DEFAULT_URL,
            json={
                "text": ANY,
                "attachments": self.attachments,
            },
            headers={"Content-Type": "application/json"},
        )

    @patch("core.management.logger.slack.requests.post")
    def test_slack_logger_disable_formatting(self, post_mock):
        self.slack_logger.info("testing", extra={"disable_formatting": True})

        post_mock.assert_called_once_with(
            settings.SLACK_DEFAULT_URL,
            json={
                "text": "testing\n*Config*:\n",
                "attachments": self.attachments,
            },
            headers={"Content-Type": "application/json"},
        )

    @patch("core.management.logger.slack.requests.post")
    def test_slack_logger_channel(self, post_mock):
        self.slack_logger.info("testing", extra={"channel": "some channel"})

        post_mock.assert_called_once_with(
            "some channel",
            json={
                "text": "*INFO*:\n```testing```\n*Config*:\n",
                "attachments": self.attachments,
            },
            headers={"Content-Type": "application/json"},
        )
