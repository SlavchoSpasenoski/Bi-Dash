import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Input, Space, Table, Tag } from "antd";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import wrapSettingsTab from "@/components/SettingsWrapper";
import Layout from "@/components/layouts/ContentWithSidebar";
import * as Sidebar from "@/components/items-list/components/Sidebar";
import { currentUser } from "@/services/auth";
import { axios } from "@/services/axios";
import routes from "@/services/routes";

export function GroupDashboards({ groupId }) {
  const [group, setGroup] = useState(null);
  const [data, setData] = useState(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    if (!currentUser.isAdmin) return;
    let cancelled = false;
    setGroup(null);
    axios.get(`api/groups/${groupId}`).then(result => { if (!cancelled) setGroup(result); })
      .catch(err => { if (!cancelled) setError(err.response?.data?.message || "Could not load group."); });
    return () => { cancelled = true; };
  }, [groupId]);

  useEffect(() => {
    if (!currentUser.isAdmin) return;
    let cancelled = false;
    setLoading(true); setData(null); setError("");
    axios.get(`api/groups/${groupId}/dashboards`, { params: { q: query, page, page_size: pageSize } })
      .then(result => { if (!cancelled) setData(result); })
      .catch(err => { if (!cancelled) setError(err.response?.data?.message || "Could not load dashboard assignments."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [groupId, query, page, pageSize, revision]);

  if (!currentUser.isAdmin) return <Alert type="error" message="Administrator access required." />;

  async function assign(board) {
    if (saving !== null) return;
    setSaving(board.id); setError(""); setSaved("");
    try {
      await axios.post(`api/groups/${groupId}/dashboards`, {
        dashboard_id: board.id, revision: board.revision, assigned: !board.assigned,
      });
      setSaved(board.assigned ? "Group dashboard assignment removed. Other valid grants may still allow access." : "Dashboard assigned to the group. Members still need the corresponding data permissions.");
      setRevision(value => value + 1);
    } catch (err) {
      setError(err.response?.data?.message || "Could not change dashboard access. Reload before trying again.");
    } finally { setSaving(null); }
  }

  const menu = [
    { key: "users", href: `groups/${groupId}`, title: "Members" },
    { key: "datasources", href: `groups/${groupId}/data_sources`, title: "Data Sources" },
    { key: "dashboards", href: `groups/${groupId}/dashboards`, title: "Dashboards" },
  ];
  return <div data-test="GroupDashboards">
    <h3>{group?.name || "Group"}</h3>
    <Layout>
      <Layout.Sidebar><Sidebar.Menu items={menu} selected="dashboards" /></Layout.Sidebar>
      <Layout.Content>
        <h4>Dashboard visibility</h4>
        <Alert showIcon type="info" message="Share dashboards with this group's members"
          description="These are the same group assignments managed in Dashboard → Access → Groups. Membership is evaluated dynamically. This grants viewing only, does not change data-source or table/column permissions, and does not grant editing or sharing rights." />
        <Space wrap style={{ marginTop: 16, marginBottom: 16 }}>
          <Input.Search aria-label="Search dashboards" placeholder="Search dashboards" value={search} allowClear
            onChange={event => setSearch(event.target.value)}
            onSearch={value => { setQuery(value.trim()); setPage(1); setSaved(""); }} />
          <Button disabled={loading || saving !== null} onClick={() => setRevision(value => value + 1)}>Reload dashboards</Button>
        </Space>
        {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} />}
        {saved && <Alert type="success" message={saved} style={{ marginBottom: 12 }} />}
        <Table rowKey="id" size="small" loading={loading} dataSource={data?.results || []}
          pagination={{ current: data?.page || page, total: data?.count || 0, pageSize: data?.page_size || pageSize,
            showSizeChanger: true, pageSizeOptions: ["25", "50", "100"],
            onChange: (nextPage, nextSize) => { setPage(nextSize !== pageSize ? 1 : nextPage); setPageSize(nextSize); setSaved(""); },
            showTotal: total => `${total} dashboards`, }}
          columns={[
            { title: "Dashboard", dataIndex: "name" },
            { title: "State", render: (_, board) => <Tag>{board.is_archived ? "Archived" : board.is_draft ? "Draft" : "Published"}</Tag> },
            { title: "Group assignment", render: (_, board) => <Tag color={board.assigned ? "green" : "default"}>{board.assigned ? "Assigned" : "Not assigned"}</Tag> },
            { title: "Action", render: (_, board) => <Button danger={board.assigned} loading={saving === board.id}
              disabled={loading || saving !== null || (!board.assigned && (board.is_draft || board.is_archived))}
              onClick={() => assign(board)}>{board.assigned ? "Remove assignment" : "Assign to group"}</Button> },
          ]}
          locale={{ emptyText: loading ? "Loading dashboards…" : "No dashboards match this search." }} />
        <p>Only published dashboards can receive new assignments. Existing assignments can be removed from drafts or archived dashboards. Owners and administrators retain their existing access.</p>
      </Layout.Content>
    </Layout>
  </div>;
}

GroupDashboards.propTypes = { groupId: PropTypes.string.isRequired };
const GroupDashboardsPage = wrapSettingsTab("Groups.Dashboards", null, GroupDashboards);
routes.register("Groups.Dashboards", routeWithUserSession({
  path: "/groups/:groupId/dashboards", title: "Group Dashboards",
  render: props => <GroupDashboardsPage key={props.groupId} {...props} />,
}));
