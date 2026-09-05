# Leagues

Every account belongs to exactly one league. Logging in lands you in yours;
there is no league switcher. The site admin (a Django superuser) creates
leagues and hands each one to a manager.

## Roles

- **Member** — `Profile.role == 'member'`. Sees and acts on their own league.
- **Manager** — `Profile.role == 'manager'`. The dashboards, accounts, emails and
  rules editor for their league. Managing is a league role; it is not
  `is_staff`, which is site-wide and now means only "can open Django's /admin/".
- **Site admin** — `is_superuser`. Can open every league's dashboards and the
  `/leagues/` area. May have no league of their own (`createsuperuser` runs
  before any league exists); such an account is sent to `/leagues/` on login.

`leagues/access.py`:

- `current_league(request)` — the signed-in user's `profile.league`, memoised on the request.
- `current_settings(request)` — that league's `LeagueSettings`.
- `@league_required` — signed in and in a league; a league-less superuser is redirected to `/leagues/`; a closed league 403s members.
- `@league_manager_required` — plus `is_manager(user, league)`.
- `@superuser_required` — the site admin pages; login at `/leagues/login/`.

`leagues/context_processors.py` puts `league`, `is_manager` and `email_paused` on
every template.

## Joining

Each league has a `join_code`. The create-account page requires one;
`/join/<code>/` pre-fills it read-only and names the league. A wrong, missing or
closed-league code creates nothing. Managers see and rotate the code at the top
of `/dashboard/accounts/`; the site admin can too. Rotating invalidates every
link carrying the old code.

Usernames are globally unique (stock Django `User`): two leagues cannot both have
a `mike`. Emails are not unique; see below.

## The site admin

`/leagues/` lists every league with member and manager counts, the week and
whether the autopilot is on. `/leagues/new/` creates a league, its settings row,
the seeded intro library, and optionally a manager account in one transaction.
`/leagues/<slug>/` edits the name, slug and active flag, rotates the code, and
promotes, demotes or creates managers. Leagues are deactivated, never deleted —
`Profile.league` is `PROTECT`.

## Isolation checklist

When adding a query, ask which league it belongs to:

- `Game`, `WeeklyLeaderboard`, `LeagueEmail`, `IntroTemplate`, `SeasonRecord` — `filter(league=...)`.
- `Pick` — `filter(game__league=...)` or through a scoped game queryset.
- `User` — `filter(profile__league=...)`.
- Fetching by id from a request — `get(id=..., league=league)` or
  `get_object_or_404(User, pk=..., profile__league=league)`, so an id from
  another league is "not found", not "found".
- Feed slugs carry the league slug (`putnambowl-recap-2026-w3`), so two leagues'
  week-3 recaps are two rows. Signoffs use `league.name`.
- The worker: `auto.tick_all_leagues()` polls the mailbox once, then
  `auto_tick(league)` for each active league, fencing each so one league's
  failure does not stop the others.

`LeagueIsolationTests`, `ManagerAccessTests`, `RecipientsScopedTests`,
`InboundRoutingTests` and `RecapSlugTests` guard this.

## One mailbox, many leagues

There is one Gmail mailbox for the whole site. Inbound mail is routed by the
sender's account: their league is the message's league. If the same email
address holds accounts in two leagues, the message is refused with a logged
reason and left unprocessed, so it is picked up once the accounts are sorted
out. The `+picks` and `+intro` tags are unchanged. Outbound mail goes to the
league's own members (`league_recipients(league)`), never everyone.

## Bots

`create_putnambot --league <slug>` creates or refreshes the Gemini-driven bot
in one league. `make_bot_picks(league, week)` only picks for that league's
bots.
