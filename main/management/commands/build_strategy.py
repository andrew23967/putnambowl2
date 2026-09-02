"""Rebuild the saved strategy report.

Run after a season finishes being graded; the page reads the saved file and does
no work of its own.

    python manage.py build_strategy
"""
import time

from django.core.management.base import BaseCommand

from main import strategy_report


class Command(BaseCommand):
    help = 'Run the strategy simulation and save its results for the /strategy/ page.'

    def handle(self, *args, **options):
        cfg = (f'{strategy_report.YEARS[0]}-{strategy_report.YEARS[-1]}, '
               f'{strategy_report.N_TRIALS} trials')
        self.stdout.write(f'Running simulation ({cfg})…')

        started = time.time()
        report, errors = strategy_report.build()
        for e in errors:
            self.stderr.write(self.style.WARNING(f'  {e}'))

        if report is None:
            self.stderr.write(self.style.ERROR('Nothing to save — no games loaded.'))
            return

        path = strategy_report.save(report)
        self.stdout.write(self.style.SUCCESS(
            f'Saved {report["total_games"]} games across {len(report["year_counts"])} '
            f'seasons to {path} in {time.time() - started:.1f}s'
        ))
