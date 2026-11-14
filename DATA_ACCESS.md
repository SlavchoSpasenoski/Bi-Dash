# Group data access on one shared connection

Updated: 2026-11-15. This replaces the earlier separate-account/private-profile design.

## Current behavior

Bi-Dash uses the data source's existing shared database account, including an account with full database permissions. Bi-Dash enforces SELECT-only execution itself; it does not require changing server grants. Administrators choose exact database/schema, table and column allowlists and assign them to Bi-Dash groups. Editors receive table/column permissions through group membership. These data rules do not apply to Readers; their dashboard rules are deferred to a separate phase. Saving rules applies them immediately; there is no prepared connection, separate DB account or activation step.

The first supported execution adapter is the mysql connector with Oracle MySQL 8.0/8.4 (including Ubuntu-distributed upstream MySQL packages) and MariaDB 10.6. Ubuntu packages are recognized only with a matching Ubuntu package version suffix and Ubuntu server comment; numeric MySQL compatibility alone is insufficient. Percona/Aurora remain excluded. See the [Ubuntu MySQL package](https://packages.ubuntu.com/jammy/mysql-server-8.0). PostgreSQL metadata browsing remains available, but PostgreSQL, SQL Server and Oracle application enforcement are not enabled yet. Unsupported connectors cannot be saved as enforced group rules. The separate rds_mysql connector is not enabled for submitted SQL in this version and fails closed; use of the standard mysql adapter requires the supported server families.

## Administrator workflow

1. Keep the existing MySQL/MariaDB data source and credentials. Full database privileges are accepted; there is no SHOW GRANTS gate and no per-group DB user. A connection/SELECT capability check runs before saving. Every submitted MySQL query uses the SELECT-only compiler and a read-only transaction, including administrators and sources without group rules.
2. Create a group in Settings > Groups and add the intended users. Add the existing data source to the group. Full Access allows Editors to execute SQL within their data rules; View Only allows authorized existing results. User roles remain an additional limit.
3. Open Settings > Data Access, add a group rule set, choose the same data source and target groups. Saved rule sets start collapsed; click the header to expand or collapse them. Headers show the rule name, data source and table-rule count. New rule sets open automatically, and saving with invalid fields reopens the affected section. Collapsing a section retains its form values.
4. Use the searchable Schema/database > Table > Allowed columns menus. Select columns individually or capture Select all current columns. Refresh metadata reloads object names.
5. Choose Save rules. This immediately protects the referenced source and applies the saved snapshots. Previous drafts are shown for editing, but are not automatically applied by upgrading.
6. Check effective assignment for a user and source. Manage membership through Groups.

Administrators can edit an existing protected MySQL connection in Settings > Data Sources, including rotating credentials. Save validates a detached candidate with the SELECT-only connection check before changing stored settings; failure keeps the old settings. The connector type, source ID and group rules are retained. Changed connection identity invalidates old Editor result/job scopes and clears cached schema; review existing table rules when changing the database. Test Connection is also available for protected MySQL sources. Retired private connections remain unavailable; no separate activation is required.

Administrators are trusted and retain unrestricted data access in a separate result scope. Editors on a protected source require at least one matching group rule. Readers retain existing authorized dashboard/result access and can refresh saved dashboard reports with typed parameters on MySQL/MariaDB and PostgreSQL, but cannot submit arbitrary SQL; this page neither creates dashboard grants nor assigns Reader dashboard visibility. Group permissions combine by union of exact allowed tables and columns. An ordinary broad source-group membership does not bypass these rules.

Removing the last group rule leaves the source protected and denies Editors without rules. This prevents accidental restoration of unrestricted access. No database grants or accounts are created, changed or removed by Bi-Dash.

## SQL enforcement

All submitted MySQL execution parses one SELECT/WITH/UNION query into an AST using pinned SQLGlot, validates a conservative grammar and resolves column references against only the permitted schema for Editors. Administrators and unprotected sources use bound metadata for the referenced ordinary base tables, with the same closed SQL grammar. Every real table is replaced by a derived SELECT containing explicit permitted columns. Only regenerated canonical SQL is executed.

Thus SELECT * FROM orders returns only the permitted columns. Forbidden columns in SELECT, WHERE, JOIN, GROUP BY, HAVING, ORDER BY, subqueries and CTEs are rejected before the business query is sent. Unqualified tables use the data source's configured default database; explicitly qualified allowed database.table references also work.

New columns are not automatically allowed for Editors. Only ordinary InnoDB base tables without computed/generated columns are supported; views and indirect objects are rejected and table types are rechecked on execution. Native reporting functions are allowlisted. Unsupported SQL is denied, including arbitrary routines/table functions, system schemas, session variables, file output, locking, hints/comments, multiple statements, backslashes and ambiguous non-ASCII whitespace. This deliberately supports a smaller SQL subset than unrestricted MySQL.

The shared account may have full privileges. Every submitted MySQL execution uses a read-only transaction, a fixed SQL mode, disabled LOCAL INFILE/multiple statements and bounded native/socket/lock timeouts. Current submitted MySQL execution is capped at 60 seconds or a stricter job timeout.

Application SQL enforcement is the authorization boundary in this design. It does not constrain a person connecting directly to the database with the shared credentials. Keep those credentials private to the server. Database/application administrators are trusted. Row filtering, masking and automatic account provisioning are not implemented.

## Sharing reports between Editors

A difference in an unrelated column no longer hides every saved query on the source. Cross-profile query definition access uses the existing MySQL/MariaDB SQL compiler against the viewer's current group allowlist. It checks referenced tables/columns in projections, WHERE, JOIN, aggregates, GROUP BY/HAVING/ORDER BY, CTEs and subqueries. For example, an Editor viewing an Orders report does not need Employees.Salary access merely because the author has it.

Execution, jobs, cache reads and exports remain scoped to the recipient. A newly readable definition does not grant the author's cached result or edit rights. An inaccessible dashboard widget is returned as a restricted placeholder without serializing its query or parameter mappings; authorized widgets still render. Query list counts/pagination apply protected-definition authorization before counting.

Cross-profile saved queries require explicit output columns; SELECT * and table.* retain their same-profile behavior but cannot cross profiles until rewritten explicitly. COUNT(*) is allowed when the table itself is permitted. Cross-profile parameterized definitions support typed dates/ranges, numbers and value enums, using neutral values for dependency analysis. Free-text/query-based parameters, conditional templates and dynamic identifiers are not supported across profiles. Actual submitted parameter values are validated and the rendered SQL is recompiled in the worker. Existing same-scope definitions retain their legacy editing/preview behavior.


## Results, saved queries and queued jobs

Source IDs and credentials stay the same. An immutable permission scope derived from the normalized effective table/column union and keyed connection identity is attached to saved queries, results, cache lookups and jobs. Unrelated rule saves do not change an unchanged effective scope.

An administrator's broad results cannot be read by restricted Editors. Editors with different effective permissions cannot reuse each other's caches or exports. Saved query definitions can be read across those profiles when the recipient can access the SQL dependencies; execution creates results in the recipient's own scope. Trusted administrators can list/open all same-organization non-archived dashboards, including other owners' drafts, and legacy queries and dashboards and read all same-organization, non-retired result scopes; newly executed administrator results remain separately scoped and unavailable to restricted Editors. For Editors, membership or rule changes invalidate old result scopes. A saved definition remains readable if its tables/columns are still authorized; refresh it to create a result in the new scope. Editors cannot use unscoped results from before protection. Legacy explicit-column query definitions can be authorized under current rules. Readers retain their previous source-group-authorized report/result access, including legacy results and results produced under other scopes; SQL and query tokens remain redacted. Readers can refresh server-stored queries on accessible dashboards with typed parameters, without receiving SQL execution/editor privileges. These read-only jobs use a separate report scope, recheck access when queued/executed/polled and never update shared latest-result pointers. Reader dashboard visibility rules are not implemented by this change.

Editor result/export/cache access and worker execution check the current scope. Reader JSON/CSV/TSV/XLSX access uses existing source-group and download permissions, not Editor column rules. Workers recheck membership and rules before executing; changes while queued reject the task. Previously downloaded data cannot be recalled.

The nullable query_results.policy_scope column is added by migration c4d2a8f1e690. Upgrade with manage db upgrade before starting the new application image. Existing data is preserved.

## Features currently unavailable when protection is configured

- Protected sources do not support schedules, alerts or public/shared query tokens. Editor query-based dropdown dependencies are unavailable; Readers and administrators may use existing authorized cached dropdown values without executing SQL.
- With protection configured, AI chat is available only to active same-organization administrators using user authentication; it returns text without executing SQL or saving resources. AI dashboard generation is also available to those administrators, with generated queries bound to their current source scope and permissions rechecked after generation. Other users' AI chat/dashboard generation and Query Results/Python execution remain unavailable until scoped context is supported. Readers retain existing authorized cached derived results with dependency-group checks; no new execution or dashboard rights are granted.
- A readable shared definition must be refreshed in the recipient's effective scope. Administrator-authored definitions can be read by an Editor only when their explicit SQL dependencies are permitted; administrator cached results remain unavailable to Editors.
- Other separate unrestricted sources pointing at the same data are outside this source's rules. Inventory access to those sources.

These boundaries are displayed in the settings UI. Existing role/export/query-limit controls still apply.

## Metadata, API and storage

GET/POST /api/settings/data-access reads and saves group rules with optimistic revision checks. POST is administrator-only, requires session CSRF, validates organization references, SELECT capability and supported table/column metadata, then applies the rules. The former /activation endpoint has been removed.

GET /api/settings/data-access/effective?user_id=...&data_source_id=... previews effective group assignment.

GET /api/settings/data-access/metadata?data_source_id=...[&schema=...[&table=...]] returns staged names only for administrator management, using fixed parameterized read-only catalog queries. Same-organization checks and retired-private-source denial apply. There are timeouts and a 2,000-name stage limit with truncation reported. No business rows or connection secrets are returned. SSH connections are unsupported.

The new organization setting data_access_group_rules stores {revision,policies}. Existing data_access_drafts are imported into the editor only until the first Save rules. Retired old private IDs remain denied; old active-account profiles are never reused.

Policy fields remain id, name, data_source_id, status=active, default_effect=deny, group_ids, user_ids=[] and rules. Rules use catalog=null, exact schema/table names, column_mode=explicit or all_current and a concrete columns array. Only group assignments are accepted.

Current configuration limits: 50 rule sets, 100 tables per set, 1,000 columns per table and the existing 16 KB request limit. Select-all is disabled for truncated/oversized column lists. Server-side metadata search/pagination, bulk schema expansion and additional dialect adapters remain future work.

## Verification

See STATUS.md for actual checks and local deployment state. Historical exact-DB-column-grant tests belonged to the removed private-profile design and are not proof of this new application enforcement.

The SELECT-only raw connector has a private fixed catalog-query path for schema discovery. Submitted queries cannot opt into that path. Reader dashboard ACL design remains separate future work; this change does not grant new dashboards or alter existing group ownership.
