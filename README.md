# Bi-Dash

A business intelligence workspace with local AI, built on [Redash](https://github.com/getredash/redash).

- Query connected data sources and build interactive dashboards.
- Draft SQL with an integrated AI assistant powered by local Ollama.
- Blue and yellow visual identity with anthracite navigation.
- Local Docker deployment with persistent PostgreSQL, Redis, and Ollama storage.

## Run locally on Windows

Install Docker Desktop with Linux containers and PowerShell 7, then run:

```powershell
./scripts/start-local.ps1
docker compose -f compose.local.yaml exec ollama ollama pull qwen2.5-coder:7b
```

Open http://localhost:5001/setup to create your administrator. On later visits, open http://localhost:5001.

The startup script generates local secrets in an ignored `.env` file. No AI API key is required for Ollama. Review generated SQL before running it. The assistant drafts queries and does not execute them.

See [LOCAL_PROJECT.md](LOCAL_PROJECT.md) for configuration, hardware limits, and validation, and [BRANDING.md](BRANDING.md) for the product identity.

## Development checks

```powershell
python -m unittest discover -s tests_local -v
docker compose -f compose.local.yaml config --quiet
```

## Upstream and license

This project is based on an upstream source archive, with custom AI integration, local deployment, and branding. See [UPSTREAM_SOURCE.json](UPSTREAM_SOURCE.json), [README.upstream.md](README.upstream.md), and [LICENSE](LICENSE). Original license and copyright notices are preserved.

Local credentials, database storage, temporary files, and editor workspaces are excluded from Git.
