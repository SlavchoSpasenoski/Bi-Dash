# Dashboard access for signed-in users

Updated: 2026-11-15.

## Assign or revoke access

1. Sign in as Admin or the Editor who owns the dashboard.
2. Open the dashboard. Its owner must publish a draft before assigning viewers.
3. Click **Access** at the top right.
4. In **Users**, select individual accounts; in **Groups**, select group audiences. Click **Save access**.
5. Remove an account/group and save to revoke that grant. Another valid direct or group grant can still provide access.

Recipients must sign in and still need source access. Editor table/column rules remain enforced; inaccessible widgets stay restricted. A forwarded URL grants no access. This dialog never changes source groups, credentials or database grants.

## Roles and ownership

| User | Visibility | Manage viewers | Edit content |
| --- | --- | --- | --- |
| Admin | Same-organization non-archived dashboards, including drafts | All same-organization dashboards | Own dashboards only |
| Editor owner | Own non-archived dashboards, including drafts | Own dashboards | Own dashboards |
| Other Editor | Published dashboards assigned directly or through a group | No | No |
| Reader | Published dashboards assigned directly or through a group | No | No |

Former Editor owners demoted to Reader also require a direct or group published-dashboard grant. Drafts are visible to their Editor/Admin owner and organization Admins. Archived dashboards are excluded from direct reads/lists; an authorized owner can restore through the content-update path.

Only owners publish, change content/layout/parameters, add/remove widgets or fork. Old generic modify grants do not override this. Editing SQL/visualizations that would change another owner's dashboard is denied, including for non-owner Admins. New widgets must use the actor's own query. Legacy cross-owner links are preserved with conservative edit restrictions; create owned query copies where necessary.

## Results and upgrade

Readers can refresh assigned published reports with safe typed parameters on MySQL/MariaDB or PostgreSQL; they cannot submit SQL, edit or reshare. ACL checks cover listing/search/counts/tags/favorites, direct reads, refresh, workers/jobs, cached results and downloads. Server-only result provenance binds report results to dashboard/query/parameters/revision; changes can invalidate queued jobs and cached report results. Unknown/orphan legacy results fail closed unless a current authorized latest-result pointer and matching template prove access. Regenerate unproven results through an assigned report.

Cached Query Results/Python dependencies and dropdown values require query/result authorization. Requestless dropdown lookups without a named actor fail closed; use date/number/value parameters for report refresh.

Anonymous dashboard/query/report object-token links are disabled. Old token rows are retained but cannot bypass grants. Named personal API credentials remain subject to their user's role, grants and source permissions.

Migration `d83b6a21f409` adds `dashboard_read_grants` and nullable `query_results.report_context`. It preserves existing records/source settings and creates no automatic grants. Admins/owners must explicitly assign existing published dashboards to non-owner Editors/Readers. Preserve a metadata backup and installation secrets before migration.

GET/POST `/api/dashboards/<id>/access` is manager-only. POST replaces unique `read_user_ids` with the current integer `revision`; stale saves return409, invalid/inactive/foreign accounts are rejected, and session writes require CSRF. Even an identical save advances revision and may invalidate queued reports. Generic dashboard `/acl` cannot issue edit grants.

## Group dashboard assignments

Admin can also open **Settings > Groups > group > Dashboards**. Search the server-paginated dashboard list and choose **Assign to group** or **Remove assignment**. This edits the same relation as Dashboard > Access > Groups; it preserves direct user grants and grants to other groups. Editor owners use their own Dashboard Access dialog; Reader cannot manage sharing.

Group membership is evaluated dynamically on the server, including list/search, direct reads, report refresh/jobs/workers, results and downloads. New members inherit the group's published dashboard visibility. Removed members lose that path; another grant or owner/Admin visibility may remain. Group sharing never changes source/group/column permissions or ownership. Existing draft/archive grants may be reduced, but new assignments require publication. Group deletion cascades its dashboard grants. Effective access explains direct and group paths.

Migration `e92c71b54a08` adds `dashboard_group_read_grants` with unique dashboard/group pairs and indexes, preserving existing direct grants and creating no automatic group assignments. POST Dashboard Access optionally adds `read_group_ids`; old clients omitting it preserve existing group assignments. Group-side GET/POST `/api/groups/<id>/dashboards` is Admin-only, uses revisioned individual grant/revoke and does not replace other grants. GET supports `q`, `page`, `page_size` (maximum100).

Collections, organization-wide automatic audiences and bulk selection are not included. Group-side dashboard search is paginated; the Dashboard Access candidate selectors still load all candidates. No 10,000-user/dashboard load benchmark is claimed.
