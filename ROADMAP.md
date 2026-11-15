# Bi-Dash scheduled delivery queue

Approved: 2026-11-14. Tasks below are user requests, not completed features. Run in order, one bounded milestone per scheduled execution, nominally every five hours. Do not start overlapping writers or restart completed milestones. A task can span multiple executions; record a precise continuation point. Scheduling spaces usage but does not guarantee a subscription quota or reset. The supplied Plus usage snapshot showed 94% five-hour and 69% weekly remaining; it is historical, not a live meter.

Scheduler state: NOT CONFIRMED ACTIVE. Native automation creation failed three times with "Transport closed" on 2026-11-14 and again on the user's explicit activation request on 2026-11-15. No existing automation TOML was found before the latest attempt. The latest requested prompt starts at task 2 because task 1 is complete. No reconnect/restart action is exposed by the available app tools. Reopen the Codex app, then inspect for duplicates before retrying; this document alone does not schedule work.

## 1. Verify current functionality and publish verified baseline

Status: complete (2026-11-15). Verified baseline 91e019b pushed successfully to origin/main, https://github.com/SlavchoSpasenoski/Bi-Dash.

Latest milestone (2026-11-14): the new FreeDB source is now saved separately and authenticates; the old source still fails with 1045. Fixed detection of Ubuntu-packaged upstream MySQL 8.0/8.4, rebuilt and applied locally. All 97 local regression tests passed, live SELECT 1 passed and the actual authenticated Test Connection API/RQ worker returned HTTP 200/ok=true. Credentials/rules remained unchanged. Next continue saved-report/schema/role checks against the appropriate source; do not silently repoint old queries/rules. No commit/push yet. See STATUS.md for validation details.

2026-11-15: 147 isolated integration checks, 97 final-image local tests, Docker TypeScript, production build and Admin Edge UI checks passed. Live FreeDB connection/schema/API/SELECT 1 worker checks passed; all five saved queries/rules still reference the old failing source, so no live FreeDB saved-report refresh is claimed. Fixed and independently reviewed safe Admin maintenance/Test Connection of protected sources and stale UI guidance. See current STATUS.md for exact checks/limits. All disposable QA resources removed. Reviewed baseline committed and pushed as recorded above; next is task 2.

- User reports the data source has been replaced and now connects. Revalidate live connectivity; previous 1045 failures are historical, not proof of the new source failing.
- Inspect current code/Git state and existing reports/parameters before refreshing. New schemas may invalidate old SQL or group rules; diagnose rather than silently rewriting live reports or widening access.
- Verify current Admin/Editor/Reader behavior, actual saved-report refresh, safe SQL execution, group restrictions, sharing and relevant UI paths. Use disposable fixtures for role mutations/destructive integration tests. Include relevant regression checks and note known deferred gaps (Editor AI and dashboard read grants are tasks 2 and 3).
- Inspect the full uncommitted baseline and exclude secrets, .env, .tmp, private data and unrelated changes. Existing task-2/3 limitations do not prevent committing a validated baseline if explicitly documented, but unexpected regressions do.
- User authorizes a commit and GitHub push of this verified baseline once checks pass. Inspect the configured remote and branch; no force push/history rewrite. Record commit SHA and destination. If verification or push fails, retain work and explain the blocker rather than declaring completion.

## 2. Admin-controlled Editor data access and scoped AI

Status: complete locally, awaiting the user's commit decision (2026-11-15); manually continued because scheduling is unavailable.

Implemented server-scoped Editor MySQL/MariaDB chat/builder, server-side schema snapshots and generated-SQL validation, with post-generation and retry scope checks. Editor source-selection UI excludes organization context and resets conversation state. Admin data-rule management remains Admin-only. Validation: 51 integration checks passed, followed by 100 local regressions after the real-model fenced-JSON format repair; TypeScript/build, actual Ollama chat-to-saved-query/worker execution and isolated Editor browser draft/worker checks passed. Updated live services and verified the existing Editor's scoped AI GET endpoints while settings remain Admin-only. No live rules/report/source assignments were changed and no commit/push was made. See STATUS.md for precise provider-mock/real-generation limits.

- Audit existing behavior first. Admin alone configures schema/table/column access, using existing group rules. Editors see/use only their assigned data and cannot modify their grants or escalate via group/settings APIs.
- Existing Admin-only Data Access management and Editor SQL restrictions must be tested, not reimplemented unnecessarily. Protected-source AI is currently Admin-only; Editor AI is a new implementation requirement.
- Permit Editor AI chat/query assistance and dashboard generation only with the Editor's effective authorized schema/context. Filter table/column metadata and any shared AI context that might expose forbidden information; never pass raw broader schema or credentials to the model. If a required context cannot be safely scoped, fail explicitly rather than leak it.
- Validate generated SQL against current Editor rules before saving/executing and recheck at worker execution and result access. Preserve read-only enforcement, owner/edit boundaries, scope isolation, revocation handling, explicit provider choice and Ollama default with no paid fallback.
- Acceptance: Admin configuration succeeds; equivalent Editor/Reader mutation attempts fail; allowed AI SQL/dashboard runs; forbidden SELECT/WHERE/JOIN/aggregate dependencies and metadata requests fail; queued revocation blocks execution and no foreign cache leaks. Include focused independent security/QA review and real synthetic DB execution. Distinguish mocked provider tests from actual Ollama generation.
- Update product/user documentation. No automatic commit/push authorization for this task was given; retain reviewed changes for the user's decision.

## 3. Dashboard visibility for individual Editors and Readers

Status: completed and deployed locally (2026-11-15); no commit/push.

Named-user Access UI, relational grants and owner-only edits are active at localhost:5001. Final backend suite: 89 run, 88 passed and one optional target skipped; 100 local regressions, five React tests and TypeScript passed. Production build, disposable migration round-trip and actual browser grant/revoke checks passed. Backup taken and migration d83b6a21f409 applied; live API/ping/count checks passed. No automatic grants or source/report reassignment. See STATUS.md and DASHBOARD_ACCESS.md.

- Admin can assign/revoke which completed dashboards a same-organization Editor or Reader can view.
- An Editor owner can grant/revoke viewing of their own dashboard to other same-organization Editors or Readers. Receiving a dashboard does not grant resharing authority. Readers cannot share dashboards/reports.
- Only the creator/owner edits dashboard content. Admin manages visibility across dashboards; interpret the user's owner-only editing requirement strictly, including non-owner Admin content changes, and document this change from inherited behavior. Do not silently transfer ownership or add edit grants through visibility.
- Readers view and execute assigned saved dashboard reports with safe typed parameters; no arbitrary SQL/edit rights. Editors retain assigned-table/column SQL restrictions even on shared dashboards. Dashboard grants must not widen source/group/column permissions or expose author-scoped results.
- Enforce read grants consistently on listing/search/tags/favorites, direct dashboard/widget APIs, parameters/dropdowns, results/exports/cache, refresh admission, worker execution and job polling. Direct result IDs and public tokens must not bypass grants. Define consistent completed/draft/archived behavior; do not mass-archive, delete or implicitly share existing dashboards.
- Design safe migration/defaults for existing dashboards/users without broad automatic non-owner grants. Preserve existing data; record any required visibility changes and use disposable fixtures for testing before local migration.
- Acceptance matrix: owner, non-owner Editor, assigned/unassigned Reader, Admin, other organization, revoked grant, direct URL/result/export, parameter tampering, cached results and queued jobs. Include UI checks and independent QA/security review.
- Update documentation and record remaining limitations. No automatic commit/push authorization for this task was given.

## Execution and team rules

Coordinator reads AGENTS.md, PROJECT_CONTEXT.md, STATUS.md, TEAM.md, this queue and relevant topic docs. Check the actual Git/container state; preserve unrelated edits. Use the programmer for a bounded implementation, QA for independent acceptance tests and security architect for permission/AI-context boundaries; roles are instantiated as needed, not continuously running agents. Assign nonconflicting files. Avoid duplicating full context across all agents or rerunning broad tests without changed code/failures.

Each run selects the earliest incomplete task and advances one concrete milestone through appropriate checks; do not cut an unsafe edit halfway merely to fit a time estimate. Record files changed, actual validation, failures and next milestone in STATUS.md and update this queue. Do not work around usage/sandbox limits with paid APIs or external schedulers. If the task needs credentials or an unresolved product decision, finish independent work, report the blocker, and avoid repeated identical retries. Once all tasks are complete, disable the recurring automation. No public website/deployment publication, live business-data mutation, new paid service or DB grants changes is authorized by this queue.
