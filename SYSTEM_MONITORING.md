# System Status monitoring

Implemented locally: 2026-11-20. No metadata migration is required. See STATUS.md for actual validation and deployment state.

## Using the view

Open **System Status** from the profile/navigation menu as an organization Admin. The user and query tables show only the signed-in Admin's organization. Pagination is bounded and the page refreshes every 10 seconds while visible, with no overlapping refreshes. Refresh failures retain a visibly stale snapshot; unavailable telemetry must not be interpreted as zero running queries.

Users show role, last request activity, last browser heartbeat and approximate presence:

- **Active:** at least one fresh tab reports interaction within the last five minutes.
- **Idle:** a tab has a fresh heartbeat but no recent interaction.
- **Stale / unknown:** an earlier heartbeat exists but has expired. This is not a confirmed logout.
- **Unknown:** no usable presence record, or the telemetry is unavailable.

Browser tabs send heartbeats every 30 seconds, only while visible, plus an idle update when hidden. Heartbeats expire after 90 seconds; the small per-user index expires after ten minutes. Multiple fresh tabs aggregate into one user row, with at most 50 retained tab entries. Tab IDs are freshly generated so duplicated browser storage cannot merge different tabs. Closing a browser/network loss is inferred from expiry, not a server-side logout event. Presence is approximate; a client can report only its own activity. No page text, SQL, browser URL or IP is stored by this feature.

Monitoring requests and heartbeats do not update last-request activity. Personal API-key activity is excluded from human activity timestamps. Existing stored timestamps can still include earlier polling activity; last request activity is not proof that a person is currently online.

## Query execution

The query table shows query/source display names, initiating identity, trigger, queued/running/recent status, enqueue/start times, wait and runtime durations. A query running for at least 60 seconds is labelled Long-running. A stale or missing worker heartbeat is a diagnostic hint. Neither proves a database lock or identifies blocking SQL on a remote database.

New query jobs enter a best-effort organization index retained for one hour and capped at 1,000 jobs. This is a bounded recent view, not a durable audit ledger. Expired RQ jobs can disappear earlier. Cache hits create no execution job; a deduplicated job can serve multiple requests, while the row reports only its initiating actor. Manual, API, scheduled, Reader dashboard and ad hoc work are distinguished when reliable metadata exists; older jobs may have Unknown attribution. Personal API keys are labelled as API triggers without changing their existing execution permissions.

A bounded transitional read also checks up to 20 RQ queues, 100 queued and 100 started entries per queue, for jobs submitted before monitoring was installed. Worker scans are bounded at 100 entries. A cap, missing job or Redis failure produces incomplete/unavailable state; the totals describe the retained accessible sample, not a complete instance history. The read path never runs SQL on connected sources or cleans up RQ registries.

Only allowlisted display fields are returned. SQL, rows, query parameters, credentials, raw errors, job args/meta, policy scopes and report authorization context are excluded. The monitor has no cancel/kill/session-termination controls and does not change grants or saved queries.

## Organization and instance boundaries

Both new endpoints require a signed browser session belonging to the route's organization; object tokens, personal API credentials and mixed cookie/API-credential requests are denied. The heartbeat is CSRF-protected and accepts only a UUID tab ID and boolean activity for the current user. Editor/Reader users can send their own heartbeat but cannot inspect monitoring.

Organization Admin does not imply `super_admin`. Instance workers and infrastructure cards require an active Admin with an explicit `super_admin` grant in a same-organization group, as indicated by the server's `instance_admin` flag. Existing installation administrators retain their explicit builtin-group grants.

The raw `/status.json` and `/api/admin/queries/rq_status`/outdated-query endpoints require the same explicit instance capability. Organization Admins cannot grant that capability or manage accounts that possess it; an instance administrator must perform those changes. Raw instance diagnostics remain trusted operator tools. The organization monitoring DTO never exposes raw global job metadata. On HTTP 401/403 the browser clears cached monitoring identities/jobs; transient connectivity failures retain a visibly stale snapshot.

## Endpoints and persistence

- `GET /api/admin/monitoring`: Admin-only organization snapshot. `users_page`, `jobs_page` are 1..10,000; `page_size` is 1..100 (default 25). Responses are private/no-store.
- `POST /api/session/presence`: current human browser session only, `{tab_id, active}`, CSRF and request-rate protection. Presence failures are best effort and never prevent query execution.
- PostgreSQL supplies existing user/query/source/group names and last activity; Redis supplies presence and RQ state. No new table or migration is introduced.
- Query indexing is best effort; telemetry failure must not turn a successful authorized enqueue into an application error. Existing execution/result/ACL enforcement is unchanged.

Validate using disposable metadata/Redis and synthetic source queries. Never run BaseTestCase on a live installation. No AI/provider call is needed for monitoring checks.
