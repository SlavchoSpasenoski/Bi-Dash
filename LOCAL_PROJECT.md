# Bi-Dash — local project

Based on https://github.com/getredash/redash, downloaded from the master branch on 2026-11-06. The upstream BSD license and source are preserved. This workspace uses a source archive because the bundled Git installation cannot use HTTPS; it is not a Git clone with upstream history.

Product identity: **Bi-Dash**, with blue actions, yellow accents, and anthracite navigation. See [BRANDING.md](BRANDING.md) for assets and color definitions.

## Start on Windows

Start Docker Desktop with Linux containers and allow at least 10 GB free on the drive holding Docker's data, then use PowerShell 7:

```powershell
./scripts/start-local.ps1
```

This builds the real Redash frontend and backend, generates private secrets in `.env`, initializes PostgreSQL, and starts the server, Redis, workers, scheduler, and Ollama. The first build downloads dependencies and can take several minutes. Open http://localhost:5001/setup and create your administrator. Data persists in the `redash-ai-local_redash-postgres` Docker volume. Local models persist in `redash-ai-local_redash-ollama`. This configuration is for local use.

On the current machine, initialization has already created the **Redash AI** organization and a local administrator (`admin@redash.local`). Its generated password is in `.tmp/local-admin.json`, which is ignored by Git and excluded from Docker images. Open http://localhost:5001/login and use those credentials. No newsletter or email signup was performed.

## Local AI with Ollama (default)

The default configuration uses `REDASH_AI_PROVIDER=ollama`, `REDASH_AI_MODEL=qwen2.5-coder:7b`, and `REDASH_OLLAMA_URL=http://ollama:11434`. No API key or OpenAI credit is required. Ollama is reachable only inside the Compose network; its port is not published to the host or LAN. Its cloud features are disabled with `OLLAMA_NO_CLOUD=true`.

On this machine, the existing `qwen2.5-coder:7b` model was copied from the Windows Ollama installation into the Docker volume on D:. It is already installed; the original host copy was preserved.

On a fresh installation, download the model once:

```powershell
docker compose -f compose.local.yaml exec ollama ollama pull qwen2.5-coder:7b
```

Click **AI Assistant** in the lower right. Ask in Macedonian or English, optionally paste table/column names and the SQL dialect, and copy reviewed SQL into Redash's query editor. The assistant has no database execution tools or automatic schema access. The conversation and manually entered schema are processed by the local Ollama service. Conversations stay in browser component memory and clear on reload. The first request loads the model; CPU inference can be slower than a hosted service.

Slow local inference defaults to a 30-minute request timeout (`REDASH_OLLAMA_TIMEOUT=1800`), with a 31-minute Gunicorn worker timeout in `compose.local.yaml`. If you increase the AI request timeout, increase the worker timeout above it too. `REDASH_OLLAMA_KEEP_ALIVE=30m` keeps the model loaded between requests; this is separate from the time allowed for an active request. Generation remains capped at 768 output tokens and the context window at 8192 tokens. These are token limits, not wall-clock timers. Browser Axios requests have no configured timeout.

Timed-out requests return HTTP 504 with an explicit timeout message. Connection/provider failures return HTTP 502. A timeout does not establish that Ollama stopped. Actual inspection of this project's Docker model showed 100% CPU execution at roughly 1.8 tokens/second; integrated graphics used by another native Ollama installation do not imply GPU acceleration for this Docker service. Keep each request focused on one SQL task and supply only the relevant table schema to reduce processing time.

Provider selection is explicit. An unavailable Ollama service produces a local error; the application never falls back to OpenAI.

## Optional OpenAI configuration

To switch back explicitly, set `REDASH_AI_PROVIDER=openai`, `REDASH_AI_MODEL=gpt-5.4-mini`, and `OPENAI_API_KEY` in `.env`, then run `docker compose -f compose.local.yaml up -d --force-recreate server`. This provider sends conversation/schema to OpenAI with `store: false`; provider retention and billing policies apply. The existing OpenAI key remains saved but is not sent to Ollama or used for local requests.

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

The ten isolated AI-client tests pass, including the local Ollama protocol, absence of OpenAI credentials in local requests, malformed responses, no automatic provider fallback, configurable request timeouts, and timeout classification. The deployed authenticated API was verified to use a 1800-second AI timeout with a 1860-second worker timeout and to distinguish HTTP 504 timeouts from HTTP 502 provider failures. Python/JSX syntax checks pass, and `docker compose -f compose.local.yaml config --quiet` validates the configuration. The full Docker image builds successfully, with one external MapLibre source-map warning. All six project services are running, including Ollama.

Headless Edge verified a real local Ollama response through the authenticated Redash AI panel: a Macedonian request with a sample PostgreSQL schema returned `SELECT COUNT(*) FROM orders;`. The first request, including model loading, took about 51 seconds. SQL clipboard copying, CSRF rejection, local-provider privacy text, desktop/mobile layout, and all six running services passed, with no uncaught browser errors. Earlier browser fixtures also verified escaped HTML, retry after provider failure, and clearing chat.

The earlier OpenAI test returned HTTP 429, `credit_balance_exhausted` / `insufficient_quota`. The active configuration now uses local Ollama instead; that OpenAI credit limitation does not affect local inference.

Current screenshots: `.tmp/redash-ollama-desktop.png` and `.tmp/redash-ollama-mobile.png`. These show the real local model response. Earlier OpenAI missing-key screenshots remain in `.tmp/redash-ai-desktop.png` and `.tmp/redash-ai-mobile.png`.

## Docker storage on this machine

The Docker WSL data disk was moved to `D:\DockerDesktopData\disk\docker_data.vhdx`. Its former directory, `C:\Users\cace_\AppData\Local\Docker\wsl\disk`, is a Windows directory junction pointing to that location. Docker's UI can therefore still display the original C: path even though the data disk resides on D:.

The stopped disk was copied and verified with matching SHA-256 hashes before redirecting the path. Docker restarted successfully; all six pre-existing containers and three volumes were verified, and the previously running `aicoach-postgres-1` container was restored. The old C: copy was removed only after these checks. Docker's WSL system files, logs, and WSL swap may still use C:.

Docker backup reference: https://docs.docker.com/desktop/settings-and-maintenance/backup-and-restore/

