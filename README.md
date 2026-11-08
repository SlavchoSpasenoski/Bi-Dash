# Bi-Dash

A business intelligence workspace with local AI, built on [Redash](https://github.com/getredash/redash).

**[Bi-Dash User Guide →](USER_GUIDE.md)** — available features, improvements over upstream Redash, practical examples, roles, AI settings and troubleshooting.

- Query connected data sources and build interactive dashboards.
- Draft SQL with an integrated AI assistant powered by local Ollama.
- Create saved draft dashboards from prompts through **AI Assistant → Build dashboard**, including queries, charts and initial results. See [AI_DASHBOARDS.md](AI_DASHBOARDS.md).
- Choose Ollama, OpenAI, Anthropic, or an OpenAI-compatible service in **Settings → AI Configuration**.
- Save shared schemas and business definitions in **Settings → AI Context**.
- Manage Admin, Editor and Reader roles, export permissions and query limits in **Settings → Roles & Access**. See [ACCESS_CONTROL.md](ACCESS_CONTROL.md).
- A custom rising-signal logo with blue and yellow accents and anthracite navigation.
- Local Docker deployment with persistent PostgreSQL, Redis, and Ollama storage.

## Install from scratch: Windows, Linux and macOS

Follow the **[complete installation guide](INSTALLATION.md)** for prerequisites, first setup, AI configuration, updates and troubleshooting:

- [Windows installation](INSTALLATION.md#2-windows) — Docker Desktop, Git and PowerShell 7.
- [Linux installation](INSTALLATION.md#3-linux) — Docker Engine, Compose v2, Git and OpenSSL.
- [macOS installation](INSTALLATION.md#4-macos) — Docker Desktop for Intel or Apple Silicon, Git and OpenSSL.

The local Docker stack includes the application, PostgreSQL metadata database, Redis, workers and Ollama. The Ollama service starts automatically; its AI model is downloaded separately. A paid provider can be used instead. No host Python or Node.js installation is required.

### Windows quick start

Install Docker Desktop with Linux containers and PowerShell 7, then run:

```powershell
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
Set-Location Bi-Dash
pwsh -NoProfile -File ./scripts/start-local.ps1
docker compose -f compose.local.yaml exec ollama ollama pull qwen2.5-coder:7b
```

Open http://localhost:5001/setup to create your administrator. On later visits, open http://localhost:5001.

For Linux and macOS, clone the same repository and follow the guide's [shared initialization commands](INSTALLATION.md#5-initialize-on-linux-or-macos), which generate secrets and initialize a fresh database. The Windows script is also for fresh setup; use `docker compose -f compose.local.yaml up -d` for later starts.

The startup script generates local secrets in an ignored `.env` file. No AI API key is required for Ollama. Chat drafts SQL for manual use; Build dashboard creates and runs bounded read-only queries for PostgreSQL and MySQL/MariaDB. Review generated results before publishing.

This configuration is for local use at `127.0.0.1:5001`; shared hosting requires separate security and network configuration. Windows/Linux-container operation has been verified; native Linux and macOS host installations still require platform validation.

An administrator can select a provider, enter its model ID and their own API key in Settings. Compatible services also accept a custom API base URL. Each installation uses its own provider account; no project owner's key is bundled. Providers with other API protocols require an adapter.

See [LOCAL_PROJECT.md](LOCAL_PROJECT.md) for configuration, hardware limits, and validation, and [BRANDING.md](BRANDING.md) for the product identity.

## Development checks

```powershell
python -m unittest discover -s tests_local -v
docker compose -f compose.local.yaml config --quiet
```

The tests require the project's Python dependencies, including `cryptography`. With the local Docker image built, run them without a host Python environment:

```powershell
docker compose -f compose.local.yaml run --rm --no-deps --entrypoint python server -m unittest discover -s tests_local -v
```

## Upstream and license

This project is based on an upstream source archive, with custom AI integration, local deployment, and branding. See [UPSTREAM_SOURCE.json](UPSTREAM_SOURCE.json), [README.upstream.md](README.upstream.md), and [LICENSE](LICENSE). Original license and copyright notices are preserved. Original Bi-Dash contributions: © 2026 Slavcho Spasenoski, under BSD-2-Clause. See [NOTICE](NOTICE) and [COPYRIGHT.md](COPYRIGHT.md).

Local credentials, database storage, temporary files, and editor workspaces are excluded from Git.
