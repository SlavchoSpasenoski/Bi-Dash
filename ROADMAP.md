# Bi-Dash roadmap: monitoring, query versions and selective migration

The next proposed feature roadmap is [Datasets and calculated fields](docs/roadmaps/DATASETS_2026-11-24.md). See [STATUS.md](STATUS.md) for publication and deployment records for the milestones below.

Created: 2026-11-20. Updated: 2026-11-22. Status: monitoring, Query version history and selective Admin content migration completed locally; folder task withdrawn at the user's request. Git publication and Contabo deployment pending.

This replaces the completed delivery queue from 2026-11-14, preserved in [the historical roadmap](docs/roadmaps/ROADMAP_2026-11-14.md). Current published source baseline is 54261ce; Contabo runs the functionally equivalent 20261118-ui-html release. The original request covered roadmap preparation; the user subsequently requested starting implementation. Implementation completed one milestone at a time. No scheduler, Git publication or Contabo deployment is implied.

## Delivery order and dependencies

| Order | Milestone | Why here | Prerequisite |
| --- | --- | --- | --- |
| 1 | System Status: users, query execution and worker health | Reuse existing telemetry and make later changes observable before modifying saves or lists. | Define organization/role boundaries and safe monitoring fields. |
| 2 | Query version history | Establish reliable saved-query revisions and read-only comparisons. | Stable query IDs and existing ownership/source rules; no functional dependency on monitoring. |
| 3 | Selective Admin content migration | Portable component export/import with explicit dependencies, ownership and permission boundaries. | Stable definitions, versioned package format, source/reference remapping and safe destination defaults. |

Monitoring and Query history are independent features. Read-only history must not execute SQL or change dashboard bindings. The former folder milestone was removed on 2026-11-22 because the user judged the workflow too similar to tags.

## Evidence checked on 2026-11-20

### Monitoring: some data is already present

| Source | Confirmed fields/behavior | Limits |
| --- | --- | --- |
| PostgreSQL `users.details.active_at` | Last authenticated non-API request activity, periodically synced from Redis. | Last activity, not a list of live logged-in sessions or proof that a browser is currently open. |
| Redis `users:last_active_at` | Request signal records user ID/time; scheduler sync interval is one minute. | Read both Redis and persisted timestamp; missing/stale scheduler or browser polling can distort an activity indicator. No existing per-tab presence protocol was found. |
| PostgreSQL `events` | Organization, actor, action, object ID/type, time and extra properties; event writes are asynchronous. | Useful for usage context, not authoritative live execution state or guaranteed complete execution history. Do not return raw extra properties. |
| Redis/RQ jobs and workers | Queues, started registries, queued jobs, enqueue/start time, job metadata and worker heartbeat/state/current job. Query enqueue metadata includes org, source, query and actor IDs and scheduled flag. | Jobs expire; raw job metadata/arguments contain private authorization context/SQL and must never be exposed wholesale. Resolve API/scheduled/adhoc/deduplicated identities explicitly. |
| PostgreSQL query results | Cached output, runtime and retrieval metadata. | Does not describe all queued/running/failed work or reliably identify every initiating user. |
| Existing System Status | `/status.json` general counts, queues, manager/DB metrics; UI refreshes every 60 seconds. `/api/admin/queries/rq_status` already exposes queue/worker detail. | Both are currently `super_admin` gated and use system-wide primitives. A same-org Admin view needs explicit filtering and a safe response contract. |

A read-only aggregate check of the running **local** metadata DB found 6 queries, 6 query change rows covering those 6 IDs, 4 dashboard change rows, 2,538 events and 4 users with an `active_at` value; schema revision e92c71b54a08. This confirms stored metadata exists locally, not its completeness or live presence. No personal fields, SQL text or business rows were retrieved. Contabo was not inspected in this planning task; these counts must not be attributed to it.

### Query history: infrastructure exists, full versioning is unproven

`Query` has a `version` field used for save conflict checks and inherits `ChangeTrackingMixin`. `changes` stores object/version, author/time and previous/current field values. Constructor and archive paths record changes; the inspected normal save handler sets `changed_by` but no complete per-Save revision capture or history UI/API was found. A counter and initial change row do not prove complete historical versions. Audit actual save entry points and change payloads before choosing reuse versus a dedicated immutable revision table. Never expose raw `changes.change`: it can include API keys and internal options.


Code anchors: `redash/models/users.py`, `redash/models/changes.py`, `redash/models/__init__.py`, `redash/monitor.py`, `redash/handlers/admin.py`, `redash/handlers/queries.py`, `redash/handlers/__init__.py`, `redash/tasks/schedule.py`, `redash/tasks/queries/execution.py`, `client/app/pages/admin/SystemStatus.jsx`, `client/app/pages/queries/components/QueryPageHeader.jsx`.

## 1. System Status monitoring

Status: completed locally (2026-11-20). User request 2. See SYSTEM_MONITORING.md and STATUS.md for actual validation, bounded retention and the inherited Admin/super_admin distinction. Not committed/pushed or deployed to Contabo.

### 1A. Safe monitoring API and existing telemetry

- Provide a same-organization Admin monitoring view in System Status. Editor/Reader/API users cannot inspect other users' activity. The new view requires an explicit group super_admin capability for instance cards; existing role policy adds super_admin to every Admin, so that effective flag alone is insufficient. Existing raw system endpoints/role policy remain unchanged; see SYSTEM_MONITORING.md.
- Return allowlisted fields: user display identity/role, last activity, query ID/name or Ad hoc label, source display name, job ID, trigger type, queue, state, enqueue/start time, wait/runtime duration, worker availability and heartbeat freshness. No SQL, rows, parameters, credentials, tokens, policy scopes, raw job args/meta or unsanitized errors.
- Merge recent Redis user timestamps with persisted `active_at`, restricted to the current org and non-disabled users. Initially label this **Recently active**, with a stated time window, not **Online now**.
- List queued/running jobs with pagination/bounded scans; provide clearly bounded recent completed/failed/cancelled status if available. Distinguish cache hits (no new job), manual runs, Reader dashboard refresh, API, scheduled work and ad hoc SQL. A deduplicated job is not necessarily one distinct user action; show the initiator and mark unavailable attribution honestly.
- Show worker/queue bottlenecks: queue wait, long-running jobs and stale/missing worker heartbeats. Long-running is a heuristic, not proof of a DB lock. Actual remote database blocking/lock diagnosis is outside this milestone unless separate connector telemetry is explicitly approved.
- Refresh at a bounded proposed 10-second interval, pause when the page is hidden, avoid overlapping requests and show last update/stale/error state. Do not show zero active jobs when Redis is unavailable; distinguish unavailable, partial and empty states. Do not run an unbounded `KEYS` scan or query every source database.

### 1B. Presence needed for current users

- Add authenticated per-session/tab heartbeats with Redis TTL if the requested **currently present users** indicator cannot be supported by existing request activity. Proposed starting values: heartbeat every 30 seconds, expiry after 90 seconds, recent interaction within 5 minutes for Active versus Idle. Tune and document these values during implementation.
- Separate recently active, fresh presence, idle and stale/unknown; browser throttling or a network loss must not imply a definitive logout. Aggregate multiple tabs/sessions into one user row and expire abandoned entries. Exclude API/service activity and the monitoring page's own polling from artificial activity inflation.
- Store only minimal IDs/timestamps/state, with a bounded retention policy. If durable execution history is needed beyond RQ TTL, design a separate small execution ledger; do not pretend cached results/events provide an existing complete ledger.
- No cancel/kill buttons, session termination, raw SQL inspection or paid monitoring service in this first delivery.

### Acceptance

Test Admin/Editor/Reader/API and foreign-org isolation; heartbeat/idle/multiple tabs/expiry; disabled users; queued -> running -> completed/failed/cancelled; cached/ad hoc/scheduled/Reader runs; deduplication; vanished jobs; stale workers; Redis failure; pagination and safe error redaction. Use disposable DB/RQ fixtures and a real synthetic worker execution. Demonstrate visible queue wait and a deliberately long test query, without hanging or changing a business DB. Record metadata freshness and limitations in the UI/docs. Monitoring reads must not alter query definitions or permission decisions.

## 2. Query version history

Status: completed locally (2026-11-20). User request 1. Queries only; no Dashboard history UI. See QUERY_VERSIONING.md and STATUS.md for exact validation and migration f1a7c9e20b64. Not committed/pushed or deployed to Contabo.

### Storage and save behavior

- Audit create/update/fork/AI-generated save and archive paths, version counter behavior, existing `changes` completeness and concurrency handling. Reuse safe existing records if they support the contract; otherwise add an additive revision model without changing dashboard change tracking.
- Capture an immutable baseline and one revision per successful material saved-definition change, atomically with the query save. Failed/conflicting saves, Run, cache refresh, autosave drafts not persisted as saves and no-op Save must not fabricate revisions.
- Snapshot only the definition whitelist: SQL, name, description, source reference and supported parameter/query-definition options. Exclude API keys, source credentials, result pointers/data, schedule execution state, authorization fingerprints, grants, tags/folder placement and other organizational metadata.
- Give each saved revision a stable per-query sequence/ID, timestamp and actor. Preserve the current optimistic save conflict contract; do not assume the existing `version` counter already implements this sequence correctly.
- Existing queries start with an explicitly labelled baseline when tracking is enabled. Import only proven safe historical definitions; missing earlier versions remain unknown. Do not reconstruct or invent all prior saves from one initial change row.

### UI and authorization

- Add **Version history** to the saved Query page's upper-right options menu, available in query view/editor where authorized; not in Dashboard menus or new unsaved queries.
- Paginated list: revision, date/time, saved by and changed-field summary. Open a read-only version and compare SQL/definition changes with the current or another saved revision. Preserve unsaved editor work; opening history must not change the editor, run SQL, change sources or rebind dashboard widgets.
- First release is viewing/comparison only. Restore/rollback is not required by this request and is a separate possible extension.
- Enforce current query-definition/SQL visibility, organization and source rules on every list/detail/diff. Readers and report tokens must not receive SQL history. Revalidate each historical definition against the viewer's current source/table/column access; an old broader revision must not leak previously allowed metadata. Filter/redact revision summaries and counts consistently, not only SQL detail.
- Return safe author identity only; never serialize whole User or Change records. Deleted/disabled author and archived-query behavior must be explicit and consistent with current query visibility.

### Acceptance

Create -> material save -> second save -> list/detail/diff; no-op and failed/conflicting saves; simultaneous writers; initial legacy query; AI-created/forked queries; source switch; permission revocation and older forbidden columns; direct revision ID/foreign-org/Reader/token denials; escaped SQL display; pagination. Confirm no secrets, old results or authorization scopes leak. Existing dashboards keep their current saved query reference and continue to execute the latest authorized definition. Additive migration/rollback tests use disposable metadata and preserve existing records.

## Selective Admin content migration

Added and completed locally on 2026-11-22 at the user's request. Admin-only Settings Migration supports component selection, encrypted export, inspected/reviewed import, source mappings and replay protection. Without Users, ownership goes to the importing Admin; optional groups/rules have no members and do not grant target default-group access. See CONTENT_MIGRATION.md and STATUS.md for supported components, limits, exact validation and migration b3d9e1a42f86. Git/Contabo publication remains separate.

## Withdrawn task: Folders

Withdrawn 2026-11-22 at the user's explicit request. The implemented folder workflow was rejected as duplicating tags. All folder-specific application changes and the local folder schema migration were removed; monitoring and query history are retained. Existing tags remain available. No replacement folder design is approved or queued.

## Completion and execution rules

1. Work one bounded milestone at a time; record implementation separately from proposals. Resolve domain contracts before UI/API implementation. Use independent QA/security review for history authorization, following TEAM.md when coordinated work is requested.
2. Each feature must include appropriate role/organization regression checks, real synthetic execution where relevant, frontend checks, relevant product documentation and exact evidence in STATUS.md. Documentation-only planning does not require app builds, provider calls or migrations.
3. Preserve installation secrets, persistent volumes and existing users/reports/rules. Never run BaseTestCase on live metadata. Additive migrations require disposable round-trip validation and a deployment backup.
4. No new recurring automation is requested. The previous queue is completed and archived, not a trigger to restart old tasks. No paid provider fallback or external monitoring subscription is introduced.
5. Record the actual local/Contabo/Git publication state for each milestone. The user authorized starting implementation on 2026-11-20; automatic commit/push and Contabo deployment of the new features remain separate actions.
