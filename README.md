# Bi-Dash

A business intelligence workspace with local AI, built on [Redash](https://github.com/getredash/redash).

## Start here

| Goal | Documentation |
| --- | --- |
| Use Bi-Dash | [English user guide](USER_GUIDE.md) · [Упатство на македонски](USER_GUIDE.mk.md) |
| Install or update | [Installation guide](INSTALLATION.md) · [Private Linux deployment](LINUX_DEPLOYMENT.md) · [Public HTTPS deployment](PUBLIC_DEPLOYMENT.md) |
| Contribute code or documentation | [Contributor guide](CONTRIBUTING.md) |
| Check release and verification status | [Project status](STATUS.md) |

## What you can do

- **Queries and dashboards.** Connect supported sources, write and parameterize SQL, build visualizations, publish dashboards, schedule eligible queries and configure alerts. Sign-in, source access and sharing rules determine what each person can open, refresh and export.
- **Datasets and calculated fields.** Publish reusable models from saved direct MySQL/MariaDB queries. Select source fields, define arithmetic, preview, version and share the model. Use **New Query → Use a Dataset** for detail tables or grouped Sum, Average and Count reports. A supported single join can combine tables on the same source. [Dataset workflow](USER_GUIDE.md#datasets)
- **AI Chat and Build Dashboard.** Draft SQL or generate a dashboard with saved queries and visualizations for review. Ollama is the default. Admins can configure OpenAI, Anthropic or an OpenAI-compatible provider and maintain shared AI Context. Editor AI uses permitted MySQL/MariaDB schema under the source rules. [AI guide](AI_DASHBOARDS.md)
- **Access and sharing.** Admin, Editor and Reader roles work with source groups, table/column rules and dashboard grants for users or groups. Admins can inspect effective permissions and configure execution limits, exports and other role controls. [Access control](ACCESS_CONTROL.md) · [Dashboard sharing](DASHBOARD_ACCESS.md) · [Effective access](EFFECTIVE_ACCESS.md)
- **History and administration.** Review saved Query versions, inspect recent query jobs in System Status, and move selected organization content with encrypted migration packages. [Query history](QUERY_VERSIONING.md) · [System Status](SYSTEM_MONITORING.md) · [Selective migration](CONTENT_MIGRATION.md)

## Portable Linux server installation

For a public server with a domain and automatic HTTPS, follow
[Public server deployment](PUBLIC_DEPLOYMENT.md). Visitors access the site in
their browser without an SSH tunnel.

For automatic secrets and a repeatable private server installation, see [Linux deployment](LINUX_DEPLOYMENT.md). Run `bash scripts/linux.sh install` after installing Docker.

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

Open [local setup](http://localhost:5001/setup) to create your administrator. On later visits, open [Bi-Dash locally](http://localhost:5001).

For Linux and macOS, clone the same repository and follow the guide's [shared initialization commands](INSTALLATION.md#5-initialize-on-linux-or-macos), which generate secrets and initialize a fresh database. The Windows script is also for fresh setup; use `docker compose -f compose.local.yaml up -d` for later starts.

The startup script generates local secrets in an ignored `.env` file. No AI API key is required for Ollama. Chat drafts SQL for manual use; Build dashboard creates and runs bounded read-only queries for PostgreSQL and MySQL/MariaDB. Review generated results before publishing.

This configuration is for local use at `127.0.0.1:5001`; shared hosting requires separate security and network configuration.

An administrator can select a provider, enter its model ID and their own API key in Settings. Compatible services also accept a custom API base URL. Each installation uses its own provider account; no project owner's key is bundled. Providers with other API protocols require an adapter.

See [LOCAL_PROJECT.md](LOCAL_PROJECT.md) for configuration, hardware limits, and validation, and [BRANDING.md](BRANDING.md) for the product identity.

## Contribute

Start with [CONTRIBUTING.md](CONTRIBUTING.md) for the code map, development setup and test commands. The app uses Flask/Python and React/TypeScript, with PostgreSQL for metadata and Redis/RQ for jobs. The Docker build includes frontend assets and connector dependencies.

## Upstream and license

This project is based on an upstream source archive, with custom AI integration, local deployment, and branding. See [UPSTREAM_SOURCE.json](UPSTREAM_SOURCE.json), [README.upstream.md](README.upstream.md), and [LICENSE](LICENSE). Original license and copyright notices are preserved. Original Bi-Dash contributions: © 2026 Slavcho Spasenoski, under BSD-2-Clause. See [NOTICE](NOTICE) and [COPYRIGHT.md](COPYRIGHT.md).

Local credentials, database storage, temporary files, and editor workspaces are excluded from Git.
