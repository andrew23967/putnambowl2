# Deployment (Railway)

Project `putnambowl`, environment `production`, three services: **web**,
**putnambowl2** (the worker: `SERVICE_TYPE=worker`), **Postgres**. Pushing `main` deploys both
app services.

`railway.toml` is the only process definition (the old `Procfile` disagreed
with it and is gone): `migrate` in `preDeployCommand`, then the start command
branches on `SERVICE_TYPE` — the worker runs `manage.py run_auto`, the web
service runs `collectstatic` then gunicorn. `collectstatic` must run in the
serving container: preDeploy is a throwaway container and its output is
discarded, which once 500'd every page with `No directory at /app/staticfiles/`.
Both services run preDeploy, so `migrate` logs "No migrations to apply" once
per deploy; that is expected.

## Environment

On both services: `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `DATABASE_URL` (Railway sets it).
On the **worker** as well — it is the process that sends and scrapes:
`GEMINI_API_KEY`, `GEMINI_MODEL`, `SITE_URL`, `IMAP_HOST/PORT/USER/PASSWORD`,
`INBOUND_REQUIRE_AUTH=true`, optional `PICKS_ADDRESS_TAG`, `INTRO_ADDRESS_TAG`,
`RESEND_*`. A missing `GEMINI_API_KEY` on the worker degrades PutnamBot to
random picks silently; look for `[ai_picks]` in the logs.

The Railway-generated domain returns 400 (DisallowedHost); only the custom
domain is in `ALLOWED_HOSTS`. HSTS is deliberately off — it cannot be withdrawn
once browsers cache it.

## Before a deploy that carries migrations

0. Set `EMAIL_PAUSED=true` on **web** and **putnambowl2** (the worker). Nothing is
   sent and the worker stands down until it is removed. Remove it once the
   deploy is verified - the same day.
1. Take a Railway snapshot of Postgres.
2. Rehearse: `railway run --service Postgres pg_dump ...` to a local Postgres,
   `migrate` against it, run `manage.py test`, open `/home/` as a member.
3. Deploy. The v3 migrations (`leagues.0001`–`0002`, `accounts.0009`–`0011`,
   `main.0027`–`0031`) are idempotent data migrations around schema changes;
   they put every existing row in the `putnambowl` league and turn `is_staff`
   users into managers.

Railway's `DATABASE_URL` is an internal host. To run a management command from
a laptop, bridge the public URL:

```python
os.environ['DATABASE_URL'] = os.environ['DATABASE_PUBLIC_URL']
```

```bash
railway status --json
railway logs --service web --deployment
railway run --service Postgres <cmd>
```
