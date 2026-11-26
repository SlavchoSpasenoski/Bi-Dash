# Contributing to Bi-Dash

Use the [Bi-Dash repository](https://github.com/SlavchoSpasenoski/Bi-Dash) for issues and pull requests concerning this distribution. For security-sensitive reports, follow SECURITY.md rather than publishing exploit details or secrets.

Describe the problem, the resulting behavior and relevant validation. Keep changes compatible with existing accounts, source configurations and reports. Read README.md, LOCAL_PROJECT.md and the focused AI/access documentation before changing those features. Run the applicable checks; database integration tests must use a disposable database, never an installation's real metadata database.

## Run a local development instance

1. Fork and clone the repository, then create a working branch from `main`.
2. Follow the [installation guide](INSTALLATION.md) to start a local development instance. Use the [update workflow](INSTALLATION.md#8-stop-restart-inspect-and-update) for an existing instance. Database migrations and application restarts are part of updating.
3. Open [local Bi-Dash](http://localhost:5001) and verify the affected workflow with a disposable source or synthetic data. Review [project context](PROJECT_CONTEXT.md) and [current status](STATUS.md) before changing established behavior.
4. Open a pull request with the user-visible change, affected roles or connectors, test evidence and any migration or rollback implications.

The Docker build supplies Python 3.13 and Node.js 24. For frontend-only checks outside Docker, use a Node version allowed by `package.json` and the pinned pnpm release.

## Find the code

| Area | Start here |
| --- | --- |
| Navigation, query editor, dashboards and settings | [client/app](client/app) |
| Chart rendering and visualization tests | [viz-lib](viz-lib) |
| HTTP endpoints and serialization | [redash/handlers](redash/handlers), [redash/serializers](redash/serializers) |
| Metadata models and Alembic migrations | [redash/models](redash/models), [migrations/versions](migrations/versions) |
| Data connectors and background jobs | [redash/query_runner](redash/query_runner), [redash/tasks](redash/tasks) |
| Datasets | [Dataset handlers](redash/handlers/datasets.py), [compiler](redash/dataset_sql.py), [runtime](redash/dataset_report_runtime.py), [UI](client/app/pages/datasets) |
| Access control | [Permissions](redash/permissions.py), [Dashboard access](DASHBOARD_ACCESS.md), [source rules](DATA_ACCESS.md) |
| AI | [AI assistant](redash/ai_assistant.py), [dashboard builder](redash/ai_dashboard.py), [AI guide](AI_DASHBOARDS.md) |
| Tests and fixtures | [tests](tests), [tests_local](tests_local), [browser tests](client/e2e) |

## Validate your change

Run quick Python checks in the locally built image and validate Compose:

```sh
docker compose -f compose.local.yaml run --rm --no-deps --entrypoint python server -m unittest discover -s tests_local -v
docker compose -f compose.local.yaml config --quiet
```

Run frontend checks in a Unix shell, such as WSL on Windows:

```sh
npm install --global pnpm@10.30.3
pnpm install --frozen-lockfile
pnpm type-check
pnpm jest --runInBand
pnpm --filter @redash/viz type-check
pnpm --filter @redash/viz test --runInBand
```

For the backend integration suite, run `python -m pytest tests tests_local` with the development dependencies and disposable PostgreSQL and Redis. Set `REDASH_DATABASE_URL` and `REDASH_REDIS_URL` to the fixtures before starting. The test harness drops and recreates metadata tables and clears its Redis namespace. Connector-specific cases may need additional disposable services and environment variables documented in the test modules. With a source checkout mounted into a built image, point `REDASH_STATIC_ASSETS_PATH` and `REDASH_FLASK_TEMPLATE_PATH` at the image's compiled assets for HTML tests.

For permission changes, check both allowed and denied flows across the HTTP request, queued execution, cached results and export. For a migration, verify upgrade and rollback using disposable metadata with existing records. Document test skips and any remaining validation in the pull request.

Original contributions are distributed under the existing BSD 2-Clause license. Preserve upstream and third-party copyright notices. Add narrowly scoped attribution for original work rather than replacing an inherited file's author or claiming the entire fork. See LICENSE, NOTICE and COPYRIGHT.md. Historical upstream material must remain attributed and must not be presented as a Bi-Dash endorsement.
