# Bi-Dash permissions audit

Updated: 2026-11-10. The original audit made no product changes. The user subsequently authorized the Editor sharing implementation described below. No live roles, groups, policies or dashboard contents were changed.

## Method and limits

Reviewed role caps, source-group permissions, Editor table allowlists, saved-query/result scopes, dashboard listing and serialization, object permission grants, Reader report refresh and worker/job checks, plus related AI/public-share boundaries. A disposable PostgreSQL/Redis integration matrix exercised the actual models and HTTP handlers with synthetic users, groups, queries, dashboards and results. It confirmed the behaviors and defects below. The audit matrix asserted observed behavior, including known defects; its successful completion does not mean those defects are fixed. No rebuild/deployment was needed for the original audit; the later user-authorized implementation was built and applied locally. This is a focused authorization review, not a proof covering every route or connector.

## Actual precedence

| Layer | Current behavior |
| --- | --- |
| Explicit role | Admin/Editor/Reader caps group privileges. An explicitly assigned Editor does not become Admin through an admin group. Legacy users without an explicit role still infer their role from groups. |
| Source group | Full Access permits query execution; View Only permits report reading/safe saved-query usage. If any matching source group grants Full Access, another group's View Only does not veto execution. |
| Editor data rules | On a protected source, an Editor needs at least one matching group allowlist. All matching table/column grants combine by union. There is no explicit deny or group priority. |
| Full Access vs table rules | Full Access alone is not an all-table grant and does not bypass Editor allowlists. A separate matching allowlist can add the omitted tables/columns. |
| Protected-source boundary | Protection applies to the source globally: another Editor with Full Access but no matching rule is denied. Removing the last rule does not silently restore broad access; protected-source markers persist. |
| Saved Editor reports | Definitions can cross scopes when their explicit SQL dependencies are permitted to the viewer. Jobs/results/cache/exports remain recipient-scoped; the author's result is not shared automatically. |
| Reader reports | Editor column rules do not apply to Readers. Source groups authorize their reports/results. The new refresh path additionally requires a visible dashboard containing the saved query and validates typed parameters/read-only SQL. |

A source access grant can come from one group and the matching table rule from another. These are independent gates, not one priority-ordered list. Table exclusions are absent grants, not explicit denies. All-current-column selection is a snapshot; adding future database columns does not automatically grant them.

Sources: redash/access_policy.py; redash/models/users.py:174; redash/permissions.py:17,56; redash/data_access_runtime.py:62,130; redash/handlers/data_access.py.

## Editor-to-Editor visibility

On unprotected sources, source-group membership determines report/query visibility; the creator is not a separate read boundary. Modification remains restricted to owners/admins/explicit edit grants and role permissions. Draft status is not a confidentiality control.

On protected sources:

- The same effective table/column scope can read another Editor's queries/results.
- A restricted Editor can read a broader Editor's explicit-column query definition if it uses only permitted dependencies; the broader cached result remains denied.
- A broader Editor can open a narrower Editor's explicit-column query definition when its dependencies are allowed. Viewing does not grant ownership/edit rights.
- Admins have the documented same-organization report-read exception.

Result scope equality remains intact. Cross-profile definitions are authorized by redash/data_access_reports.py using the recipient's SQL compiler/schema and execution creates recipient-scoped results. Wildcard report projections must be made explicit before crossing profiles; typed date/number/value-enum parameters are supported, while text/conditional/dynamic-identifier templates remain unavailable across profiles.

Sources: redash/permissions.py:17; redash/data_access_runtime.py:119,130; redash/handlers/queries.py:421; redash/serializers/__init__.py:73,109.

## Reader visibility: ten dashboards

Confirmed with ten published dashboards owned by ten different synthetic Editors, all using a source accessible to the Reader: the Reader could list all ten without any per-dashboard grant. View Only source access is sufficient for report reading. Which Editor created a dashboard does not decide which Readers may view it.

There is currently no enforced Reader dashboard allowlist. Source-group separation can coarsely separate reports on different data sources, but cannot safely choose two out of ten dashboards that use the same source. Existing object permission/sharing controls do not implement this Reader read boundary. Hiding a dashboard from the UI is insufficient because direct links/results also need authorization.

Dashboard listing uses any matching widget source group, not a requirement that every widget be accessible. Individual widgets are intended to be restricted independently. Reader result-by-ID and downloads currently authorize the source; they do not require an allowed dashboard. Refresh is stricter: redash/reader_reports.py validates the dashboard/query relationship and current dashboard listing visibility.

Sources: redash/models/__init__.py:1152; redash/handlers/dashboards.py:144; redash/serializers/__init__.py:244; redash/handlers/query_results.py:286; redash/reader_reports.py:15; redash/handlers/permissions.py.

## Confirmed inconsistencies

1. **Dashboard list vs direct URL.** A Reader's list omitted another user's draft and an archived dashboard, but GET by ID returned 200 with readable source-authorized widgets for both. Listing filters are not enforced by DashboardResource.get. This does not grant access to an otherwise unauthorized source, but draft/archive/list omission is not a reliable privacy boundary. Refresh correctly rejects a dashboard excluded by its visibility check, so opening and refreshing have different behavior.
2. **Fixed after audit: mixed-scope dashboard blocked the whole page.** An Editor could read one widget's scope but not another's. The dashboard GET returned 403 instead of a restricted placeholder for the inaccessible widget. The restricted branch of serialize_dashboard first calls serialize_widget; that calls serialize_query, whose protected-query access check aborts before projection removes the query. This is an availability/consistency defect; the test did not observe unauthorized result disclosure.
3. **Fixed after audit: query pagination count ignored final scope filtering.** For two source-authorized queries with different scopes, the Editor's response had count=2 and one result. Counting/pagination happen before QuerySerializer removes inaccessible queries. This leaks a count and can create sparse/empty pages.

Also important in the present architecture: result/cache scope isolation remains stricter than definition sharing; source protection affects all Editors, and AI chat/builder remains disabled for all non-admin users in an organization with protected sources. Editor rules are enforced only on the supported standard MySQL/MariaDB connector, not uniformly across every database technology.

## Recommended coherent model

1. Retain explicit role caps and allow-only group union. Clearly distinguish source execution access from table/column grants; show effective access and the groups contributing each grant. Do not silently reinterpret existing Full Access as all columns.
2. Add administrator-managed **dashboard read grants to groups**. Example: Reader Sales -> dashboards 1,3; Reader Management -> all ten explicitly selected dashboards. Group grants combine by union, absence of a grant denies access, Admin retains same-organization access. A new dashboard should not become visible merely because the Reader can access its source.
3. Apply the same dashboard authorization to listing/search/favorites/tags, direct GET, widgets, parameters/dropdowns, refresh admission, worker execution, job polling, results and downloads. A Reader must not bypass it by requesting a result ID. A source permission alone must not become a substitute for a dashboard grant.
4. Correct mixed-widget serialization and apply Editor scope filtering before pagination/counting. Define draft/archive direct-link behavior consistently rather than relying on list filters.
5. Separately design cross-scope Editor template sharing and recipient-scoped execution. Keep cached results isolated until that design is implemented and tested.

The Editor definition-sharing, restricted-widget and pagination repairs have now been implemented at the user's request. Reader dashboard ACL, direct draft/archive consistency and cross-profile cache optimization remain outstanding. No group-priority change was introduced. Source-based Reader access must not be presented as dashboard-specific security.

## Follow-up: one excluded column and useful BI sharing

Verified on 2026-11-10 with a focused runtime test: two effective Editor policies that differ only by Employees.Salary have different source-wide permission fingerprints. Results from either fingerprint are rejected under the other. The original saved-query gate compared stored data_access_scope to the viewer's full source fingerprint before examining SQL, blocking unrelated Orders reports too. That gate has now been replaced with per-query dependency authorization for cross-profile definitions. This affects that data source, not every separate source in the organization. All 21 runtime tests passed in this follow-up, including the new one-column case. That verification initially changed no production permission behavior; the later implementation below changes definition access while preserving result isolation.

Design agreed and implemented for the supported MySQL/MariaDB subset:

- Separate report discovery/read sharing from data authorization and from ownership/edit grants. A shared/published report should not require equality with its author's complete source permissions.
- Authorize each saved query under the recipient's current table/column policy. Dependencies include SELECT expressions, WHERE, JOIN, GROUP BY, HAVING, ORDER BY, CTEs and subqueries, not just displayed result columns. A salary-based predicate or aggregate remains denied when Salary is forbidden.
- An all-column Editor and an Editor without Salary can both use a shared report that reads only Name or unrelated Orders columns. Only the former can run a Salary-dependent widget. A dashboard should show a restricted placeholder for that widget without failing other authorized widgets.
- Resolve SELECT * into an explicit publication-time report projection, or require an explicit projection for sharing. Do not silently change a saved report's output columns for different viewers. Ad-hoc policy-limited SELECT * is a separate operation.
- Compute execution/job/result scope from the recipient's authorization; preserve the existing cache isolation initially. Do not simply remove fingerprint equality and return the author's cached result. Later cache sharing can use validated query dependencies and equivalent effective access, including future row filters, only after that equivalence is established.
- Validate current permissions again in workers and for result reads/exports; never fall back to an old broader cached result after authorization fails. Existing owner/edit permissions remain independent.

The implementation targets the already enforced MySQL/MariaDB adapter and reuses its AST authorization. Wider connector support, row-level rules and cross-profile cache optimization remain separate extensions. None of this requires database users per Bi-Dash user.
