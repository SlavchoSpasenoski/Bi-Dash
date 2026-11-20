# AI-created dashboards

Open **AI Assistant → Build dashboard** from the left navigation bar (mobile: navigation menu), select an accessible data source and optionally choose relevant tables (leave the selection empty to include all available permitted tables), describe the report, and choose **Create draft dashboard**. For example: “Monthly revenue this year as a line chart, sales by product as a bar chart, and the top ten customers as a table.” Macedonian prompts are supported by the selected model.

Bi-Dash supplies the exact selected table/column names and your prompt to the configured provider. Saved organization AI context is included for administrators only. Editors receive only server-derived permitted MySQL/MariaDB names; shared context and extra client schema/context are excluded. Query result rows are not sent back to the model. The model returns a structured plan; Bi-Dash validates it, saves real queries, visualizations and a dashboard in one transaction, then the browser runs the queries sequentially through the existing execution API. No copying SQL is required. If the model needs clarification, no dashboard is created.

The builder supports PostgreSQL and MySQL/MariaDB for administrators; Editor AI currently supports the standard MySQL connector on the supported MySQL/MariaDB versions only. It supports table, bar, line, pie and counter visualizations, up to six widgets. Other connections remain available through normal Bi-Dash workflows. Provider flexibility is shared with chat: Ollama, OpenAI, Anthropic and OpenAI-compatible APIs. Compatible APIs must implement the expected API contract; an arbitrary agent endpoint requires an adapter.

## Execution and access

- Only users with AI, source, query creation/execution and dashboard editing permissions can build reports. Readers cannot use the builder. Source permissions are checked again after generation.
- Generated SQL must pass a conservative single read-only SELECT/CTE validator and a reporting-function allowlist. Queries run inside database read-only transactions. Database credentials and database-level permissions still determine which objects can be read; table selection guides generation, and is not an authorization boundary.
- Generated queries have no automatic row cap. Use the query editor’s **Limit 1000** checkbox when needed. Explicit top-N limits requested in the prompt are preserved. Each query runs for at most 60 seconds, or the user's stricter configured limit. Existing concurrency limits apply. PostgreSQL uses its statement timeout and worker cancellation; MySQL uses the connector's existing connection cancellation on worker timeout.
- Generated queries and dashboards are unpublished drafts. Draft status is not a private access-control list: normal organization and source access rules still apply. Nothing is scheduled or published automatically.
- Review business definitions, joins, date ranges and totals before publishing. An expensive query can still use resources until its timeout.

## Date range filters

Ask for a manually selectable date range in the report prompt. The builder now accepts typed `date-range` parameters on widgets, saves their SQL placeholders and definitions, and maps the same parameter name across widgets to one dashboard filter. Only date ranges are supported in AI plans; arbitrary text parameters and undeclared placeholders are rejected. Both quoted `{{period.start}}` and `{{period.end}}` placeholders must appear in the query. The provider can supply validated ISO defaults; otherwise the default is from 30 days ago through today. Shared parameters must have identical defaults across widgets. Initial result execution receives those defaults, and the saved dashboard uses the existing date range picker to change them.

Older dashboards are not automatically modified to add a date filter. Regenerate with a date range request, or add a parameter to the existing query and map it to a dashboard filter. AI can still return an incomplete plan; review generated SQL and filter behavior.

## Failures and retries

Active same-organization administrators and authorized Editors can use AI; Readers and report API users cannot. **Ask AI** never executes queries. Administrators retain shared-context conversations. Editors must select a permitted MySQL/MariaDB source: the server supplies only allowed table/column names, ignores client schema and prior messages, excludes organization context, and returns only validated SQL or a safe clarification. General explanatory model prose is not returned to Editors. Changing source or reopening the Editor drawer clears the conversation.

**Build dashboard** checks every Editor widget's rendered SQL dependencies against current access before saving any resources; generated queries receive the normal recipient execution scope. Current role, source, groups, connection identity and permitted schema are rechecked after AI generation and before persistence. Retrying an Editor draft requires its original AI access fingerprint and revalidates its saved SQL; use a new request after access changes. Worker/result/cache authorization remains unchanged. These checks do not grant source access or expand column rules. See [Data Access](DATA_ACCESS.md).

Table selection has no count cap. Empty or omitted selection includes all cached tables and explicit selections include only those definitions. The existing 16,000-character schema-reference and 30,000-character combined-context limits still apply; oversize requests fail clearly before contacting the provider, without silently omitting tables. Refresh the data-source schema to include newly added tables.

For Docker Ollama, set **Settings → AI Configuration → API base URL** to `http://ollama:11434`. For Ollama running on the Docker host, use `http://host.docker.internal:11434`. This address must be reachable from the Bi-Dash server; the settings form accepts single-label Docker hostnames.

Invalid plans create no resources. Repeating a request ID returns the existing draft instead of duplicating it. Per-query execution errors appear in the panel and leave the saved draft editable. A chart whose declared aliases are missing in actual results is changed to a table. **Retry report results** retries the draft's queries; it does not create another dashboard.

Keep the page open while generation and sequential execution complete. Closing the drawer preserves its state, but navigating away or reloading stops the browser's remaining execution steps. Already saved drafts and queued queries survive; reopen the draft to inspect or run its queries normally.

Local CPU models can take several minutes and may return invalid plans. Choose fewer relevant tables, use focused context and specify the requested metrics and chart types. Dashboard generation allows up to 3,000 output tokens for Ollama and 4,000 for hosted providers; chat keeps its smaller output limit. Provider/model context-window limits still apply.

Native transaction references: [PostgreSQL read-only transactions](https://www.postgresql.org/docs/14/sql-set-transaction.html), [MariaDB START TRANSACTION](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction).
