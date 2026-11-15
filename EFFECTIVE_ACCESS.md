# Effective access

Administrators can review a user's saved permissions at **Settings > Users > select a user > Effective access**. This is a read-only report; it never changes grants, connects to a data source, runs SQL or invokes an AI provider.

The report is organized by:

1. **Identity and role**: account status, group membership and effective query limits (including stricter user overrides).
2. **Actions**: role/group and organization-policy permissions, with a reason for each allowed or denied action. Resource-specific conditions remain separate.
3. **Data**: each same-organization source, matching source groups and Full/View Only levels, Groups versus rule-set mode, source access and SQL submission permission, and the exact effective union of allowed table/column names. Readers are explicitly shown as report-output access; Editor column rules do not apply to them.
4. **Dashboards**: visible/hidden/draft/archived dashboards, separate view/edit/manage-viewers decisions (including direct and matching group visibility paths), and each query widget's definition authorization and reason. Text-only widgets do not need source authorization and are not counted as query widgets.

Use the name filter to find a source or dashboard. Expand a source/dashboard to inspect reasons and tables/widgets. **Reload permissions** obtains a fresh snapshot after any role, group, rule or viewer change. The report shows when the snapshot was generated; it is not a live subscription.

Allowed source SQL permission does not prove that a connector, SQL statement or database connection will succeed. Allowed widget definition access does not prove that a particular old cached result is reusable or that refresh will succeed. Result provenance, supported connector/parameter/SQL checks, worker authorization and current source connectivity remain independently enforced. AI also requires supported source/provider configuration; no provider-readiness check is performed here.

The endpoint is **GET /api/users/<id>/effective-access**, restricted to organization administrators and same-organization targets. Responses contain names and authorization explanations, never source connection options, passwords, SQL text, result rows, scope fingerprints or API keys. Report-token and non-admin users cannot inspect other users' access through it.
