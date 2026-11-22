# Selective content migration

Implemented, verified and activated locally, 2026-11-22. Consult STATUS.md for validation evidence and Git/Contabo deployment state.

## Workflow

Active organization Admins use Settings > Migration (also available under Admin > Migration). Export selects components, chooses whether to include connection/provider secrets, and supplies a package passphrase of at least 12 characters. Download the .bidash file and retain its passphrase separately. On the receiving Bi-Dash installation, upload the file, enter its passphrase, resolve source mappings, inspect the preview and explicitly review it before Import. This feature moves logical organization content; it does not restore a PostgreSQL backup or change the destination installation keys.

Components: Queries with visualizations/parameters, Dashboards/widgets, Data sources, Users, Groups, table/column rules, role restrictions/execution limits, Query history, AI configuration/context. Dashboards and history include Queries; Users include Groups; rules include Groups and Data sources. Queries export the organization's active set plus required dropdown dependencies. Component selection is not per-report selection. Unsupported connectors or unresolved dependencies are errors rather than silently omitted objects.

Without Users, every imported Query/Dashboard belongs to the importing Admin. Old per-user shares and group memberships are not recreated. Groups/rules are off by default; selecting them without Users creates empty groups, allowing the Admin to assign new members later. With Users selected, new accounts are imported disabled and do not receive source passwords, old login passwords, sessions or old API tokens. Review imported identities/roles and configure authentication before enabling accounts. Existing users, including the bootstrap Admin, are never replaced. Conflicting identities must be resolved before importing.

Data sources are newly created, receive isolated permissions and are paused after commit when Redis is available. No default-group source grant is created. Omitting rules also omits source-group bindings; an empty rule configuration must never accidentally mean unrestricted source access. With Rules selected, both ordinary source-group permissions and table/column rules refer only to new sources and new cloned groups, never destination builtin groups. Without Users, these groups have no members. Admin/super_admin group capabilities are not imported as ordinary group permissions. If sources are excluded, each source reference must map to a valid same-organization connector of the same type; those existing source permissions remain authoritative and can make standalone Queries readable to existing Editors.

## Package and destination behavior

Version 1 uses bounded JSON encrypted with Fernet and a PBKDF2-HMAC-SHA256 key derived from the package passphrase and random salt (600,000 iterations). Envelope limit is 20 MiB and logical content limit 12 MiB. The passphrase, package and plaintext credentials are not stored in browser localStorage, receipts or URLs. Passwords/keys are optional package material; when included they are decrypted by the exporting installation into the encrypted transport payload and re-encrypted under the destination's existing keys on import. Do not replace destination REDASH_SECRET_KEY or REDASH_COOKIE_SECRET.

All object references receive destination IDs. Query parameters/dropdown references, visualizations, widget layouts, mappings, ownership and selected grants are validated/remapped together. Existing target resources are not overwritten. Role-policy and AI component selection can update destination organization settings and must be reviewed in the preview. No authentication-domain/SSO or whole organization settings object is copied.

Old execution scopes, latest results, cache/job IDs, schedules, API keys and user sessions are excluded. No import action executes SQL, contacts a source/provider, sends invitations or starts schedules. External business database contents, uploaded/external source files, Docker volumes, Ollama models, domain/TLS/network configuration and Redis operational state are outside this package. Configure connectivity/models separately.

Preview tokens expire after 20 minutes and bind the Admin, organization, encrypted package, source mapping and destination state. Changing any of those requires another preview. Import revalidates inside a metadata transaction. The migration_runs receipt and all content commit together; failure rolls back the content and receipt. A package cannot accidentally be applied twice to the same organization. A different package can intentionally contain overlapping report definitions, so review duplicate names.

## Installation and limits

Both installations need a Bi-Dash release supporting format version 1 and the required connectors. Additive migration b3d9e1a42f86 follows f1a7c9e20b64 and adds import receipts only; no reports are reassigned by upgrading. Downgrading the receipt table removes replay protection/history of imported package IDs, not imported content. Preserve a metadata backup before deployment or rollback.

This is separate from full-environment recovery, which still requires a metadata backup, matching application release and original installation encryption/configuration material. Selective packages are not a substitute for disaster-recovery backups. Exact test coverage, connector exclusions and remaining limitations are recorded with the finished implementation in STATUS.md.

Version 1 does not migrate Query Results/derived sources, Python, JSON/CSV/Excel or URL connectors with external/code dependencies. Active reports and required dropdown dependencies are included; arbitrary archived report collections are not selected. Missing/retired historical sources or unresolved old dropdown references block History export rather than fabricate definitions. Export without History or resolve those dependencies. User identity conflicts block import rather than merging or replacing the destination bootstrap Admin. Large-installation load performance has not been benchmarked.
