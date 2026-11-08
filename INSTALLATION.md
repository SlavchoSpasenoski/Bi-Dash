# Install Bi-Dash from scratch

This guide installs the repository's local Docker stack on Windows, Linux or macOS. All application dependencies are built inside Linux containers: you do not need host Python, Node.js, PostgreSQL, Redis or a separate Ollama installation.

The stack is intended for local use. It binds the application to `127.0.0.1:5001`, uses local database credentials and does not configure HTTPS, a public domain or email delivery. A shared company deployment needs separate production configuration.

## 1. Requirements

- Git, internet access and Docker with the Compose v2 plugin (`docker compose`).
- Windows: PowerShell 7 and Docker Desktop using Linux containers.
- Linux/macOS: a shell and `openssl` for generating secrets.
- Practical starting point: 16 GB system RAM and 30 GB free disk space for building the application and trying the default 7B model. These are planning estimates, not guaranteed minimums; dependency caches and models can require more space. Allocate sufficient memory to Docker; 8 GB is a starting point for the application build, with additional memory needed when running a model. More RAM and CPU improve local AI responsiveness.
- Access to the databases you want to report on. The included PostgreSQL instance stores Bi-Dash metadata; it does not automatically contain your business data.

The Docker stack does not configure GPU passthrough. Do not assume a host GPU or Apple Silicon acceleration is available to the containerized model. Hosted AI avoids the local model's memory requirement, but the application still needs to build.

The current local deployment has been verified on Windows with Linux containers. The Linux and macOS instructions use the same Compose services; a complete fresh installation has not yet been verified on physical Linux and Mac hosts. Native ARM builds and individual database drivers may have additional compatibility constraints.

## 2. Windows

### Install prerequisites

1. Install [Git for Windows](https://git-scm.com/downloads/win).
2. Install [PowerShell 7](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows). Open **PowerShell 7**, rather than the older Windows PowerShell 5.1.
3. Follow the official [Docker Desktop for Windows installation guide](https://docs.docker.com/desktop/setup/install/windows-install/), including its virtualization/WSL requirements. Restart if requested.
4. Start Docker Desktop and use Linux containers. Wait until Docker is ready.

Check your tools:

```powershell
git --version
$PSVersionTable.PSVersion
docker version
docker compose version
```

Both the Docker client and server must be available. Do not continue if Docker reports that it cannot connect to the daemon.

### Download and initialize

Choose a local development directory, then run:

```powershell
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
Set-Location Bi-Dash
pwsh -NoProfile -File ./scripts/start-local.ps1
```

The script creates `.env` with two independent random secrets if the file does not exist, builds the application, starts PostgreSQL and Redis, initializes the metadata database and starts the remaining services, including Ollama. The first build may take several minutes or longer, depending on network and hardware.

If execution policy blocks the reviewed script, allow it for this process only and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
./scripts/start-local.ps1
```

Use the startup script for a **fresh installation**. For an existing installation, use the restart/update commands below rather than rerunning database initialization.

Continue at [First sign-in](#6-first-sign-in).

## 3. Linux

### Install prerequisites

Install Docker Engine using the official instructions for your distribution, including Buildx and the Compose plugin:

- [Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Debian](https://docs.docker.com/engine/install/debian/)
- [Other supported distributions](https://docs.docker.com/engine/install/)

Install Git and OpenSSL using your distribution's package manager. For Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y git openssl
```

Start Docker and configure access according to the official [Linux post-installation instructions](https://docs.docker.com/engine/install/linux-postinstall/). Docker group membership grants root-level privileges; use the access method appropriate to your environment. The commands below assume your current account can run Docker.

```bash
git --version
openssl version
docker version
docker compose version
```

### Download the project

```bash
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
cd Bi-Dash
```

Continue at [Initialize on Linux or macOS](#5-initialize-on-linux-or-macos).

## 4. macOS

### Install prerequisites

1. Install Git, for example through Apple's Command Line Tools: run `xcode-select --install` and complete the installer.
2. Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/), choosing the download for your Intel or Apple Silicon Mac.
3. Start Docker Desktop, complete initial setup and wait until Docker is ready.
4. Check the tools:

```bash
git --version
openssl version
docker version
docker compose version
```

### Download the project

```bash
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
cd Bi-Dash
```

Continue with the shared initialization below. Start with the native architecture. If a dependency cannot build on Apple Silicon, the alternative is an AMD64 build under [Docker's supported emulation](https://docs.docker.com/build/building/multi-platform/):

```bash
export DOCKER_DEFAULT_PLATFORM=linux/amd64
```

Set this before the build and keep it consistent for later Compose commands in that terminal. Emulation can be substantially slower; it is a compatibility fallback, not a verified guarantee for every connector. Include the architecture and failed dependency when reporting a build problem.

## 5. Initialize on Linux or macOS

Run these commands from the cloned `Bi-Dash` directory. They create `.env` only if it does not already exist and restrict its permissions:

```bash
if [ ! -e .env ]; then
  (
    set -eu
    umask 077
    cookie_secret=$(openssl rand -hex 32)
    data_secret=$(openssl rand -hex 32)
    printf '%s\n' \
      "REDASH_COOKIE_SECRET=$cookie_secret" \
      "REDASH_SECRET_KEY=$data_secret" \
      'OPENAI_API_KEY=' \
      'REDASH_AI_PROVIDER=ollama' \
      'REDASH_AI_MODEL=qwen2.5-coder:7b' \
      'REDASH_OLLAMA_URL=http://ollama:11434' > .env
  )
fi
```

Keep `.env` private and preserve it for backups. Do not replace its secrets on an existing installation: saved AI keys depend on the encryption secret. Do not use the placeholder secrets from `.env.example` unchanged.

Run each command in order and continue only if it succeeds:

```bash
docker compose -f compose.local.yaml config --quiet
docker compose -f compose.local.yaml build server
docker compose -f compose.local.yaml up -d postgres redis
docker compose -f compose.local.yaml run --rm server create_db
docker compose -f compose.local.yaml up -d
```

`create_db` is for a fresh metadata database. Do not use it as an upgrade command or rerun it for an installation containing reports. The initial build downloads application dependencies; no host language runtime is required.

## 6. First sign-in

1. Run `docker compose -f compose.local.yaml ps`. Expect server, worker, scheduler, postgres, redis and ollama services; PostgreSQL and Redis should become healthy.
2. Open **http://localhost:5001/setup** on the same computer.
3. Create your organization and first administrator account. No default administrator password is bundled.
4. On later visits, open **http://localhost:5001**.
5. In **Settings → Data Sources**, add your business database, save and use **Test Connection**.
6. Configure groups, users and **Roles & Access** before sharing reports.

Inside containers, `localhost` refers to the container itself. For a database on the Docker Desktop host, use `host.docker.internal` where appropriate. On Linux Engine, use a reachable host address or explicitly configure a host gateway. Remote database access also depends on network routing, grants and SSL settings.

## 7. Enable AI

### Option A: local Ollama

The Ollama service is started automatically, but **the model is downloaded separately**, once per new model volume:

```bash
docker compose -f compose.local.yaml exec ollama ollama pull qwen2.5-coder:7b
docker compose -f compose.local.yaml exec ollama ollama list
```

These Docker commands work in PowerShell as well. Wait for the download to finish. In **Settings → AI Configuration**, select **Ollama**, Model ID `qwen2.5-coder:7b`, and URL `http://ollama:11434`, then save. No paid API key is needed. The first prompt may take longer while the model loads; CPU execution can be slow.

Models remain in the Docker volume across normal restarts. You do not need a separate Windows/macOS Ollama application for this configuration.

### Option B: a hosted or existing compatible provider

Skip the model download. In **Settings → AI Configuration**, choose OpenAI, Anthropic or Other / OpenAI-compatible and enter your own supported model ID, credentials and base URL when required. No paid account or API key is bundled. A compatible API URL uses the base path, such as `https://provider.example/v1`, without `/chat/completions`.

Choosing a hosted provider does not automatically remove the Ollama service from the local Compose stack. Bi-Dash does not fall back to a paid provider if local AI fails.

### Verify the workflow

Save business definitions in **Settings → AI Context**. Try a small **Ask AI** question, then use **Build dashboard** with a permitted PostgreSQL/MySQL source and a small selection of relevant tables. Review generated SQL and results before publishing. See the [User Guide](USER_GUIDE.md) for detailed instructions.

## 8. Stop, restart, inspect and update

Run commands from the repository directory:

```bash
# Inspect services and recent application logs
docker compose -f compose.local.yaml ps
docker compose -f compose.local.yaml logs --tail 100 server worker

# Stop containers while retaining persistent volumes
docker compose -f compose.local.yaml down

# Start an already initialized installation
docker compose -f compose.local.yaml up -d
```

These commands also work in PowerShell. Do not add `--volumes` / `-v` to `down` unless you intend to delete persistent data, including reports and downloaded models.

Before updating, back up the metadata database and private `.env`, check release/migration instructions and resolve local source changes. A normal application update is:

```bash
git pull --ff-only
docker compose -f compose.local.yaml build server
docker compose -f compose.local.yaml stop server worker scheduler
docker compose -f compose.local.yaml run --rm server manage db upgrade
docker compose -f compose.local.yaml up -d
```

The stop command pauses application writers during migration; schedule downtime for shared deployments. If migration fails, keep writers stopped and investigate before starting the new application. Never substitute `create_db` for migrations. Keep encryption secrets with your backup in protected storage. GitHub contains source code, not your accounts, reports, data or credentials.

## 9. Troubleshooting

| Problem | Action |
| --- | --- |
| Cannot connect to Docker | Start Docker Desktop or the Linux Docker daemon; confirm `docker version` shows a server |
| `docker compose` is unavailable | Install Compose v2; Docker Desktop includes it |
| Windows script fails on cryptography methods | Run it in PowerShell 7, not Windows PowerShell 5.1 |
| Build exits with code 137 or runs out of memory | Increase Docker memory, close other workloads and retry the build |
| Port 5001 is already in use | Stop the conflicting application, or consistently change the Compose host port and `REDASH_HOST` |
| Browser cannot open the app | Check `ps` and server logs; allow startup to finish and use HTTP on port 5001 |
| Remote computer cannot reach Bi-Dash | Expected for this local stack: it binds to loopback. Configure a separate secured deployment for shared access |
| AI reports model not found | Pull the exact Model ID and verify it with `ollama list` |
| AI is slow or times out | Reduce selected tables/widgets/context and check available CPU/RAM; consider a hosted provider |
| Database tables appear but queries fail | Schema can be cached. Run Test Connection and verify the actual host, credentials and network access |
| Apple Silicon build fails | Record the failing dependency; try the documented AMD64 fallback if appropriate |

Further references: [User Guide](USER_GUIDE.md), [local implementation notes](LOCAL_PROJECT.md), [AI dashboard builder](AI_DASHBOARDS.md) and [access controls](ACCESS_CONTROL.md).
