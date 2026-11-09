# Bi-Dash — local project

Based on https://github.com/getredash/redash, downloaded from the master branch on 2026-11-06. The upstream BSD license and source are preserved. This workspace uses a source archive because the bundled Git installation cannot use HTTPS; it is not a Git clone with upstream history.

Product identity: **Bi-Dash**, with blue actions, yellow accents, and anthracite navigation. See [BRANDING.md](BRANDING.md) for assets and color definitions.

Identity update (2026-11-08): the former B-in-a-square mark was replaced with the Rising Signal vector logo and refreshed favicons/PNG exports. Editable assets and a preview are in `branding/`. Product prose and notifications use Bi-Dash; upstream legal, technical and historical references retain their proper attribution. Original contributions are attributed to Slavcho Spasenoski under the existing BSD 2-Clause license; see NOTICE and COPYRIGHT.md. LICENSE/NOTICE are included in the frontend Docker build and accessible from login and the profile/version menu. Verification details are recorded in BRANDING.md.

User documentation (2026-11-08): USER_GUIDE.md is the full English user guide, with an explicit comparison against the recorded upstream snapshot, role-specific workflows, AI provider/context setup, report generation, execution and optional row limits, exports, privacy and troubleshooting. README links to it; the documentation site includes a short entry under User Guide. Git publication includes code and documentation, while runtime databases, .env, private credentials, caches and temporary verification artifacts remain excluded.

## Start on Windows

Start Docker Desktop with Linux containers and allow at least 10 GB free on the drive holding Docker's data, then use PowerShell 7:

```powershell
./scripts/start-local.ps1
```

This builds the real Bi-Dash frontend and backend, generates private secrets in `.env`, initializes PostgreSQL, and starts the server, Redis, workers, scheduler, and Ollama. The first build downloads dependencies and can take several minutes. Open http://localhost:5001/setup and create your administrator. Data persists in the `redash-ai-local_redash-postgres` Docker volume. Local models persist in `redash-ai-local_redash-ollama`. This configuration is for local use.

On the current machine, initialization has already created the **Bi-Dash AI** organization and a local administrator (`admin@redash.local`). Its generated password is in `.tmp/local-admin.json`, which is ignored by Git and excluded from Docker images. Open http://localhost:5001/login and use those credentials. No newsletter or email signup was performed.

## Local AI with Ollama (default)

The default configuration uses `REDASH_AI_PROVIDER=ollama`, `REDASH_AI_MODEL=qwen2.5-coder:7b`, and `REDASH_OLLAMA_URL=http://ollama:11434`. No API key or OpenAI credit is required. Ollama is reachable only inside the Compose network; its port is not published to the host or LAN. Its cloud features are disabled with `OLLAMA_NO_CLOUD=true`.

On this machine, the existing `qwen2.5-coder:7b` model was copied from the Windows Ollama installation into the Docker volume on D:. It is already installed; the original host copy was preserved.

On a fresh installation, download the model once:

```powershell
docker compose -f compose.local.yaml exec ollama ollama pull qwen2.5-coder:7b
```

Click **AI Assistant** in the lower right. The wider panel has a larger question field and a scrollable answer area. Ask in Macedonian or English and copy reviewed SQL into the query editor. Administrators can save shared context under **Settings → AI Context**; the optional extra-context section in the panel is only for the current conversation. The assistant has no database execution tools or automatic schema access. Conversations stay in browser component memory and clear on reload. The first request loads the model; CPU inference can be slower than a hosted service.

## AI settings for each installation

**Settings → AI Configuration** is available to organization administrators, alongside Data Sources and Users. Choose:

- **Local · Ollama**: installed model ID and Ollama server URL; no API key.
- **OpenAI**: model ID and the installation owner's API key, using the Responses API.
- **Anthropic · Claude**: model ID and the installation owner's API key, using the Messages API.
- **Other · OpenAI-compatible API**: model ID, API base URL including its version path (such as `https://provider.example/v1`), and the installation owner's key. Bi-Dash appends `/chat/completions`. Authentication-free compatible local servers are also supported.

The compatible option supports the text Chat Completions protocol; it does not imply support for every proprietary agent API, OAuth flow, or custom authentication scheme. Other protocols need a dedicated adapter. Model IDs are entered freely rather than limited to a fixed model list.

Profiles are saved per organization in PostgreSQL and retained when switching providers. API keys are encrypted using the server's `REDASH_SECRET_KEY` (or Bi-Dash's existing secret fallback), never returned by the settings endpoints, and omitted from audit event payloads. Keep the server secret stable when restarting or restoring a backup. A blank key keeps a previously saved key for that profile; the removal checkbox deletes it. Changing a compatible provider URL clears its saved key unless a replacement is entered. Redirects are not followed for provider calls.

Existing environment configuration remains the fallback until AI Configuration is first saved. Once saved, organization preferences take precedence, with no implicit reuse of environment API keys. Enter your own key when saving a hosted profile. Missing required keys disable chat until configured; there is no automatic provider fallback. Use HTTPS for hosted endpoints. Addresses are resolved from the Bi-Dash server, so host-native Ollama in Docker usually uses `http://host.docker.internal:11434`.

**Settings → AI Context** stores up to 24,000 characters of shared business definitions, SQL dialect, schemas and reporting conventions. Context is automatically included with each request for that organization and survives reloads and container restarts. Only include information that AI-enabled organization members and the selected provider may access. Context is reference data; it does not grant database access. It can be edited or cleared at any time. Large contexts may exceed a selected model's input window; keep local-model context focused.

Provider API contracts: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), [Claude Messages](https://platform.claude.com/docs/en/api/messages/create).

Slow local inference defaults to a 30-minute request timeout (`REDASH_OLLAMA_TIMEOUT=1800`), with a 31-minute Gunicorn worker timeout in `compose.local.yaml`. If you increase the AI request timeout, increase the worker timeout above it too. `REDASH_OLLAMA_KEEP_ALIVE=30m` keeps the model loaded between requests; this is separate from the time allowed for an active request. Generation remains capped at 768 output tokens for chat (3,000 for dashboard plans) and the context window at 8192 tokens. These are token limits, not wall-clock timers. Browser Axios requests have no configured timeout.

Timed-out requests return HTTP 504 with an explicit timeout message. Connection/provider failures return HTTP 502. A timeout does not establish that Ollama stopped. Actual inspection of this project's Docker model showed 100% CPU execution at roughly 1.8 tokens/second; integrated graphics used by another native Ollama installation do not imply GPU acceleration for this Docker service. Keep each request focused on one SQL task and supply only the relevant table schema to reduce processing time.

Provider selection is explicit. An unavailable Ollama service produces a local error; the application never falls back to OpenAI.

## Optional OpenAI configuration

Row-limit correction (2026-11-08): AI dashboard generation no longer wraps SQL in `LIMIT 1000`, and the PostgreSQL/MySQL connectors no longer truncate AI result sets. Newly generated queries default to `apply_auto_limit: false`; the existing query editor checkbox controls optional limiting. Explicit top-N requests remain supported. The 60-second or stricter role timeout and read-only transactions remain in place. This supersedes the initial row-cap description below.

Real-model dashboard verification: local Ollama `qwen2.5-coder:7b` returned a valid one-counter JSON plan against a synthetic PostgreSQL schema after the counter mapping instructions were strengthened with a concrete example. Its initial malformed counter plan was rejected without creating resources. This test used no business data or paid API.

Dashboard builder update (2026-11-08): **AI Assistant → Build dashboard** creates real unpublished queries, visualizations and layouts from a prompt and selected cached table schemas. PostgreSQL and MySQL/MariaDB are supported initially. Queries run sequentially through the existing job APIs with native read-only transactions, a 1,000-row result cap and a 60-second or stricter user timeout. Saved organization AI context and provider selection are reused. See [AI_DASHBOARDS.md](AI_DASHBOARDS.md) for access rules, provider contracts, retries and limitations. The isolated regression suite passed 63 tests, including real PostgreSQL/RQ execution, read-only enforcement, resource creation, idempotency, permission rechecks and connector behavior. Browser checks used synthetic data and a stubbed model response: a bar chart, counter (600) and table were created and rendered with three saved real query results and no uncaught JavaScript errors. Desktop and mobile layouts passed. The final production image was deployed locally; existing Admin access and the MySQL builder source were verified without creating business reports. Disposable browser containers, credentials and database were removed. Building and loading the model simultaneously exhausted local memory, requiring a Docker Desktop restart; subsequent build and inference checks were run sequentially. No paid API calls were made. Screenshots include `.tmp/ai-builder-dashboard.png`, `.tmp/ai-builder-form-mobile.png` and `.tmp/ai-builder-local.png`.

Use **Settings → AI Configuration** to choose OpenAI and enter your model ID and API key. For installations that have never saved AI Configuration, the earlier environment-based setup also works: set `REDASH_AI_PROVIDER=openai`, `REDASH_AI_MODEL` and `OPENAI_API_KEY` in `.env`, then recreate the server. Saved organization settings take precedence over these variables. This provider sends conversation/context to OpenAI with `store: false`; provider retention and billing policies apply. Environment OpenAI keys are not sent to Ollama or to another provider.

The authenticated, organization-scoped `api/ai/chat` endpoint requires `create_query`, validates request sizes and roles, limits requests to 10/minute per client IP, keeps credentials on the server, and returns sanitized provider errors. Cookie-authenticated AI requests explicitly require a CSRF token, including deployments where upstream Redash CSRF checks are disabled. API-key authentication follows Redash's existing behavior.

## Operations

```powershell
docker compose -f compose.local.yaml logs --tail 100 server
docker compose -f compose.local.yaml down
# After source changes:
./scripts/start-local.ps1
# Isolated AI client tests (no API key/network/database required):
python -m unittest discover -s tests_local -v
```

Do not delete the Docker volume if you want to preserve data. For upstream development, the original `compose.yaml` remains available; the standalone local configuration deliberately builds frontend assets inside Docker.

API references: https://docs.ollama.com/api/chat and https://developers.openai.com/api/docs/guides/text

## Verification on this machine

Role-management update (2026-11-07): Admin, Editor and Reader are configured in Settings → Roles & Access and user profiles. Editor defaults are 60 seconds and two running/queued jobs, with stricter optional per-user limits. Readers receive cached results and configured exports, without SQL/source access. Role and execution tests include real PostgreSQL and worker cancellation with a one-second test limit. Isolated browser checks verified saved policies, per-user limits, Reader pagination/sorting and CSV retrieval, SQL API denial, Editor SQL access, and no uncaught JavaScript errors after correction of a missing dashboard-header import. Existing local administrator access and the 60-second default were verified on port 5001 without changing local users or reports. See [ACCESS_CONTROL.md](ACCESS_CONTROL.md) for role behavior, compatibility and connector limits.

AI settings update (2026-11-07): 20 isolated client/configuration tests and six integration tests passed. Integration tests used a separate disposable PostgreSQL database, which was removed afterward; they checked admin permissions, organization separation, context persistence and injection, encrypted key storage, and explicit CSRF enforcement. The final production image built successfully with the existing MapLibre source-map warning. Headless Edge verified provider selection and a custom-key save with mocked provider calls, key-field clearing, a simulated chat answer, and the desktop/mobile layouts with no uncaught browser errors. Prompt dimensions were approximately 824 × 213 px on desktop and 358 × 202 px on mobile. No paid-provider request was made. Screenshots are `.tmp/ai-configuration-desktop.png`, `.tmp/ai-context-desktop.png`, `.tmp/ai-panel-desktop.png`, `.tmp/ai-panel-answer.png` and `.tmp/ai-panel-mobile.png`.

Earlier local-provider validation covered the Ollama protocol, absence of OpenAI credentials in local requests, malformed responses, no automatic provider fallback, configurable request timeouts, and timeout classification. The authenticated API was verified to use a 1800-second AI timeout with a 1860-second worker timeout and to distinguish HTTP 504 timeouts from HTTP 502 provider failures. Python/JSX syntax checks passed, and `docker compose -f compose.local.yaml config --quiet` validated the configuration. The Docker image built successfully, with one external MapLibre source-map warning.

Headless Edge verified a real local Ollama response through the authenticated Bi-Dash AI panel: a Macedonian request with a sample PostgreSQL schema returned `SELECT COUNT(*) FROM orders;`. The first request, including model loading, took about 51 seconds. SQL clipboard copying, CSRF rejection, local-provider privacy text, desktop/mobile layout, and all six running services passed, with no uncaught browser errors. Earlier browser fixtures also verified escaped HTML, retry after provider failure, and clearing chat.

The earlier OpenAI test returned HTTP 429, `credit_balance_exhausted` / `insufficient_quota`. The active configuration now uses local Ollama instead; that OpenAI credit limitation does not affect local inference.

Current screenshots: `.tmp/redash-ollama-desktop.png` and `.tmp/redash-ollama-mobile.png`. These show the real local model response. Earlier OpenAI missing-key screenshots remain in `.tmp/redash-ai-desktop.png` and `.tmp/redash-ai-mobile.png`.

## Docker storage on this machine

The Docker WSL data disk was moved to `D:\DockerDesktopData\disk\docker_data.vhdx`. Its former directory, `C:\Users\cace_\AppData\Local\Docker\wsl\disk`, is a Windows directory junction pointing to that location. Docker's UI can therefore still display the original C: path even though the data disk resides on D:.

The stopped disk was copied and verified with matching SHA-256 hashes before redirecting the path. Docker restarted successfully; all six pre-existing containers and three volumes were verified, and the previously running `aicoach-postgres-1` container was restored. The old C: copy was removed only after these checks. Docker's WSL system files, logs, and WSL swap may still use C:.

Docker backup reference: https://docs.docker.com/desktop/settings-and-maintenance/backup-and-restore/


### Schema prefix handling

Saved context and additional schema accompany the latest user question, after conversation history. Explicit English table-prefix declarations produce a compact summary of exact referenced identifiers immediately before the question. System guidance requires preserving supplied table prefixes. This improves model adherence; it is not SQL validation or a guarantee of correct explanations. The exact reported users-count question was verified against local Ollama with the saved 10,909-character context and generated `FROM wnbe_users` (258 seconds on CPU). All 22 isolated client/settings tests passed inside Docker. `wnbe_users` counts registered WordPress accounts and excludes guest-only checkout identities.
