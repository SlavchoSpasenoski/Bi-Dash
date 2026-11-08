# Portable Linux installation

Target: Linux x86-64, Docker Engine with Buildx and Compose v2, Bash and OpenSSL.
Ubuntu 24.04 LTS is a suitable starting point. No host Python, Node, PostgreSQL
or Redis is needed. Plan initially for 4 vCPU, 8 GB RAM and 60 GB disk without
local AI; measure actual usage. ARM64 and GPU inference are not validated.

## Install

Install Docker for your distribution: https://docs.docker.com/engine/install/.
Your account must be allowed to use Docker. Then:

```bash
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
cd Bi-Dash
bash scripts/linux.sh install
```

No configuration edits are required. Three independent secrets are generated in
`.env.linux` with owner-only permissions. Frontend and backend build in Docker.
PostgreSQL, Redis, server, worker and scheduler start automatically. Repeating
install preserves secrets and data; it only initializes empty databases and does
not migrate existing schemas. Do not run concurrent installers.

From your computer:

```bash
ssh -N -L 5001:127.0.0.1:5001 your-user@your-server
```

Open http://localhost:5001/setup to create the administrator, then use
http://localhost:5001. This URL works on every machine through the tunnel.
Only loopback can access the app; database, Redis and Ollama ports are unpublished.

## Optional AI

```bash
bash scripts/linux.sh ai
```

This starts Ollama and downloads qwen2.5-coder:7b into a persistent volume.
Repeat after `stop` to start Ollama again. Plan at least 16 GB host RAM for local
AI and measure responsiveness. GPU passthrough is not configured. Alternatively,
select a hosted provider and enter your own model/key in Settings / AI
Configuration. AI needs one of these options; ordinary dashboards do not.

## Operations

```bash
bash scripts/linux.sh status
bash scripts/linux.sh logs
bash scripts/linux.sh stop
bash scripts/linux.sh start
bash scripts/linux.sh check
```

Stop preserves volumes. Never use `down -v` unless deliberately deleting data.
Back up PostgreSQL AND `.env.linux`: the original encryption key is required to
read saved credentials. Migrating existing data requires restoring both, rather
than generating fresh secrets. Never change POSTGRES_PASSWORD on an existing
volume without changing the database role password too.

Before upgrading source, back up the database and secrets, then:

```bash
docker compose --env-file .env.linux -f compose.linux.yaml build server
docker compose --env-file .env.linux -f compose.linux.yaml stop server worker scheduler
docker compose --env-file .env.linux -f compose.linux.yaml run --rm --no-deps server manage db upgrade
bash scripts/linux.sh start
```

If migration fails, keep writers stopped and investigate or restore matching old
source/image, database and secrets. Rolling back an image does not undo migrations.

## Public hosting

This is private staging. Public hosting additionally needs a TLS reverse proxy,
DNS and REDASH_HOST set to the actual HTTPS URL. These installation-specific
values cannot be automatically inferred. Do not publicly expose the development
`compose.yaml`. Optional BIDASH_PORT and REDASH_HOST may be set in the shell or
`.env.linux`; neither is required for default tunnel access.

The fixed Compose project name `bi-dash-linux` preserves volume identity when the
checkout directory changes. Multiple installations on one host need separate
Compose project names (`-p`) and ports. Windows `.env` and compose.local.yaml
are separate and unchanged.

## Validation

The Linux CI workflow builds a fresh installation, checks HTTP readiness and
checks restart/secret persistence on Ubuntu. Review its result for the revision
being deployed. Docker Desktop Linux containers alone do not validate a native
Linux host. No paid AI requests are included in these checks.
