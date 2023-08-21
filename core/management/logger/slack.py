import logging

import requests
from django import conf
from django.conf import settings


class SlackHandler(logging.Handler):
    """
    A log handler to publish to registered webhooks
    """

    url = conf.settings.SLACK_DEFAULT_URL

    def emit(self, record: logging.LogRecord):
        if not (url := getattr(record, "channel", None) or self.url):
            return

        if getattr(record, "disable_formatting", None):
            txt = f"{record.msg}"
        else:
            txt = f"*{record.levelname}*:\n```{record.msg}```"
        if record.exc_info:
            txt += f"\n*Traceback*:\n```{self.format(record=record).lstrip(record.msg)}```"

        txt += "\n*Config*:\n"
        config = {
            "BLOCKCHAIN_URL": settings.BLOCKCHAIN_URL,
            "APPLICATION_STAGE": settings.APPLICATION_STAGE,
        }
        json = {
            "text": txt,
            "attachments": [{"fields": [{"title": k, "value": v} for k, v in config.items()]}],
        }

        requests.post(url, json=json, headers={"Content-Type": "application/json"})
