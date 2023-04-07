from django.core.management import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        from core.substrate import substrate_service

        substrate_service.sync_initial_accs()  # todo check which data to preload
        substrate_service.listen()
