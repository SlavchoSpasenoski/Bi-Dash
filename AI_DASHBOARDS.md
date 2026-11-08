# AI-created dashboards

Open **AI Assistant → Build dashboard**, select an accessible data source and up to 12 relevant tables, describe the report, and choose **Create draft dashboard**. For example: “Monthly revenue this year as a line chart, sales by product as a bar chart, and the top ten customers as a table.” Macedonian prompts are supported by the selected model.

Bi-Dash supplies the exact selected table/column names, saved organization AI context and your prompt to the configured provider. Query result rows are not sent back to the model. The model returns a structured plan; Bi-Dash validates it, saves real queries, visualizations and a dashboard in one transaction, then the browser runs the queries sequentially through the existing execution API. No copying SQL is required. If the model needs clarification, no dashboard is created.

The initial builder supports PostgreSQL and MySQL/MariaDB, with table, bar, line, pie and counter visualizations, up to six widgets. Other connections remain available through normal Bi-Dash workflows. Provider flexibility is shared with chat: Ollama, OpenAI, Anthropic and OpenAI-compatible APIs. Compatible APIs must implement the expected API contract; an arbitrary agent endpoint requires an adapter.

## Execution and access

- Only users with AI, source, query creation/execution and dashboard editing permissions can build reports. Readers cannot use the builder. Source permissions are checked again after generation.
- Generated SQL must pass a conservative single read-only SELECT/CTE validator and a reporting-function allowlist. Queries run inside database read-only transactions. Database credentials and database-level permissions still determine which objects can be read; table selection guides generation, and is not an authorization boundary.
- Generated queries have no automatic row cap. Use the query editor’s **Limit 1000** checkbox when needed. Explicit top-N limits requested in the prompt are preserved. Each query runs for at most 60 seconds, or the user's stricter configured limit. Existing concurrency limits apply. PostgreSQL uses its statement timeout and worker cancellation; MySQL uses the connector's existing connection cancellation on worker timeout.
- Generated queries and dashboards are unpublished drafts. Draft status is not a private access-control list: normal organization and source access rules still apply. Nothing is scheduled or published automatically.
- Review business definitions, joins, date ranges and totals before publishing. An expensive query can still use resources until its timeout.

## Failures and retries

Invalid plans create no resources. Repeating a request ID returns the existing draft instead of duplicating it. Per-query execution errors appear in the panel and leave the saved draft editable. A chart whose declared aliases are missing in actual results is changed to a table. **Retry report results** retries the draft's queries; it does not create another dashboard.

Keep the page open while generation and sequential execution complete. Closing the drawer preserves its state, but navigating away or reloading stops the browser's remaining execution steps. Already saved drafts and queued queries survive; reopen the draft to inspect or run its queries normally.

Local CPU models can take several minutes and may return invalid plans. Choose fewer relevant tables, use focused context and specify the requested metrics and chart types. Dashboard generation allows up to 3,000 output tokens for Ollama and 4,000 for hosted providers; chat keeps its smaller output limit. Provider/model context-window limits still apply.

Native transaction references: [PostgreSQL read-only transactions](https://www.postgresql.org/docs/14/sql-set-transaction.html), [MariaDB START TRANSACTION](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction).
