import time
import logging
from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Auto-pilot loop: scrapes, locks, grades, and advances week on schedule.'

    def handle(self, *args, **options):
        self.stdout.write('[run_auto] Auto-pilot started.')
        while True:
            try:
                from main.auto import auto_tick
                from main.models import SiteSettings
                from main import sim as sim_module
                from main import inbound_email
                # Deliberately outside auto_tick(): that returns immediately when
                # auto_enabled is off, and league mail should still be collected
                # by a league running its weeks by hand. Self-contained and
                # best-effort, so a mailbox outage cannot stall the tick.
                inbound_email.fetch()
                auto_tick()
                interval = sim_module.get_tick_interval() or SiteSettings.get().tick_interval
            except Exception as exc:
                self.stderr.write(f'[run_auto] Tick error: {exc}')
                log.exception('auto_tick error')
                interval = 60
            time.sleep(interval)
