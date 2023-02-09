from django.core.management import BaseCommand

from core.substrate import substrate_service


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        substrate_service.listen()
