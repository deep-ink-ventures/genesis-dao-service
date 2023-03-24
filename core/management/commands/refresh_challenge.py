import logging
import secrets
import time

from django.conf import settings
from django.core.management import BaseCommand

from core import models as core_models

logger = logging.getLogger("alerts")


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        logger.info("Challenge refresher started.")
        try:
            challenge = core_models.Challenge.objects.get()
        except core_models.Challenge.DoesNotExist:
            challenge = core_models.Challenge()

        while True:
            start_time = time.time()
            while (new_key := secrets.token_hex(64)) == challenge.key:
                pass
            challenge.key = new_key
            challenge.save()
            elapsed_time = time.time() - start_time
            if elapsed_time < settings.CHALLENGE_LIFETIME:
                time.sleep(settings.CHALLENGE_LIFETIME - elapsed_time)
