import time
import logging

from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Auto-pilot loop: scrapes, locks, grades, and advances the week on schedule.'

    def handle(self, *args, **options):
        from main.auto import auto_tick
        from main.models import SiteSettings
        from main import inbound_email

        log.info('[run_auto] auto-pilot started')
        while True:
            try:
                # Deliberately outside auto_tick(): that returns immediately when
                # auto_enabled is off, and league mail should still be collected
                # by a league running its weeks by hand. Self-contained and
                # best-effort, so a mailbox outage cannot stall the tick.
                inbound_email.fetch()
                auto_tick()
                interval = SiteSettings.get().tick_interval or 300
            except Exception:
                log.exception('[run_auto] tick error')
                interval = 60
            time.sleep(max(10, interval))
