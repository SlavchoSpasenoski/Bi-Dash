// Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
// SPDX-License-Identifier: BSD-2-Clause
import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Form, Input, Select, Spin, Typography } from "antd";
import Link from "@/components/Link";
import { axios } from "@/services/axios";
import DataSource from "@/services/data-source";

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const requestId = () => window.crypto?.randomUUID?.() || "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g,
  (c) => { const r = Math.floor(Math.random() * 16); return (c === "x" ? r : (r & 3) | 8).toString(16); });

export default function DashboardBuilder({ extraContext, onBusyChange, onOpenDraft }) {
  const [sources, setSources] = useState([]);
  const [sourceId, setSourceId] = useState(null);
  const [tables, setTables] = useState([]);
  const [selected, setSelected] = useState([]);
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(true);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [clarification, setClarification] = useState("");
  const [draft, setDraft] = useState(null);
  const [progress, setProgress] = useState({});
  const id = useRef(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    axios.get("api/ai/dashboards").then((data) => {
      if (!alive.current) return;
      setSources(data.data_sources);
      if (data.data_sources.length === 1) setSourceId(data.data_sources[0].id);
    }).catch((err) => {
      if (alive.current) setError(err.response?.data?.message || "Could not load builder data sources.");
    }).finally(() => { if (alive.current) setLoading(false); });
    return () => { alive.current = false; };
  }, []);
  useEffect(() => {
    let active = true;
    setTables([]);
    setSelected([]);
    id.current = null;
    if (!sourceId) return;
    setSchemaLoading(true);
    DataSource.fetchSchema({ id: sourceId }).then((schema) => {
      if (active) setTables(schema);
    }).catch(() => {
      if (active) setError("Could not load the schema. Check the data-source connection and schema worker.");
    }).finally(() => { if (active) setSchemaLoading(false); });
    return () => { active = false; };
  }, [sourceId]);

  function updateStatus(queryId, status) {
    if (alive.current) setProgress((previous) => ({ ...previous, [queryId]: status }));
  }

  async function execute(query) {
    let response;
    for (let retry = 0; retry < 4; retry += 1) {
      try {
        response = await axios.post(`api/queries/${query.id}/results`, { max_age: 0 });
        break;
      } catch (err) {
        if (err.response?.status !== 429 || retry === 3) throw err;
        updateStatus(query.id, "Waiting for a query slot…");
        await pause(2000);
        if (!alive.current) return;
      }
    }
    for (let poll = 0; response.job && poll < 300; poll += 1) {
      if (!alive.current) return;
      const job = response.job;
      if (job.status === 4 || job.status === 5) throw new Error(job.error || "Query failed.");
      if (job.status === 3) {
        response = await axios.get(`api/query_results/${job.query_result_id || job.result}`);
        break;
      }
      updateStatus(query.id, job.status === 1 ? "Queued…" : "Running…");
      await pause(1000);
      response = await axios.get(`api/jobs/${job.id}`);
    }
    if (!response.query_result) throw new Error("The query is still queued or running. Check the saved draft later.");
    const columns = response.query_result.data.columns.map((column) => column.name);
    if (query.columns.some((column) => !columns.includes(column))) {
      await axios.post(`api/visualizations/${query.visualization_id}`, { type: "TABLE", options: { itemsPerPage: 20 } });
      updateStatus(query.id, "Ready as a table — chart aliases did not match the result.");
    } else updateStatus(query.id, "Ready");
  }

  async function build() {
    if (busy || !sourceId || !selected.length || !prompt.trim()) return;
    setBusy(true);
    onBusyChange(true);
    setError("");
    setClarification("");
    setProgress({});
    id.current ||= requestId();
    try {
      const result = await axios.post("api/ai/dashboards", {
        request_id: id.current, data_source_id: sourceId, tables: selected,
        prompt: prompt.trim(), context: extraContext,
      });
      if (!alive.current) return;
      if (result.clarification) {
        setClarification(result.clarification);
        return;
      }
      setDraft(result);
      for (const query of result.queries) {
        if (!alive.current) return;
        updateStatus(query.id, "Preparing…");
        try { await execute(query); }
        catch (err) { updateStatus(query.id, err.response?.data?.job?.error || err.response?.data?.message || err.message || "Could not run this query."); }
      }
    } catch (err) {
      if (alive.current) setError(err.response?.data?.message || "Could not create a dashboard. Retry the same request safely.");
    } finally {
      if (alive.current) setBusy(false);
      onBusyChange(false);
    }
  }

  return <div className="redash-ai-builder">
    <Typography.Title level={4}>Describe it. Get a draft dashboard.</Typography.Title>
    <Alert type="info" showIcon message="Creates real queries and visualizations"
      description="Selected table schemas, saved AI context and your prompt are sent to your AI provider. The draft runs read-only queries with up to 60 seconds per query, or your stricter limit. Row limits are controlled by the query editor’s Limit 1000 checkbox. Review results before publishing." />
    {loading ? <Spin /> : <Form layout="vertical" style={{ marginTop: 20 }}>
      <Form.Item label="Data source" required>
        <Select value={sourceId} disabled={busy} placeholder="Choose PostgreSQL or MySQL/MariaDB"
          options={sources.map((source) => ({ value: source.id, label: `${source.name} · ${source.type}` }))}
          onChange={(value) => { setSourceId(value); setDraft(null); }} />
      </Form.Item>
      {!sources.length && <Alert type="warning" message="No supported data source with query access is available." />}
      <Form.Item label="Relevant tables (up to 12)" required
        extra="Choose the tables needed for this report. Exact names and columns are supplied automatically; no schema copying is needed.">
        <Select mode="multiple" showSearch value={selected} loading={schemaLoading} disabled={busy || !sourceId}
          optionFilterProp="label" placeholder="Search table names…" options={tables.map((table) => ({ value: table.name, label: table.name }))}
          onChange={(values) => { setSelected(values.slice(0, 12)); id.current = null; setDraft(null); }} />
      </Form.Item>
      <Form.Item label="What report or dashboard should I create?" required>
        <Input.TextArea rows={6} maxLength={8000} value={prompt} disabled={busy} className="redash-ai-prompt"
          placeholder="Example: Monthly sales this year, sales by product, and the top 10 customers. Use a line chart, bar chart and table."
          onChange={(event) => { setPrompt(event.target.value); id.current = null; setDraft(null); setClarification(""); }} />
      </Form.Item>
      {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} />}
      {clarification && <Alert type="info" message="More information is needed" description={clarification} style={{ marginBottom: 12 }} />}
      <Button type="primary" loading={busy} disabled={!sourceId || !selected.length || !prompt.trim() || schemaLoading}
        onClick={build}>{busy ? (draft ? "Preparing report results…" : "Designing dashboard…") : draft ? "Retry report results" : "Create draft dashboard"}</Button>
      {draft && <div className="redash-ai-draft-result">
        <Typography.Title level={4}>{draft.title}</Typography.Title>
        <p>{draft.description}</p>
        <ul>{draft.queries.map((query) => <li key={query.id}><strong>{query.title}</strong> · {progress[query.id] || "Waiting…"}</li>)}</ul>
        {!busy && <Link.Button type="primary" href={draft.url} onClick={onOpenDraft}>Open draft dashboard</Link.Button>}
        <p className="m-t-10">Saved as an unpublished draft. Normal workspace access rules apply. You can edit its SQL, charts and layout in Bi-Dash.</p>
      </div>}
    </Form>}
  </div>;
}
