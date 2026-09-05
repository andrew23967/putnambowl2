# Development

```bash
python -m venv .venv && .venv\Scripts\activate      # Python 3.13
pip install -r requirements.txt
copy .env.example .env                              # defaults are fine locally
python manage.py migrate
python manage.py createsuperuser                    # league-less; lands on /leagues/
python manage.py runserver
python manage.py run_auto                           # in a second terminal
```

The `putnambowl` league exists after `migrate` (seeded by `leagues/0002`). To
join it locally, create an account with its join code (shown on `/leagues/`),
or make yourself its manager from `/leagues/putnambowl/`.

## Tests

```bash
python manage.py test main accounts leagues   # ~75s
python manage.py makemigrations --check --dry-run
python -m pyflakes main/*.py leagues/*.py accounts/*.py config/*.py   # optional
```

Helpers at the top of `main/tests.py`: `default_league()`, `make_league(slug)`,
`make_member(username, ..., league=, role=)` (every test user needs a league or
every page 403s), `make_game(week, ..., league=)`. Stubs for the autopilot's
collaborators take `*a, **kw` because most functions now receive a league.

`NavPageRenderTests` GETs every nav-reachable page as a signed-in manager and
compiles every template under `templates/`; add new pages to `PAGES`.
`manage.py check` compiles no templates, and a `{% url %}` for a deleted route
is a render-time 500.

Under test the static storage is the plain one (`config/settings.py`); the
manifest storage needs `collectstatic` and would fail every page render in a
fresh checkout. Run `python manage.py collectstatic --no-input` once before
trusting a `{% static %}` path.

## Checking a page by eye

Run the server and open it. For a mid-season scenario on a scratch copy of the
database, write a short script against the ORM (create games for weeks 1–3
with picks and `WeeklyLeaderboard` rows, publish week 4) rather than adding a
seed command to the repo — v2's seed commands rotted and one broke on import.
Playwright screenshots at 1000px and 390px are the quickest way to compare
against the mocks in the design zip.

## Conventions

- Logging, not `print`. `LOGGING` in settings sends `main`, `accounts`,
  `leagues` to the console at INFO; keep messages ASCII.
- Views take `settings = current_settings(request)`; helpers take `league` or
  `settings`; nothing reads a league from global state.
- Do not use `runserver --noreload`: Django 6 caches templates without the reloader.
- Multi-line `{# #}` renders as text; use `{% comment %}`.
