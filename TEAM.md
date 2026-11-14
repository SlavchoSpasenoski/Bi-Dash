# Bi-Dash agent team

Updated: 2026-11-10. Agents are scoped to this chat; this file preserves responsibilities, not persistent background processes.

Roadmap update, 2026-11-14: the user explicitly requested scheduled work with this team. ROADMAP.md is the current authorized scope and supersedes the older Reader-visibility deferral below. Recreate roles as needed per bounded milestone: coordinator integrates; programmer implements; QA independently validates; security architect reviews authorization/context leakage. Do not run four agents for routine coordination or duplicate their work. No agents are persistently running between scheduled executions.

## Shared context

Read AGENTS.md, PROJECT_CONTEXT.md, STATUS.md and DATA_ACCESS.md. Agents communicate directly for coordination and record accepted decisions in shared documents. The coordinator assigns file ownership and integrates changes; reread shared files before edits and preserve unrelated work.

## Roles and completed assignments

| Agent | Responsibility | Current work |
| --- | --- | --- |
| /root | Coordinator, integration, API/UI/docs/deployment | Cascading metadata selectors, local browser verification, documentation and deployment |
| /root/programmer | Runtime and database adapters | PostgreSQL/MySQL adapters and admin names-only metadata endpoint |
| /root/qa_tester | Independent tests | Policy/runtime checks, real database tests and metadata endpoint regression tests |
| /root/security_architect | Architecture and security review/integration | Enforcement bypass review and metadata endpoint security review |

## Authorized scope

The user explicitly replaced the previous separate-account design: use the existing shared connection even with full database privileges, with SELECT-only execution enforced inside Bi-Dash and application-enforced schema/table/column rules assigned to Bi-Dash groups for Editors only. Reader dashboard rules are deferred; existing Reader authorization remains. Current implementation supports MySQL8.0/8.4 and MariaDB10.6 first; other engines remain unsupported for enforcement. Root owns group-rule API/UI, app tests, docs and deployment; programmer owns SQL AST compiler; QA owns adversarial/direct-server/runtime tests; security architect owns scope/result/cache/job guards and shared runner review. Local deployment and additive metadata migration are within the requested implementation. No public publication or business database grant changes are authorized.

## Working cycle

Agree architecture and acceptance criteria; assign nonconflicting files; implement; independently test and review; fix findings; verify integration; update shared status. Use isolated disposable PostgreSQL and Redis for destructive integration fixtures. Never use the live metadata database for BaseTestCase.
