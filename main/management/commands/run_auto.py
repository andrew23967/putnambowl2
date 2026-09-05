import logging
import time

from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Auto-pilot loop: scrapes, locks, grades, and advances every active league on schedule.'

    def handle(self, *args, **options):
        from main.auto import tick_all_leagues

        log.info('[run_auto] auto-pilot started')
        while True:
            try:
                interval = tick_all_leagues()
            except Exception:
                log.exception('[run_auto] tick error')
                interval = 60
            time.sleep(max(10, interval))
