# Access model review

Reviewed 2026-11-15 against the current Bi-Dash implementation. This is a proposal, not authorization to change the remaining role model.

## Current effective checks

1. Role determines actions (Admin settings, Editor creation/AI, Reader saved-report use).
2. Dashboard grant determines visibility; owners/Admin retain their documented exceptions.
3. Source Groups determine source access and Full versus View Only.
4. For sources with rule sets, Editor group allowlists determine permitted tables/columns. Matching groups combine allowed columns; an unrelated rule set does not restrict another source. No rule sets means ordinary source Groups access.
5. Owner determines content editing. Result/job provenance rechecks the same authorization after revocation.

## Confirmed friction

- Fixed: deleted rule sets previously left hidden protection markers denying access despite Full source permission. Modern rule sets are now authoritative.
- Full access in source Groups means source-level execution capability, not an override of active column allowlists or dashboard visibility. Rename to clarify this in a future UI change.
- Editor column allowlists do not apply to Readers; Readers can see assigned report output under source permissions. This is an intentional existing requirement, but must be explicit in role help and tested when demoting users.
- Group allowlists combine by union. A broad group expands access even if another group is narrow. There are no explicit deny rules; do not present a narrow allowlist as a higher-priority deny.
- Query/visualization ownership plus attachment to another owner's dashboard conservatively blocks edits. This preserves dashboard owner-only edits, but encourages copying queries and adds workflow friction.
- The locked-widget message hides whether source membership, a table/column dependency or a result scope failed. Detailed reasons should be available to Admins without leaking SQL/schema to unauthorized users.

## Suggested product model

Keep three visible sections: Role (actions), Data (source plus optional table/column allowlists), Dashboards (viewers and owner). Use one effective-access summary per user with allowed/denied and a precise reason. Label each source mode as Groups access or Restricted tables/columns, derived from the presence of rule sets. Show a warning before removing its final rule set because this restores Groups-level access. Avoid a second independent enable/disable flag.

Keep default-deny when a source has rules but the user has no matching rule. Preserve source and dashboard checks as separate boundaries. Do not grant dashboard viewers arbitrary SQL execution. Consider dashboard-specific immutable query copies or versions to resolve cross-owner query editing friction, rather than granting edit rights to all viewers.

No additional role, Reader, dashboard-grant or query-ownership behavior was changed by this review.

Implemented 2026-11-15: the per-user effective-access report is now available to Admins from the user profile. See EFFECTIVE_ACCESS.md. Source mode naming, role-policy redesign and query-copy/version workflow remain proposals.
