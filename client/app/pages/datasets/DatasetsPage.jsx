import React, { useEffect, useRef, useState } from "react";
import Alert from "antd/lib/alert";
import AutoComplete from "antd/lib/auto-complete";
import Button from "antd/lib/button";
import Card from "antd/lib/card";
import Input from "antd/lib/input";
import Select from "antd/lib/select";
import Table from "antd/lib/table";
import Link from "@/components/Link";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import { clientConfig } from "@/services/auth";
import { axios } from "@/services/axios";
import location from "@/services/location";
import routes from "@/services/routes";

import "./datasets.css";

const operators = [
  { value: "+", label: "Add (+)" },
  { value: "-", label: "Subtract (−)" },
  { value: "*", label: "Multiply (×)" },
  { value: "/", label: "Divide (÷)" },
];
let nextRowId = 0;

function fieldRow(name = "", expression = { field: "" }) {
  return { id: ++nextRowId, name, expression };
}

function readable(expression) {
  if ("field" in expression) return expression.field;
  if ("decimal" in expression) return expression.decimal;
  return `(${readable(expression.left)} ${expression.op} ${readable(expression.right)})`;
}

function operand(value) {
  return /^-?\d+(\.\d+)?$/.test(value) ? { decimal: value } : { field: value };
}

function errorMessage(error) {
  return error.response?.data?.message || error.message || "The request failed.";
}

function DatasetList() {
  const [items, setItems] = useState([]);
  const [after, setAfter] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load(cursor = 0) {
    setLoading(true);
    setError("");
    try {
      const data = await axios.get("api/datasets", { params: { after: cursor } });
      setItems(previous => cursor ? [...previous, ...data.results] : data.results);
      setAfter(data.next_after);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  return (
    <main className="datasets-page">
      <header className="datasets-header">
        <h1>Datasets</h1>
        <Link href="queries">Choose a saved query</Link>
      </header>
      {error && <Alert type="error" message={error} showIcon />}
      <Table
        dataSource={items} rowKey="id" loading={loading} pagination={false}
        locale={{ emptyText: "Open a saved query and select Create dataset to get started." }}
        columns={[
          { title: "Name", dataIndex: "name", render: (name, item) => <Link href={`datasets/${item.id}`}>{name}</Link> },
          { title: "Version", dataIndex: "published_revision", render: version => version ? `Published v${version}` : "Draft" },
        ]}
      />
      {after !== null && <Button onClick={() => load(after)} disabled={loading}>Load more</Button>}
    </main>
  );
}

function FieldEditor({ row, onChange, onRemove, editable, sourceFields }) {
  const { name, expression } = row;
  if (!editable) return <li><strong>{name}</strong><code>{readable(expression)}</code></li>;
  const kind = "op" in expression ? "formula" : "field";
  const edit = next => onChange({ ...row, ...next });
  return (
    <li className="dataset-field-editor">
      <Input aria-label="Output field name" placeholder="Output field" value={name} onChange={event => edit({ name: event.target.value })} />
      <Select aria-label="Field type" value={kind} options={[{ value: "field", label: "Source field" }, { value: "formula", label: "Formula" }]}
        onChange={value => edit({ expression: value === "formula"
          ? { op: "+", left: { field: "" }, right: { field: "" } }
          : { field: "" } })} />
      {kind === "field" ? (
        <Select aria-label="Source field name" placeholder="Select a source field" showSearch
          value={expression.field || undefined} options={sourceFields}
          onChange={field => edit({ name: name || field, expression: { field } })} />
      ) : (
        <div className="dataset-formula">
          <AutoComplete aria-label="Left operand" placeholder="Field or number" options={sourceFields}
            value={readable(expression.left)} filterOption={(input, option) => option.value.toLowerCase().includes(input.toLowerCase())}
            onChange={value => edit({ expression: { ...expression, left: operand(value) } })} />
          <Select aria-label="Operator" value={expression.op} options={operators}
            onChange={op => edit({ expression: { ...expression, op } })} />
          <AutoComplete aria-label="Right operand" placeholder="Field or number" options={sourceFields}
            value={readable(expression.right)} filterOption={(input, option) => option.value.toLowerCase().includes(input.toLowerCase())}
            onChange={value => edit({ expression: { ...expression, right: operand(value) } })} />
        </div>
      )}
      <Button onClick={onRemove} aria-label={name ? `Remove ${name}` : "Remove field"}>Remove</Button>
    </li>
  );
}

function DatasetAccess({ datasetId, onSaved }) {
  const [access, setAccess] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const url = `api/datasets/${datasetId}/access`;

  async function load() {
    setBusy(true);
    setError("");
    try {
      setAccess(await axios.get(url));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      const { version, read_user_ids, read_group_ids } = access;
      const result = await axios.post(url, { version, read_user_ids, read_group_ids });
      setAccess(result);
      onSaved(result.version);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Access">
      {error && <Alert type="error" message={error} showIcon />}
      {!access ? <Button onClick={load} loading={busy}>Manage access</Button> : (
        <div className="dataset-access">
          {[[ "users", "read_user_ids", "Editors" ], [ "groups", "read_group_ids", "Groups" ]].map(([collection, field, label]) => (
            <div className="dataset-control" key={field}>
              <span>{label}</span>
              <Select mode="multiple" aria-label={label} value={access[field]} optionFilterProp="label"
                options={access[collection].map(item => ({ value: item.id, label: item.name }))}
                onChange={value => setAccess({ ...access, [field]: value })} />
            </div>
          ))}
          <Button onClick={save} loading={busy}>Save access</Button>
          <Button onClick={load} disabled={busy}>Reload access</Button>
        </div>
      )}
    </Card>
  );
}

function DatasetChartBuilder({ dataset }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [group, setGroup] = useState(undefined);
  const [measure, setMeasure] = useState(undefined);
  const [aggregation, setAggregation] = useState("sum");
  const [filter, setFilter] = useState(undefined);
  const [filterValue, setFilterValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState(null);
  const choices = dataset.published_fields.map(value => ({ value, label: value }));

  async function createChart() {
    setError("");
    setBusy(true);
    try {
      const alias = group === "metric" ? "result" : "metric";
      setCreated(await axios.post(`api/datasets/${dataset.id}/reports`, {
        revision: dataset.published_revision,
        name,
        spec: {
          group_by: [group],
          metrics: { [alias]: { field: measure, aggregation } },
          filters: filter ? [{ field: filter, value: filterValue }] : [],
        },
      }));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Reports">
      {!open ? <Button onClick={() => setOpen(true)}>Create chart</Button> : (
        <div className="dataset-access">
          {error && <Alert type="error" message={error} showIcon />}
          {created && (
            <Link href={`queries/${created.query_id}#${created.visualization_id}`}>Open chart</Link>
          )}
          <label>Chart name<Input value={name} onChange={event => setName(event.target.value)} /></label>
          <div className="dataset-control"><span>Group by</span><Select aria-label="Group by" value={group} options={choices} onChange={setGroup} /></div>
          <div className="dataset-control"><span>Measure</span><Select aria-label="Measure" value={measure} options={choices} onChange={setMeasure} /></div>
          <div className="dataset-control">
            <span>Aggregation</span><Select aria-label="Aggregation" value={aggregation} onChange={setAggregation}
              options={[{ value: "sum", label: "Sum" }, { value: "avg", label: "Average" }, { value: "count", label: "Count" }]} />
          </div>
          <div className="dataset-control"><span>Filter field</span><Select aria-label="Filter field" allowClear value={filter} options={choices} onChange={setFilter} /></div>
          {filter && <label>Equals<Input value={filterValue} onChange={event => setFilterValue(event.target.value)} /></label>}
          <Button type="primary" onClick={createChart} disabled={!name.trim() || !group || !measure} loading={busy}>
            Save chart
          </Button>
        </div>
      )}
    </Card>
  );
}

function DatasetDetail({ datasetId, baseQueryId }) {
  const [dataset, setDataset] = useState(null);
  const [name, setName] = useState("");
  const [fields, setFields] = useState([fieldRow()]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [preview, setPreview] = useState(null);
  const [sourceFields, setSourceFields] = useState([]);
  const [sourceError, setSourceError] = useState("");
  const [sourceLoading, setSourceLoading] = useState(false);
  const mounted = useRef(false);
  const url = `api/datasets/${datasetId}`;
  const sourceQueryId = baseQueryId || dataset?.base_query_id;

  function populate(value) {
    setDataset(value);
    setName(value.name);
    setFields(Object.entries(value.fields).map(([field, expression]) => fieldRow(field, expression)));
  }

  useEffect(() => {
    mounted.current = true;
    if (datasetId) {
      setBusy("load");
      axios.get(url).then(data => { if (mounted.current) populate(data); })
        .catch(failure => { if (mounted.current) setError(errorMessage(failure)); })
        .finally(() => { if (mounted.current) setBusy(""); });
    }
    return () => { mounted.current = false; };
  }, [datasetId, url]);

  useEffect(() => {
    if (!sourceQueryId) return undefined;
    let active = true;
    setSourceLoading(true);
    setSourceError("");
    setSourceFields([]);
    axios.get(`api/datasets/source-fields/${sourceQueryId}`)
      .then(data => {
        if (active) setSourceFields(data.fields.map(value => ({ value, label: value })));
      })
      .catch(failure => { if (active) setSourceError(errorMessage(failure)); })
      .finally(() => { if (active) setSourceLoading(false); });
    return () => { active = false; };
  }, [sourceQueryId]);

  async function save() {
    const names = fields.map(field => field.name.trim());
    if (!names.length || names.some(value => !value) || new Set(names.map(value => value.toLowerCase())).size !== names.length) {
      throw new Error("Enter a unique name for every field.");
    }
    const payload = { name, fields: Object.fromEntries(fields.map((field, index) => [names[index], field.expression])) };
    const result = datasetId
      ? await axios.post(url, { ...payload, version: dataset.draft_version })
      : await axios.post("api/datasets", { ...payload, base_query_id: Number(baseQueryId) });
    if (datasetId) populate(result);
    else location.setPath(new URL(`datasets/${result.id}`, clientConfig.basePath).pathname);
    return result;
  }

  async function execute(operation) {
    setBusy(operation);
    setError("");
    setSaved("");
    setPreview(null);
    try {
      const value = await save();
      if (operation === "save") {
        setSaved("Draft saved.");
        return;
      }
      const job = await axios.post(`${url}/jobs`, { operation, version: value.draft_version });
      for (let attempt = 0; attempt < 57 && mounted.current; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 2100));
        if (!mounted.current) return;
        const status = await axios.get(`${url}/jobs/${job.id}`);
        if (status.status === "finished") {
          if (operation === "preview") setPreview(status.result.preview);
          else {
            populate(await axios.get(url));
            setSaved(`Published version ${status.result.published_revision}.`);
          }
          return;
        }
        if (["failed", "stopped", "canceled"].includes(status.status)) {
          throw new Error("Dataset validation failed. Check the source columns and formulas, then retry.");
        }
      }
      if (mounted.current) throw new Error("The job is taking longer than expected. Reload to check its status.");
    } catch (failure) {
      if (mounted.current) setError(errorMessage(failure));
    } finally {
      if (mounted.current) setBusy("");
    }
  }

  const editable = !datasetId || Boolean(dataset?.can_edit);
  return (
    <main className="datasets-page">
      <header className="datasets-header">
        <h1>{dataset?.name || (datasetId ? "Dataset" : "Create dataset")}</h1>
        <Link href="datasets">All datasets</Link>
      </header>
      {error && <Alert type="error" message={error} showIcon />}
      {saved && <Alert type="success" message={saved} showIcon />}
      {busy === "load" && <p role="status">Loading dataset…</p>}
      {(!datasetId || dataset) && <>
        <Card title={editable ? "Draft fields" : `Published version ${dataset.published_revision}`}>
          {editable && <label className="dataset-name">Dataset name<Input value={name} onChange={event => setName(event.target.value)} /></label>}
          {(baseQueryId || dataset?.base_query_id) && <p><Link href={`queries/${baseQueryId || dataset.base_query_id}`}>Open source query</Link></p>}
          {sourceLoading && <p role="status">Loading source fields…</p>}
          {sourceError && <Alert type="error" message={sourceError} showIcon />}
          <ol className="dataset-fields">
            {fields.map(row => <FieldEditor key={row.id} row={row} editable={editable} sourceFields={sourceFields}
              onChange={next => setFields(fields.map(field => field.id === row.id ? next : field))}
              onRemove={() => setFields(fields.filter(field => field.id !== row.id))} />)}
          </ol>
          {editable && (
            <div className="dataset-actions">
              <Button onClick={() => setFields([...fields, fieldRow()])} disabled={Boolean(busy)}>Add field</Button>
              <Button onClick={() => execute("save")} loading={busy === "save"} disabled={Boolean(busy) && busy !== "save"}>Save draft</Button>
              {datasetId && <>
                <Button onClick={() => execute("preview")} loading={busy === "preview"} disabled={Boolean(busy) && busy !== "preview"}>Preview</Button>
                <Button type="primary" onClick={() => execute("publish")} loading={busy === "publish"} disabled={Boolean(busy) && busy !== "publish"}>Publish</Button>
              </>}
            </div>
          )}
        </Card>
        {preview && (
          <Card title="Preview">
            <Table rowKey={(_row, index) => index} pagination={false} scroll={{ x: true }}
              columns={preview.columns.map(column => ({
                key: column.name, title: column.name, dataIndex: column.name,
                render: value => value === null ? "NULL" : String(value),
              }))}
              dataSource={preview.rows} />
          </Card>
        )}
        {editable && dataset?.published_revision && (
          <DatasetAccess datasetId={datasetId} onSaved={version => setDataset(value => ({ ...value, draft_version: version }))} />
        )}
        {dataset?.published_fields && <DatasetChartBuilder key={dataset.published_revision} dataset={dataset} />}
      </>}
    </main>
  );
}

[
  ["Datasets.List", "/datasets", () => <DatasetList />],
  ["Datasets.New", "/datasets/new/:baseQueryId", ({ baseQueryId }) => <DatasetDetail key={`new-${baseQueryId}`} baseQueryId={baseQueryId} />],
  ["Datasets.Detail", "/datasets/:datasetId", ({ datasetId }) => <DatasetDetail key={datasetId} datasetId={datasetId} />],
].forEach(([id, path, render]) => routes.register(id, routeWithUserSession({
  path,
  title: "Datasets",
  render: (props) => clientConfig.datasetsEnabled ? render(props) : <p>Datasets are unavailable.</p>,
})));
