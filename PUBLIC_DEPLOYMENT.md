# Install Bi-Dash on a public Linux server

This deployment gives each installation its own HTTPS address, accounts, database
and secrets. Visitors use a browser; they do not need Docker or an SSH tunnel.
The server operator needs SSH access, Docker Engine with Compose and a domain.
The application supports authenticated users; making the login page reachable
does not give every visitor access to reports.

## 1. Prepare the server

Use a supported Ubuntu server (24.04 LTS is a starting point). Install Git and
OpenSSL, and install Docker Engine, Buildx and Compose using the
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
The operator must have permission to run Docker. Check `docker version` and
`docker compose version` before continuing. See [Linux deployment](LINUX_DEPLOYMENT.md)
for hardware estimates and optional local AI.

Point your domain's DNS A record, for example `bi.example.com`, to the server's
public IPv4 address. Configure an AAAA record only if IPv6 also reaches this server.
Allow inbound TCP 80 and 443 in the hosting provider firewall and host network;
preserve SSH access. These ports must be available for Caddy. An existing web
server on these ports needs integration with its own reverse proxy instead.
Only the proxy publishes public ports; PostgreSQL, Redis and Ollama stay internal.

## 2. Install and create the administrator privately

On the server:

```bash
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
cd Bi-Dash
bash scripts/linux.sh install
```

The repository must be public for anonymous downloads; a private repository
requires GitHub access. Installation generates secrets in `.env.linux` and
initializes a fresh database. It does not import an existing local installation.

Before publishing the site, create its first administrator. On your own computer:

```bash
ssh -N -L 5001:127.0.0.1:5001 your-user@your-server
```

Open `http://localhost:5001/setup` and create the organization and administrator.
Close the tunnel afterwards. This is a one-time bootstrap step, so an unrelated
visitor cannot claim the initial administrator account.

## 3. Enable public HTTPS

On the server, edit `.env.linux` and add exactly one line with your real domain
(hostname only, without `https://`, a port or a path):

```dotenv
BIDASH_DOMAIN=bi.example.com
```

Preserve all existing secrets. Then run:

```bash
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml config --quiet
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml up -d
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml logs --tail 100 proxy server
```

Open `https://bi.example.com` using your real domain. Caddy obtains and renews
certificates and redirects HTTP to HTTPS automatically; DNS and external reachability
must be correct. Certificate storage persists in the Caddy volumes. See
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

Sign in and configure users, groups and data sources. Configure SMTP separately
if you need invitation and password-reset email. Optional AI is described in
[Linux deployment](LINUX_DEPLOYMENT.md#optional-ai).

## Operations and updates

For this deployment, always include **both** Compose files. Do not use
`bash scripts/linux.sh start` or the local Compose commands after enabling public
hosting: they omit the public environment overrides.

```bash
# Status, logs and restart
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml ps
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml logs --tail 100 server proxy
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml up -d

# Stop while preserving data
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml down
```

Before updating, back up PostgreSQL and `.env.linux`, then pull the reviewed source
revision. Build and migrate with the same configuration:

```bash
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml build server
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml stop server worker scheduler
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml run --rm --no-deps server manage db upgrade
docker compose --env-file .env.linux -f compose.linux.yaml -f compose.public.yaml up -d
```

Continue only when each command succeeds. Never use `create_db` for upgrades or
`down -v` to restart. Existing `compose.local.yaml` installations use different
database volumes and secrets: back up and migrate them explicitly before switching
stacks; a fresh Linux installation will not contain their reports or accounts.

## Troubleshooting and validation

- DNS/certificate error: verify A/AAAA records, inbound 80/443 and proxy logs.
- Port already allocated: inspect existing web servers before starting Caddy.
- HTTP 502: check server logs and container readiness; container startup alone
  does not guarantee that the application is ready.
- Login/redirect problems: keep BIDASH_DOMAIN equal to the browser hostname and
  use HTTPS. All application services must run with both Compose files.

The Compose configuration can be checked locally. A successful public deployment
also requires verification on the target server: trusted HTTPS, HTTP redirect,
administrator login, a saved query, worker execution and persistence after restart.
Configuration validation alone does not verify DNS or certificate issuance.
