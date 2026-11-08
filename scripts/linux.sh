#!/usr/bin/env bash
# Run from any directory; keep installation secrets separate from local development.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo "Install Docker Engine and Compose v2 first." >&2; exit 1; }
docker compose version >/dev/null
compose() { docker compose --env-file .env.linux -f compose.linux.yaml "$@"; }
action=${1:-help}
case "$action" in
  install)
    command -v openssl >/dev/null || { echo "Install openssl first." >&2; exit 1; }
    if [[ ! -e .env.linux ]]; then
      # Noclobber prevents concurrent installers from replacing existing secrets.
      (umask 077; set -o noclobber
        printf '%s\n' \
          "REDASH_COOKIE_SECRET=$(openssl rand -hex 32)" \
          "REDASH_SECRET_KEY=$(openssl rand -hex 32)" \
          "POSTGRES_PASSWORD=$(openssl rand -hex 32)" \
          'REDASH_AI_PROVIDER=ollama' \
          'REDASH_AI_MODEL=qwen2.5-coder:7b' \
          'REDASH_OLLAMA_URL=http://ollama:11434' \
          'REDASH_OLLAMA_TIMEOUT=1800' \
          'REDASH_OLLAMA_KEEP_ALIVE=30m' > .env.linux
      )
    fi
    chmod 600 .env.linux
    compose config --quiet
    docker info >/dev/null
    compose build server
    compose up -d --wait --wait-timeout 120 postgres redis
    compose run --rm --no-deps server create_db
    compose up -d
    echo "Bi-Dash installed. Open http://localhost:${BIDASH_PORT:-5001}/setup through an SSH tunnel."
    echo "Optional local AI: bash scripts/linux.sh ai"
    ;;
  start) compose up -d ;;
  stop) compose down ;;
  status) compose ps ;;
  logs) compose logs --tail 100 -f server worker scheduler ;;
  ai)
    compose --profile local-ai up -d ollama
    compose exec ollama ollama pull qwen2.5-coder:7b
    ;;
  check) compose config --quiet ;;
  *)
    echo "Usage: bash scripts/linux.sh {install|start|stop|status|logs|ai|check}"
    [[ "$action" == help ]] || exit 1
    ;;
esac
