# Bi-Dash

Bi-Dash is this project's product identity, built on the open-source Redash codebase. Upstream license and copyright notices are preserved in LICENSE and the source files.

The mark is a geometric B inside a blue rounded square, with a yellow dash. The compact mark is used in navigation, loading screens, notifications, shared dashboards, and browser tabs; the horizontal wordmark is used on account screens.

| Role | Color |
| --- | --- |
| Primary / action | `#2563EB` |
| Secondary accent | `#FACC15` |
| Navigation / ink | `#191C22` |
| Blue on dark backgrounds | `#60A5FA` |
| Canvas | `#F4F6FA` |

Yellow is a small accent, paired with dark text. Success, warning, and error states retain their semantic colors. Both logo assets are editable SVGs under `client/app/assets/images/`.

Product line: **Your data. A clearer direction.**

The rebrand does not rename internal Redash package names, API routes, environment variables, database tables, or Docker volumes. Existing connections, accounts, queries, and local Ollama settings continue to work.

Local verification: the production Docker image builds with a single minifier worker to limit memory use. Browser checks passed for login, SVG assets and favicon, authenticated home, anthracite navigation, AI provider configuration, query page titles, and mobile layouts at 390px. No uncaught browser errors were reported; all six project services were running after deployment.
