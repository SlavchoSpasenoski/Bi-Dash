# Bi-Dash roles and execution safeguards

Manage organization defaults in **Settings → Roles & Access**. Assign a role when creating a user, or open **Settings → Users → a user → Role & query limits**. These settings are enforced by the backend, including requests made with a personal API key.

| Capability | Admin | Editor | Reader |
| --- | --- | --- | --- |
| Manage users, roles, data sources and AI configuration | Yes | No | No |
| Read SQL and data-source schemas | Yes | Within data-source group access | No |
| Build/edit queries, visualizations and dashboards | Yes | Own resources or explicitly granted edit access | No |
| Browse reports; sort, filter and page loaded results | Yes | Yes | Yes |
| CSV, TSV and Excel export | Yes | Yes | Enabled by default; admin can disable |
| Execute fresh queries | Yes | Subject to runtime and outstanding-job limits | No |
| Scheduling / public sharing / archiving reports | Yes | Disabled by default; separately configurable | No |
| AI Assistant | Yes | Enabled by default; configurable | No |

Groups still decide which data sources a member can access. An explicit role is an upper bound on group permissions: adding a Reader or Editor to an administrative group does not promote their assigned role. Editors cannot change access grants. Admins can manage all resources within their organization; deployment-wide query time limits still apply.

Existing accounts remain compatible: users without an explicit role are Admin if their groups contain the admin permission, otherwise Editor. New invitations default to Reader, with an explicit role selector. New SSO accounts default to Reader and can be promoted by an administrator. Existing accounts and data-source memberships are preserved. CLI-created accounts without explicit roles retain the legacy group-based inference.

## Reader results and SQL privacy

Readers do not receive SQL, query hashes or query API keys in dashboard/result JSON. Query lists, SQL source routes, schemas, query snippets, AI and modification endpoints are denied. Result responses use `Cache-Control: private, no-store` so new authorization decisions are not replaced by long-lived cached API responses.

Readers load previously computed results. Sorting, paging and table filters work on the loaded data. Selecting parameters can retrieve a cached result for that exact combination; when no result exists, the API asks for an editor to refresh it. Even a forced refresh request cannot enqueue a job for a Reader. An administrator can separately disable CSV/TSV/XLSX downloads; report data remains readable through the normal JSON API.

## Query limits

Editor defaults are **60 seconds per execution** and **two running or queued jobs per user**. Runtime can be configured from 1–3600 seconds, and the outstanding-job limit from 1–20. User profiles can impose stricter limits; blank fields inherit the role policy. A larger user override cannot relax a stricter role limit. Identical pending requests can reuse their existing job; new work beyond the quota receives HTTP 429.

Admission uses a Redis lock, and counts queued work as well as running work. Interactive jobs and query-token jobs share the owner's quota. Query tokens cannot escape a stricter owner timeout, and a Reader/disabled owner cannot refresh a report through an old token. Non-admin jobs do not reuse an administrator's unrestricted execution. Job status/cancellation is scoped to the organization and submitting identity. User-role/disabled-state checks are repeated before queued jobs execute.

The effective runtime is the smaller of the role/user limit and any positive deployment-wide limit. Limits are attached when new jobs are enqueued; changing a runtime policy does not retroactively shorten a job already admitted. Scheduled execution also checks the owner's scheduling permission, so disabling scheduling prevents further scheduled execution, including existing schedules.

RQ interrupts the worker task at its timeout. PostgreSQL additionally receives a native `statement_timeout`; the existing interrupt path requests query cancellation and closes the connection. The worker has its existing hard-kill fallback for an unresponsive connector. Remote cancellation behavior varies by connector and remote service; a worker timeout alone cannot guarantee that every remote engine has stopped its work. Use appropriate database-side resource limits and database credentials for the connected service. Timeouts do not undo statements that already completed.

Role policies, assignments and per-user limit changes are recorded as audit events. Non-admin users cannot assign roles, edit role policies or grant object permissions. An admin cannot demote themself or remove their own group membership. Permission changes take effect on subsequent server requests; reload open pages to refresh the visible controls.

## Verification

Tests cover Reader SQL/key redaction, result browsing and three export formats, direct-API execution/modification denial, cached-only refresh, download restrictions, role assignment, group escalation prevention, CSRF, scheduling/sharing/deletion/AI restrictions, query admission, API-key quota bypasses, and stricter per-user limits. Real PostgreSQL and RQ worker tests use a one-second test limit with `pg_sleep(5)` to verify that execution is interrupted and PostgreSQL has no remaining active test statement. The normal Editor default is independently checked as 60 seconds.

Integration tests under `tests/` use the repository's `BaseTestCase`, which recreates the schema: run them only against a separate disposable test database. The `tests_local/` suite remains independent of a database.

Relevant connector references: [RQ job timeouts](https://python-rq.org/docs/results/), [Psycopg cancellation](https://www.psycopg.org/docs/connection.html).
