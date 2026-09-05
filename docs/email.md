# Email

One Gmail mailbox does everything for every league. There is no mailing list:
the site holds the membership, so the commissioner sends one message and the
site fans it out.

## Configuration

`IMAP_HOST/PORT/USER/PASSWORD/FOLDER/MARK_SEEN` for polling; `SMTP_HOST/PORT`
default to Gmail and `SMTP_USER/PASSWORD` default to the IMAP credentials, so
one app password does both. `RESEND_API_KEY/RESEND_FROM` is a fallback only —
Resend's sandbox sender reaches nobody but the account owner. `SITE_URL` is
the link in every mail; set it on the worker, which is what sends.
`INBOUND_REQUIRE_AUTH` stays on in production.

`EMAIL_PAUSED=true` is the kill switch: `outbound_suppressed()` is true for every
transport, and `tick_all_leagues()` returns without polling the mailbox or
ticking a league. Managers see a red line at the top of every page while it
is set. It is for deploy windows and incidents, and it must be removed the
same day: a scheduled publish that fires under the pause mails nobody.

## Addresses

- `user@gmail.com` — a message to the league.
- `user+picks@gmail.com` — "these are my picks". `PICKS_ADDRESS_TAG`.
- `user+intro@gmail.com` — this week's intro. `INTRO_ADDRESS_TAG`.

Gmail delivers `user+tag@` to `user@` and keeps the tag in the headers, which
is what lets one inbox route three jobs. Ballots and confirmations set
`Reply-To` to the picks address, so replying to one is unambiguous — including
for the commissioner, whose plain mail is published.

## Inbound (`main/inbound_email.py`)

`fetch()` runs every worker tick, searches a rolling 7-day window (`SINCE`, not
`UNSEEN` — a person reads that inbox too, and reading a message would hide it
from the poller for ever), newest first, 25 at a time, and never raises.

`ingest_message` gates each message: Message-ID present → not already in
`ProcessedEmail` → From parseable → `Authentication-Results` shows a DMARC,
DKIM or SPF pass (the only real security boundary; `From` is forged trivially)
→ sender is a member with an address → body not empty. The sender's account
decides the league; a sender with accounts in two leagues is refused and left
unprocessed. Then:

| Sent to | Sender's `email_posts_enabled` | Treated as |
|---|---|---|
| `+intro` | on | the week's intro; confirmation reply |
| `+picks` | either | pick submission |
| plain address | on | announcement — published to the feed and relayed |
| plain address | off | pick submission |

Dedupe reads `ProcessedEmail`, never the feed: deleting a feed row in the admin
must not get a message re-ingested and relayed to the whole league again. Only
messages that were acted on are recorded; a rejected one is picked up on the
next poll once the account exists. `ProcessedEmail` is written before relaying.

Bodies are plain text rendered escaped. `manage.py fetch_emails --check`
verifies the mailbox; `--file message.eml` ingests one message by hand.

## Outbound (`main/email_utils.py`)

SMTP from the mailbox whenever `smtp_ready()`; Resend otherwise. Every send is
per member — `league_recipients(league)` (one address per person, compared
case-insensitively) — never to a list, because a ballot reply on a group message
would broadcast someone's picks. `weekly=True` honours the member's
`email_weekly` opt-out; relayed league correspondence does not. Sends run on
daemon threads that never touch the ORM.

`outbound_suppressed()` is `settings.TESTING` and every transport checks it.
The suite drives smtplib and Resend directly, so Django's locmem backend is no
protection; it really did mail fixture addresses once. Tests that exercise a
send path override `TESTING=False` and stub the transport.

### The weekly mail

One scheduled mail per week, "Week N picks are live", sent by `publish_week`
(and by the Publish button). Sections in order: the hand-written intro
(`weekly_intro`, `{week}` and `{league}` substituted with `replace()` at send
time), the lock line, the picks link, last week's recap (`email_recap`, never in
week 1 — `weekly_recap` survives a season boundary), the ballot (`email_ballot`,
last because it is the longest part). Recorded to the feed before the send
starts, keyed `{slug}-picks-live-w{n}`.

### The reminder

`send_pick_reminder_email` goes to `members_missing_picks(league, week)`:
anyone whose ballot is *incomplete*, not merely empty — the rules are
all-or-nothing — who has not opted out of `email_reminder`. Fires once,
`reminder_hours_before_lock` before `auto_lock_dt`; `reminder_sent_week` stops a
five-minute tick from repeating it. Per person, naming how many games they owe.

### The recap

Written at advance by `build_recap(league, week)`: Gemini with
`build_recap_prompt`, a plain summary if the model is down. The prompt is
`[editable instructions] + [recap_stats.data_block] + [RECAP_FORMAT_RULES]`; only
the instructions are editable on the Emails page, so no edit can remove the data
or the plain-text rule. `recap_stats` emits *angles* — best week, biggest mover,
the game nobody got, a tight race — only when they fire; a quiet week gives a
short list. Recaps are recorded to the feed (`{slug}-recap-{year}-w{week}`, so
regenerating replaces the row) and reach the league inside the next weekly
mail. Nothing mails a recap on its own.

### Relay

`relay_to_league` forwards a published announcement to every member except the
sender, the mailbox, and anyone already on the original To/Cc. `Reply-To` is the
original sender, not the mailbox — a reply to the mailbox would be read as
picks. Gated on `email_relay`.

### The intro library

`IntroTemplate` rows are reusable opening lines; choosing one copies its raw body
into `weekly_intro`, placeholders intact. New leagues get the seeded set from
`main/intro_seeds.py`. Substitute with `replace()`, never `format()`: a stray
brace in hand-edited text must not raise inside the send path.

## Picks by email (`main/pick_email.py`)

One Gemini call reads the message against the week's slate; the reply is
validated through `ai_picks._parse` so only this week's game ids and
`team1`/`team2` survive. No regex matcher: the one that existed picked the team
named as the loser and read "no" as New Orleans. Unlike PutnamBot, nothing falls
back to random — a wrong pick sabotages someone's week, so anything unclear
stays unpicked and is listed in the reply. The untrimmed body is parsed on
purpose (people edit inside the quoted ballot). "Model unavailable" is retried
for 30 minutes (`ProcessedEmail.deferred`) before the sender is told. Every
submission gets a reply saying exactly what was recorded; keep it.

## The feed

The home page's mail list is `LeagueEmail` only, newest first, one source so
every row has a real `sent_at`. Nobody receives old mail on joining: every send
is triggered by an event happening now, and the archive is read on the site.
