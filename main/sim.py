import threading
import time
import logging

log = logging.getLogger(__name__)

_sim_thread = None
_stop_event = threading.Event()
_status = {'running': False, 'step': 'idle', 'week': None, 'error': None}
_sim_tick_interval = None  # overrides SiteSettings.tick_interval while sim is running


def get_tick_interval():
    return _sim_tick_interval


def get_status():
    return dict(_status)


def start(lock_delay, grade_delay, advance_delay, year, tick_interval=None):
    global _sim_thread, _stop_event, _sim_tick_interval
    if _sim_thread and _sim_thread.is_alive():
        return False
    _stop_event = threading.Event()
    _sim_tick_interval = int(tick_interval) if tick_interval else None
    _sim_thread = threading.Thread(
        target=_run,
        args=(int(lock_delay), int(grade_delay), int(advance_delay), int(year), _stop_event),
        daemon=True,
    )
    _status.update({'running': True, 'step': 'starting', 'week': None, 'error': None})
    _sim_thread.start()
    return True


def stop():
    global _sim_tick_interval
    _stop_event.set()
    _sim_tick_interval = None
    _status.update({'running': False, 'step': 'stopped'})


def _wait(seconds, stop_event):
    for _ in range(seconds * 2):
        if stop_event.is_set():
            return False
        time.sleep(0.5)
    return True


def _run(lock_delay, grade_delay, advance_delay, year, stop_event):
    try:
        from .auto import do_scrape_and_publish, do_lock_picks, do_grade, do_advance_week, make_bot_picks
        from .models import SiteSettings, Game

        while not stop_event.is_set():
            settings = SiteSettings.get()
            _status['week'] = settings.week

            # Step 1: scrape + publish if not yet published
            if not settings.publish:
                _status['step'] = f'Week {settings.week}: scraping & publishing'
                do_scrape_and_publish(settings, year=year)
                settings.refresh_from_db()

            # Always ensure bots have picks (idempotent — skips games already picked)
            _status['step'] = f'Week {settings.week}: bot picks'
            make_bot_picks()

            if stop_event.is_set():
                break

            # Step 2: wait, then lock
            if not settings.lock_picks:
                _status['step'] = f'Week {settings.week}: locking in {lock_delay}s'
                if not _wait(lock_delay, stop_event):
                    break
                settings.refresh_from_db()
                _status['step'] = f'Week {settings.week}: locking picks'
                do_lock_picks(settings)
                settings.refresh_from_db()

            if stop_event.is_set():
                break

            # Step 3: grade all games (instant with past data)
            _status['step'] = f'Week {settings.week}: grading games'
            do_grade(settings, year=year)

            if stop_event.is_set():
                break

            # Step 4: wait before advancing (so you can see graded state)
            _status['step'] = f'Week {settings.week}: graded — advancing in {grade_delay}s'
            if not _wait(grade_delay, stop_event):
                break

            # Step 5: advance week
            settings.refresh_from_db()
            games = list(Game.objects.all())
            if games and all(g.graded for g in games):
                _status['step'] = f'Week {settings.week}: advancing'
                do_advance_week(settings)
            else:
                _status['step'] = f'Week {settings.week}: not all graded, stopping'
                break

            if stop_event.is_set():
                break

            # Step 6: wait before next week
            settings.refresh_from_db()
            _status['step'] = f'Week {settings.week}: next week in {advance_delay}s'
            if not _wait(advance_delay, stop_event):
                break

    except Exception as e:
        log.error('[sim] error: %s', e)
        _status.update({'running': False, 'step': 'error', 'error': str(e)})
        return

    global _sim_tick_interval
    _sim_tick_interval = None
    _status.update({'running': False, 'step': 'stopped' if stop_event.is_set() else 'finished'})
