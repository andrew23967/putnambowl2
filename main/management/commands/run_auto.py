import time
import logging
from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Auto-pilot loop: scrapes, locks, grades, and advances week on schedule.'

    def handle(self, *args, **options):
        self.stdout.write('[run_auto] Auto-pilot started — ticking every 5 minutes.')
        while True:
            try:
                from main.auto import auto_tick
                auto_tick()
            except Exception as exc:
                self.stderr.write(f'[run_auto] Tick error: {exc}')
                log.exception('auto_tick error')
            time.sleep(300)
