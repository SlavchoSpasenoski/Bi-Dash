# Bi-Dash verification — 2026-11-09

Verification used Windows with Docker Desktop Linux containers. Application data
was preserved; database, permission and end-to-end tests used disposable databases,
Redis instances or a separate Compose project on localhost:5002.

## Confirmed fixes

- Browser sessions now receive global CSRF enforcement when
  `REDASH_ENFORCE_CSRF=true`. Flask-Login stores the session identity in `_user_id`;
  the old check only recognized `user_id`. API-key requests remain supported.
- AI connection resets and incomplete HTTP responses produce an actionable
  provider-unavailable error (HTTP 502), instead of an uncaught HTTP 500.
- Query serialization without a request or authenticated user omits SQL and
  credentials instead of raising an exception.
- Initial setup respects the selected newsletter and security-notification
  checkboxes. Unchecked options no longer trigger a subscription task, and the
  administrator password is excluded from any subscription task payload.

## Practical checks

- Production Docker image built from the current source.
- Fresh database initialization succeeded twice without replacing existing data.
- Fresh setup without subscriptions and real password sign-in succeeded.
- A real PostgreSQL connection executed a query through the worker, returning
  the expected aggregate values from synthetic rows.
- Saved query, chart, dashboard widget and CSV export succeeded.
- A browser-session mutation without a CSRF token was rejected.
- Restart preserved the installation secrets and database.
- Existing local application was updated without replacing its volumes or secrets.
- Desktop and mobile navigation, role settings and AI builder controls loaded;
  the updated browser session reported no JavaScript errors.
- Real local Ollama inference returned `SELECT 1 AS verification_value` through
  the application AI client, using only a synthetic prompt and no saved context.
- Frontend TypeScript checking passed. Client tests: 145 passed; visualization
  tests: 161 passed; 62 snapshots passed.
- Broad backend run: 683 passed and one skipped. Its 24 legacy failures were
  traced to outdated permission/authentication fixtures and then covered by an
  affected-file rerun with 81 passing tests. Default application permissions
  were retained.
- Focused AI, access-policy, query-limit, CSRF and setup regression suite:
  77 passed, including real worker execution and PostgreSQL cancellation.
- Setup and administrator access-control checks: 24 passed, including explicit
  denial of permission changes by an Editor who owns the query.
- Standalone MK/EN landing-page checks passed, including 551 local links.
- A clean Linux Node container built the complete Astro site: 133 generated
  pages, plus its static download pages and search index.

## Scope and remaining limits

These results validate the tested Docker installation and synthetic workflows.
They do not certify every third-party database connector or production workload.
Paid AI providers and public-domain HTTPS certificate issuance were not exercised.
Local AI inference must be tested without simultaneously rebuilding the image on
this machine because the 7B model competes with the build for Docker's memory.

Native Windows Astro compilation encountered CommonJS dependency-loading errors;
the same source built successfully in a clean Linux Node container. The application
itself builds and runs inside Docker on Windows.
