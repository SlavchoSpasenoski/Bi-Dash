# Group and collection dashboard sharing proposal

2026-11-15. The dynamic group-grant portion is now implemented and deployed locally; see DASHBOARD_ACCESS.md and STATUS.md. Collections, bulk actions and organization-wide audiences remain proposals. The verified limitations below describe the pre-group implementation.

## Verified current limitations

DashboardReadGrant stores only (dashboard_id, user_id). Access POST accepts read_user_ids only, capped at 1,000 IDs, replaces the complete list and fetches all user candidates. There is no dashboard group grant or collection ACL. Source groups do not implicitly share dashboards. Effective access currently walks all sources/dashboards/widgets for one user and is not load-tested at 10,000 dashboards/users.

## Proposed model

- Reuse organization Groups as audiences, with separate dashboard VIEW grants. Assigning a dashboard to a group must not change group data-source permissions or role.
- Add dashboard group grants without expanding them into per-user rows. Membership changes apply dynamically.
- Add flat dashboard Collections: collection-to-dashboard membership and collection VIEW grants to Groups. Keep the first version free of nested inheritance and explicit deny overrides. Existing per-user dashboard grants remain optional exceptions.
- Offer an explicit Admin-only audience for all active organization users. Do not infer this from the legacy default group; some users have only custom group memberships. Organization-wide access remains authenticated and excludes drafts/archives. It is not anonymous public sharing.
- Admin manages collections, their contents, audiences and organization-wide access. An Editor owner can share only their own published dashboards to permitted same-org groups/users, not manage another owner's collection or content. Reader cannot share. Existing owner-only content editing remains.

Visible = same-org active account AND non-archived AND (existing Admin/Editor-owner exception OR published AND [direct user grant OR direct group grant OR collection group grant OR explicit organization audience]). Query/source/table/column/result checks remain separate. Revoking one path does not revoke another valid path; Effective access must list every granting path.

## UX and large batches

Dashboard Access has Users and Groups sections. Collections have Dashboards and Viewers sections. Admin bulk actions select dashboards across server-side filtered pages, show the exact affected count, distinguish current selection from future collection membership and offer additive grant/revoke operations. Adding a dashboard to a collection immediately exposes it to that collection's viewers; show the audience before saving. Preserve unrelated direct grants.

Use server-side pagination/search for candidates, dashboards, collection members and Effective access detail. Never load 10,000 user options or walk 10,000 dashboards per screen request. Large bulk writes use bounded transactional batches with authorization rechecks, progress, idempotency and an audit record, not a single full-list replacement payload.

## Enforcement, upgrade and validation

Use indexed relational joins/EXISTS before list pagination/counting; do not build 100 million user-dashboard grant rows for a 10,000 by 10,000 audience. Reference: PostgreSQL multicolumn indexes https://www.postgresql.org/docs/18/indexes-multicolumn.html . Measure actual queries with EXPLAIN/ANALYZE on synthetic data before making scale claims.

Centralize the visibility predicate and reuse it for list/detail/search/favorites/tags, query dependencies, results/downloads, refresh jobs and workers. Group membership and collection/audience revisions must invalidate permission snapshots, with current authorization rechecked at admission/execution/poll/result access. No browser-only checks. Invalid/foreign/disabled identities remain denied.

Additive migrations preserve existing direct grants and default all new sharing relations to empty; no broad automatic shares. Test direct/group/collection overlap, removal/reassignment, member removal, source revocation, drafts, archives, other organizations, stale revisions, cached results and queued work. Benchmark 10,000 users/dashboards plus representative widgets, pagination and revocation; current implementation has no such benchmark.
