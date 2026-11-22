# Query version history

Implemented locally: 2026-11-20. Storage migration: `f1a7c9e20b64` after `e92c71b54a08`. Actual validation/deployment state is in STATUS.md.

## View and compare

On a saved **Query** page or SQL editor, open the upper-right **More actions -> Version history** menu. History is read-only: select a recorded version, inspect its SQL/definition, compare SQL with the current saved definition or another visible version, and paginate through older versions. Paging/reloading resets comparison to the current saved definition. Opening history does not save, run SQL, replace unsaved editor text or change dashboard query bindings. There is no Restore action in this delivery and no Dashboard history menu.

Each row contains a revision number, recording time, safe author name and changed-definition fields. Deleted/unavailable authors display as Former user. Saved SQL is rendered as escaped text; errors are generic and do not display server internals.

## What is recorded

An immutable revision stores SQL, query name/description, source ID and supported query-definition options (parameter definitions/defaults and auto-limit). It excludes query/API keys, source credentials, internal scope fingerprints, cache/result data, schedules, tags, folder placement and permission grants.

New queries, including AI-created drafts and forks, receive their own initial history. Successful material definition saves append a revision in the same metadata transaction. No-op Save, running/refreshing, cached result updates, tags and publish/archive metadata do not create SQL-definition revisions. Failed/rolled-back/conflicting saves create no committed revision. The existing query version field is an optimistic update-conflict counter; it is separate from the immutable history sequence. Concurrent stale saves return 409 rather than overwrite one another.

Existing queries receive one **Activation baseline** containing the current definition when the migration is applied. Earlier unrecorded edits cannot be reconstructed. Legacy `changes` records are not imported or exposed: they are incomplete for normal saves and can contain unsafe internal fields. Baseline recording time means tracking activation, not an invented historical Save time.

## Current access applies to historical SQL

History requires an active same-organization Admin/Editor with current query-definition/source access and a signed browser session. Readers, object/report tokens, personal API keys and mixed cookie/API-credential requests cannot browse it.

Every historical definition is checked against current source access. For restricted Editors, SQL dependencies are compiled against current table/column rules without trusting an old saved scope marker and without executing SQL or opening a source connection. Old forbidden-column/source definitions are omitted before list counts/pagination and direct detail/comparison requests are denied. Admins retain trusted same-org definition access, excluding retired/private sources under the existing access model. History access does not grant execution or change result/cache permissions.

If a selected version, source or comparison is no longer permitted, the panel clears the stale SQL and shows a generic unavailable message. The current saved definition can be omitted from comparison when its full dependency check cannot be satisfied. Names, authors and counts are returned only for readable revision rows; numeric history labels may have gaps after filtering. SQL comparison work is bounded in the browser; very large input uses a replacement-block fallback. Restricted-user list counting validates stored revisions, so no large-history load benchmark is claimed.

## Operation

- `GET /api/queries/<id>/versions?page=1&page_size=20`: visible metadata rows, filtered count and pagination. Page size is capped at 100.
- `GET /api/queries/<id>/versions/<revision_id>`: safe selected/current definitions. Optional `compare_to=<revision_id>` is authorized independently within the same query.
- Responses are private/no-store and history reads are rate-limited.
- ORM capture covers normal create/save/fork/AI paths. Privileged direct SQL maintenance is outside the application Save contract and must not be advertised as automatically versioned.
- Before local/remote activation: back up metadata, stop writers, apply the additive migration, then activate the matching image. Downgrading removes the new revision table/history; preserve a backup first. Existing query/source IDs, ownership, dashboard links and data-access rules must remain unchanged.

Tests must use disposable metadata. No AI inference or business database query is needed to validate recording or read-only comparison.
