# Bi-Dash project instructions

## Start of every chat

- Read `PROJECT_CONTEXT.md` and `STATUS.md` before starting project work.
- For coordinated multi-agent work, read `TEAM.md` for roles, communication and current scope.
- Read the topic-specific documents linked there when relevant. Inspect current code and Git state before relying on historical notes.
- Use these shared files to continue across chats; do not ask the user to repeat context already recorded here.
- Other chats are not automatically included. Mark missing information as unknown rather than inventing requirements or completed work.

## Keep context current

- After meaningful changes or decisions, update `STATUS.md` with the outcome, actual validation, unresolved issues and concrete next steps before the final response.
- Update `PROJECT_CONTEXT.md` when durable requirements, architecture or decisions change. Keep it concise and link to detailed documents instead of duplicating them.
- Distinguish implemented behavior, documented historical verification, checks run in this chat and work still proposed. Never claim a check passed unless there is evidence.
- Date updates. Replace obsolete status instead of accumulating a transcript. Preserve unrelated work from other chats and inspect files again before editing shared status.
- Never put secrets, credentials, private business data or complete chat transcripts in these files.

## Project conventions

- Product name: Bi-Dash. Preserve upstream Redash technical identifiers and legal attribution where appropriate.
- Use Macedonian for conversation with this user unless requested otherwise; follow existing file and code language conventions.
- Preserve installation secrets, persistent Docker volumes and user data. Use disposable resources for integration tests.
- Ollama is the documented default. Provider selection is explicit; do not introduce silent paid-provider fallback.
- Keep relevant product, installation and user documentation aligned with behavior. Treat inherited upstream plans as historical unless confirmed for Bi-Dash.
- Run checks appropriate to the actual change. Documentation-only context updates do not require rebuilding the app or invoking AI providers.

## Useful checks

- Local AI regression tests: `python -m unittest discover -s tests_local -v` (requires project dependencies).
- Container alternative: `docker compose -f compose.local.yaml run --rm --no-deps --entrypoint python server -m unittest discover -s tests_local -v`.
- Compose validation: `docker compose -f compose.local.yaml config --quiet`.
- Frontend types: `pnpm type-check`; consult `package.json` for other scripts.
- Website locale checks: `node website/scripts/check-landing-locales.mjs`.
- Do not run image builds and local 7B model inference simultaneously on this machine; documented memory pressure can interrupt Docker.
