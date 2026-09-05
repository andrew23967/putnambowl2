# The legacy archive

`../legacy/` (a sibling of this folder) holds the original site: `db.sqlite3`
and `original-site-code.zip`. **Do not delete `db.sqlite3`** — its
`main_history` table is the only copy of the league's pre-2026 history (22
weeks, 19 members). Its own README describes the layout.

v2 carried an importer (`manage.py import_old_data`) and a converter
(`main/history_import.py`) for that archive. v3 removed both along with the
other one-off tooling; they are in git history at the commit before
`v3 phase 0` if the archive is ever needed again.

If those seasons should appear on the Seasons page, the cheapest route is a
one-off script that reads `main_history` and writes one `SeasonRecord` per
season with `final_standings` entries of `{username, score}` — readers accept
that old shape and recompute ranks — rather than rebuilding Game and Pick rows.

Migration `main/0010` dropped the new site's own `History` table after
converting it; production had an empty one, so nothing was lost there.
