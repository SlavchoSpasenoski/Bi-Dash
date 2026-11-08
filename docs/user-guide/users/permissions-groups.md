---
title: Roles, Permissions & Groups
---

Bi-Dash combines Admin, Editor and Reader roles with group-based data-source access. Administrators configure the policy in **Settings → Roles & Access** and may apply stricter query limits in a user's profile.

- **Admin:** manages users, roles, data sources and organization settings.
- **Editor:** creates and edits queries and dashboards on permitted sources. The default limit is 60 seconds and two running or queued queries. Scheduling, public sharing and archival depend on the administrator's policy.
- **Reader:** views available saved results and can browse, sort and download them when exports are allowed. Readers cannot view SQL, edit reports or execute queries.

Each user belongs to groups. Each source grants Full or View Only access to selected groups. A role does not automatically grant access to an otherwise inaccessible source. Role restrictions remain in force even if a legacy group grants broader actions. A dashboard can contain widgets from different sources; inaccessible widgets do not disclose their data.

Newly invited users can be assigned a role; SSO users default to Reader. Consult **ACCESS_CONTROL.md** in the Bi-Dash repository for compatibility rules, source-specific cancellation behavior, quotas and configuration details.
