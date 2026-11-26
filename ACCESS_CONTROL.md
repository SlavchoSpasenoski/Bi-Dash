# Bi-Dash roles and execution safeguards

For table/column Editor rules enforced by Bi-Dash groups on one shared MySQL/MariaDB source (including full-privilege DB credentials, with SELECT-only execution in Bi-Dash), see [DATA_ACCESS.md](DATA_ACCESS.md). Saving group rules applies restrictions immediately; source-group and role permissions remain additional limits.

Manage organization defaults in **Settings → Roles & Access**. Assign a role when creating a user, or open **Settings → Users → a user → Role & query limits**. These settings are enforced by the backend, including requests made with a personal API key.

Dashboard visibility uses explicit per-user grants. Admin manages viewers throughout the organization; owner Editors manage their own dashboards. Organization Admins edit any Query/Dashboard in their organization; Editors edit only their own. Non-owner Editors/Readers see assigned published dashboards only; source membership alone is insufficient. Anonymous report tokens are disabled. See [DASHBOARD_ACCESS.md](DASHBOARD_ACCESS.md).

| Capability | Admin | Editor | Reader |
| --- | --- | --- | --- |
| Manage users, roles, data sources and AI configuration | Yes | No | No |
| Read SQL and data-source schemas | Yes | Within data-source group access | No |
| Edit dashboard content and attached queries/visualizations | Any same-organization resource | Owner only, with Full source access for query edits | No |
| Assign dashboard viewers | Any same-organization dashboard | Own dashboards | No |
| Browse reports; sort, filter and page loaded results | Yes | Yes | Yes |
| CSV, TSV and Excel export | Yes | Yes | Enabled by default; admin can disable |
| Execute fresh queries | Yes | Subject to runtime and outstanding-job limits | Saved dashboard refresh only (MySQL/MariaDB, PostgreSQL) |
| Anonymous public sharing | No | No | No |
| Scheduling / archiving reports | Subject to role and ownership | Disabled by default; configurable | No |
| AI Assistant | Yes | Enabled by default; configurable; scoped MySQL/MariaDB data only | No |

Groups still decide which data sources a member can access. An explicit role is an upper bound on group permissions: adding a Reader or Editor to an administrative group does not promote their assigned role. Editors cannot change access grants. Admins can manage all resources within their organization; deployment-wide query time limits still apply.

An Editor with View only source access can execute an accessible saved query with safe, declared parameters. Ad hoc SQL and changes to its SQL/parameter definitions require Full source access. The worker revalidates the saved definition and parameters before execution; changing the query or revoking access while it is queued denies that execution. Reader refresh remains limited to assigned dashboard reports.

Organization Admin does not imply installation-wide administration. Global diagnostics and changes to accounts/groups carrying an explicit `super_admin` capability require an active instance administrator. Existing installation administrators retain their builtin-group capability; organization Admins cannot grant it to themselves or another account. Organization Admins can still edit other Admins' reports within their own organization.

Existing accounts remain compatible: users without an explicit role are Admin if their groups contain the admin permission, otherwise Editor. New invitations default to Reader, with an explicit role selector. New SSO accounts default to Reader and can be promoted by an administrator. Existing accounts and data-source memberships are preserved. CLI-created accounts without explicit roles retain the legacy group-based inference.

## Editor report sharing

Published Datasets can be assigned to Editors directly or through a group. Organization Admins manage their organization's Datasets, and owner Editors manage their own. Editing a Dataset or creating a Dataset-backed report requires Full access to its saved base Query. Published definitions require current access to their base Query and source. Readers use Dataset-backed results through assigned published dashboards. Dataset report execution, cached results and export are checked against the current report, Dataset revision, source and actor permissions.

Editor AI requires Full query access to a supported MySQL/MariaDB source and matching group rules when that source is protected. Only administrators manage those rules. Chat and builder obtain permitted schema on the server, exclude organization-wide AI context, validate generated SQL before exposing/saving it, and recheck current access after generation. Chat returns validated SQL or a safe clarification; unvalidated model explanations are not shown to Editors. Receiving an AI draft does not change source permissions or result scope.

For protected MySQL/MariaDB sources, source groups remain the read-sharing boundary, and the recipient's current table/column permissions authorize cross-profile saved query definitions. One unrelated excluded column no longer blocks all reports. The author retains ownership; organization Admins can also edit. Other Editors do not gain editing through viewing or generic modify grants. Recipient execution and result/export/cache scopes remain isolated. Shared SELECT * definitions need explicit columns first; typed date/value parameters are supported, dynamic SQL identifiers and unsafe text templates are not. A denied widget does not block other dashboard widgets. See DATA_ACCESS.md for the supported SQL and parameter subset.

## Reader results and SQL privacy

Readers do not receive SQL, query hashes or query API keys in dashboard/result JSON. Query lists, SQL source routes, schemas, query snippets, AI and modification endpoints are denied. Result responses use `Cache-Control: private, no-store` so new authorization decisions are not replaced by long-lived cached API responses.

Readers can refresh explicitly assigned published dashboard queries with safe typed parameters on MySQL/MariaDB or PostgreSQL. Server-stored SQL, read-only execution and runtime/concurrency limits apply. Queue/worker/poll/result/download checks revalidate dashboard grants, source access and report provenance. Reader report caches do not update shared latest pointers or alerts. Arbitrary SQL, unsafe text parameters, unsupported engines and refresh outside a dashboard remain unavailable. Downloads additionally require the role's export permission.

## Query limits

Editor defaults are **60 seconds per execution** and **two running or queued jobs per user**. Runtime can be configured from 1–3600 seconds, and the outstanding-job limit from 1–20. User profiles can impose stricter limits; blank fields inherit the role policy. A larger user override cannot relax a stricter role limit. Identical pending requests can reuse their existing job; new work beyond the quota receives HTTP 429.

Admission uses a Redis lock, and counts queued work as well as running work. Personal API requests use their authenticated user's limits. Anonymous query/dashboard tokens cannot execute or read reports. Non-admin jobs do not reuse an administrator's unrestricted execution. Job status/cancellation is scoped to the organization and submitting identity. User-role/disabled-state checks are repeated before queued jobs execute.

The effective runtime is the smaller of the role/user limit and any positive deployment-wide limit. Limits are attached when new jobs are enqueued; changing a runtime policy does not retroactively shorten a job already admitted. Scheduled execution also checks the owner's scheduling permission, so disabling scheduling prevents further scheduled execution, including existing schedules.

RQ interrupts the worker task at its timeout. PostgreSQL additionally receives a native `statement_timeout`; the existing interrupt path requests query cancellation and closes the connection. The worker has its existing hard-kill fallback for an unresponsive connector. Remote cancellation behavior varies by connector and remote service; a worker timeout alone cannot guarantee that every remote engine has stopped its work. Use appropriate database-side resource limits and database credentials for the connected service. Timeouts do not undo statements that already completed.

Role policies, assignments and per-user limit changes are recorded as audit events. Non-admin users cannot assign roles, edit role policies or grant object permissions. An admin cannot demote themself or remove their own group membership. Permission changes take effect on subsequent server requests; reload open pages to refresh the visible controls.

## Verification

Tests cover Reader SQL/key redaction, result browsing and three export formats, direct-API execution/modification denial, dashboard-bound refresh with typed parameters, download restrictions, role assignment, group escalation prevention, CSRF, scheduling/sharing/deletion/AI restrictions, query admission, API-key quota bypasses, and stricter per-user limits. Real PostgreSQL and RQ worker tests use a one-second test limit with `pg_sleep(5)` to verify that execution is interrupted and PostgreSQL has no remaining active test statement. The normal Editor default is independently checked as 60 seconds.

Integration tests under `tests/` use the repository's `BaseTestCase`, which recreates the schema: run them only against a separate disposable test database. The `tests_local/` suite remains independent of a database.

Relevant connector references: [RQ job timeouts](https://python-rq.org/docs/results/), [Psycopg cancellation](https://www.psycopg.org/docs/connection.html).

## Effective access review

Administrators can open Settings > Users > user > Effective access for a read-only explanation of the current role/group/policy and dashboard checks. It does not test connections or execute SQL/AI. See [EFFECTIVE_ACCESS.md](EFFECTIVE_ACCESS.md).
