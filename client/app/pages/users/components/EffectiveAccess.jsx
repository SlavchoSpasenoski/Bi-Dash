import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Collapse, Descriptions, Empty, Input, Modal, Space, Spin, Table, Tag, Typography } from "antd";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";

function AccessDecision({ value }) {
  return <><Tag color={value.allowed ? "green" : "red"}>{value.allowed ? "Allowed" : "Denied"}</Tag>{value.reason}</>;
}

export function EffectiveAccessReport({ report }) {
  const [search, setSearch] = useState("");
  const term = search.trim().toLowerCase();
  const matches = row => !term || row.name.toLowerCase().includes(term);
  return <>
    {report.generated_at && <Typography.Paragraph type="secondary">Permissions loaded: {new Date(report.generated_at).toLocaleString()}</Typography.Paragraph>}
    <Descriptions bordered size="small" column={2} title="1. Identity and role">
      <Descriptions.Item label="User">{report.user.name} · {report.user.email}</Descriptions.Item>
      <Descriptions.Item label="Role">{report.user.role} · {report.user.disabled ? "Disabled" : "Active"}</Descriptions.Item>
      <Descriptions.Item label="Groups" span={2}>{report.groups.map(g => g.name).join(", ") || "No groups"}</Descriptions.Item>
      <Descriptions.Item label="Query timeout">{report.limits ? `${report.limits.query_timeout_seconds} seconds` : "No role limit (Admin)"}</Descriptions.Item>
      <Descriptions.Item label="Running + queued queries">{report.limits?.max_concurrent_queries ?? "No role limit (Admin)"}</Descriptions.Item>
    </Descriptions>
    <Typography.Title level={4}>2. Actions</Typography.Title>
    <Table size="small" rowKey="key" pagination={false} dataSource={report.actions} columns={[
      { title: "Action", dataIndex: "label", width: "30%" },
      { title: "Effective permission and reason", render: (_, row) => <AccessDecision value={row} /> },
    ]} />
    <Input.Search aria-label="Filter sources and dashboards" placeholder="Filter sources and dashboards by name" value={search}
      onChange={event => setSearch(event.target.value)} allowClear style={{ marginTop: 20, marginBottom: 8 }} />
    <Typography.Title level={4}>3. Data sources and table / column access</Typography.Title>
    <Typography.Paragraph>Groups grant source access. A source with rule sets additionally limits Editors to matching group allowlists. Reader access covers assigned report output.</Typography.Paragraph>
    <Collapse>
      {report.sources.filter(matches).map(source => <Collapse.Panel key={source.id} header={<Space wrap>
        <strong>{source.name}</strong><Tag>{source.type}</Tag><Tag>{source.mode === "restricted" ? "Rule sets configured" : "Groups access"}</Tag>
        <Tag color={source.view.allowed ? "green" : "red"}>{source.view.allowed ? "Source access" : "Source denied"}</Tag>
      </Space>}>
        <p><strong>Source access: </strong><AccessDecision value={source.view} /></p>
        <p><strong>Submit SQL: </strong><AccessDecision value={source.execute} /></p>
        <p><strong>Matching source groups: </strong>{source.groups.map(g => `${g.name} (${g.level === "full" ? "Full / execute" : "View Only"})`).join(", ") || (report.user.role === "admin" ? "None (Admin does not require membership)" : "None")}</p>
        {source.table_scope === "allowlist" ? <Table size="small" pagination={{ pageSize: 10 }} rowKey={row => JSON.stringify([row.catalog, row.schema, row.table])}
          dataSource={source.tables} columns={[
            { title: "Schema / database", dataIndex: "schema" }, { title: "Table", dataIndex: "table" },
            { title: "Allowed columns", render: (_, row) => row.columns.join(", ") },
          ]} /> : <p><strong>Table / column scope: </strong>{({ all: "No Bi-Dash table/column allowlist restriction. Database and SQL validation still apply.", report_output: "Saved report output only; Editor column rules do not apply.", none: "No data access." })[source.table_scope]}</p>}
      </Collapse.Panel>)}
    </Collapse>
    {!report.sources.filter(matches).length && <Empty description="No matching data sources" />}
    <Typography.Title level={4}>4. Dashboards and report widgets</Typography.Title>
    <Typography.Paragraph>View, edit content and manage viewers are separate permissions. An allowed query definition does not prove cached-result access or successful refresh.</Typography.Paragraph>
    <Collapse>
      {report.dashboards.filter(matches).map(board => <Collapse.Panel key={board.id} header={<Space wrap>
        <strong>{board.name}</strong><Tag>{board.state}</Tag><Tag color={board.view.allowed ? "green" : "red"}>{board.view.allowed ? "Visible" : "Not visible"}</Tag>
        <span>{board.widgets.filter(w => w.allowed).length}/{board.widgets.length} query widgets authorized</span>
      </Space>}>
        <p><strong>View: </strong><AccessDecision value={board.view} /></p>
        <p><strong>Edit content: </strong><AccessDecision value={board.edit} /></p>
        <p><strong>Manage viewers: </strong><AccessDecision value={board.share} /></p>
        <Table size="small" rowKey="id" pagination={{ pageSize: 10 }} dataSource={board.widgets} columns={[
          { title: "Query", dataIndex: "name" }, { title: "Source", render: (_, row) => report.sources.find(s => s.id === row.source_id)?.name || "Unavailable" },
          { title: "Definition access and reason", render: (_, row) => <AccessDecision value={row} /> },
        ]} locale={{ emptyText: "No query widgets" }} />
      </Collapse.Panel>)}
    </Collapse>
    {!report.dashboards.filter(matches).length && <Empty description="No matching dashboards" />}
    <Typography.Title level={4}>How to read this report</Typography.Title>
    <ul>{report.notes.map(note => <li key={note}>{note}</li>)}</ul>
  </>;
}

export default function EffectiveAccess({ userId }) {
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open || !currentUser.isAdmin) return;
    let cancelled = false;
    setReport(null); setLoading(true); setError("");
    axios.get(`api/users/${userId}/effective-access`).then(data => { if (!cancelled) setReport(data); })
      .catch(err => { if (!cancelled) setError(err.response?.data?.message || "Could not load effective access."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, userId, revision]);
  if (!currentUser.isAdmin) return null;
  return <>
    <Button onClick={() => setOpen(true)}>Effective access</Button>
    <Modal visible={open} title="Effective access" width={1100} onCancel={() => setOpen(false)} destroyOnClose
      footer={<Space><Button disabled={loading} onClick={() => setRevision(value => value + 1)}>Reload permissions</Button><Button onClick={() => setOpen(false)}>Close</Button></Space>}>
      <Alert showIcon type="info" message="Read-only permissions review" description="This report explains saved permissions. It does not change access or test database connections, queries or AI providers." style={{ marginBottom: 16 }} />
      {loading && <Spin aria-label="Loading effective access" />}
      {error && <Alert type="error" message={error} />}
      {!loading && report && <EffectiveAccessReport key={`${userId}-${revision}`} report={report} />}
    </Modal>
  </>;
}

EffectiveAccess.propTypes = { userId: PropTypes.number.isRequired };
