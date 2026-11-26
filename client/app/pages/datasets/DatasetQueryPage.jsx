import React, { useEffect, useState } from "react";
import Alert from "antd/lib/alert";
import Button from "antd/lib/button";
import Card from "antd/lib/card";
import Input from "antd/lib/input";
import Select from "antd/lib/select";
import Table from "antd/lib/table";
import Link from "@/components/Link";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import { clientConfig } from "@/services/auth";
import { axios } from "@/services/axios";
import routes from "@/services/routes";

import "./datasets.css";

const options = values => values.map(value => ({ value, label: value }));
const aggregations = [{ value: "sum", label: "Sum" }, { value: "avg", label: "Average" }, { value: "count", label: "Count" }];
let rowId = 0;

function DatasetQueryForm({ dataset }) {
  const [name, setName] = useState("");
  const [fields, setFields] = useState([]);
  const [metrics, setMetrics] = useState([]);
  const [filters, setFilters] = useState([]);
  const [previewResult, setPreview] = useState(null);
  const [created, setCreated] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  const choices = options(dataset.published_fields || []);
  const definitionKey = JSON.stringify([dataset.published_revision, fields, metrics, filters]);
  const preview = previewResult?.key === definitionKey ? previewResult.data : null;

  function edit(setter, id, updates) {
    setter(rows => rows.map(row => row.id === id ? { ...row, ...updates } : row));
  }

  async function submit(operation) {
    setError("");
    setBusy(operation);
    try {
      const aliases = metrics.map(row => row.alias.trim());
      if (new Set(aliases.map(alias => alias.toLowerCase())).size !== aliases.length) {
        throw new Error("Choose a different output name for each measure.");
      }
      const payload = {
        revision: dataset.published_revision,
        spec: {
          group_by: fields,
          metrics: Object.fromEntries(metrics.map((row, index) => [aliases[index], { field: row.field, aggregation: row.aggregation }])),
          filters: filters.map(row => ({ field: row.field, value: row.value })),
        },
      };
      const url = `api/datasets/${dataset.id}/reports`;
      if (operation === "preview") {
        setPreview(null);
        setPreview({ key: definitionKey, data: await axios.put(url, payload) });
      } else {
        setCreated(await axios.post(url, { ...payload, name }));
      }
    } catch (failure) {
      setError(failure.response?.data?.message || failure.message);
    } finally {
      setBusy(null);
    }
  }

  const invalid = (!fields.length && !metrics.length) ||
    metrics.some(row => !row.field || !row.alias.trim()) || filters.some(row => !row.field);

  return (
    <>
      <Card title="Query fields">
        <div className="dataset-access">
          {error && <Alert type="error" message={error} showIcon />}
          <label>Query name<Input value={name} onChange={event => setName(event.target.value)} /></label>
          <div className="dataset-control">
            <span>{metrics.length ? "Group by" : "Fields"}</span>
            <Select mode="multiple" aria-label="Output fields" value={fields} options={choices}
              onChange={values => { if (values.length <= 4) setFields(values); }} maxTagCount="responsive" />
          </div>
          {metrics.map(row => (
            <div key={row.id} className="dataset-actions">
              <Select aria-label="Measure field" placeholder="Measure field" style={{ minWidth: 150 }}
                value={row.field} options={choices} onChange={field => edit(setMetrics, row.id, { field })} />
              <Select aria-label="Aggregation" value={row.aggregation} options={aggregations}
                onChange={aggregation => edit(setMetrics, row.id, { aggregation })} />
              <Input aria-label="Measure name" placeholder="Output name" style={{ width: 150 }} value={row.alias}
                onChange={event => edit(setMetrics, row.id, { alias: event.target.value })} />
              <Button onClick={() => setMetrics(rows => rows.filter(item => item.id !== row.id))}>Remove measure</Button>
            </div>
          ))}
          {filters.map(row => (
            <div key={row.id} className="dataset-actions">
              <Select aria-label="Filter field" placeholder="Filter field" style={{ minWidth: 150 }}
                value={row.field} options={choices} onChange={field => edit(setFilters, row.id, { field })} />
              <Input aria-label="Equals" placeholder="Equals" style={{ width: 150 }} value={row.value}
                onChange={event => edit(setFilters, row.id, { value: event.target.value })} />
              <Button onClick={() => setFilters(rows => rows.filter(item => item.id !== row.id))}>Remove filter</Button>
            </div>
          ))}
          <div className="dataset-actions">
            <Button disabled={metrics.length >= 8} onClick={() => setMetrics([...metrics, { id: ++rowId, aggregation: "sum", alias: "" }])}>Add measure</Button>
            <Button disabled={filters.length >= 8} onClick={() => setFilters([...filters, { id: ++rowId, value: "" }])}>Add filter</Button>
            <Button loading={busy === "preview"} disabled={invalid || Boolean(busy)} onClick={() => submit("preview")}>Preview</Button>
            <Button type="primary" loading={busy === "save"} disabled={invalid || !name.trim() || Boolean(busy)}
              onClick={() => submit("save")}>Save query</Button>
            {created && <Link href={`queries/${created.query_id}`}>Open query</Link>}
          </div>
        </div>
      </Card>
      {preview && (
        <Card title="Preview">
          <Table rowKey={(_row, index) => index} pagination={false} scroll={{ x: true }} dataSource={preview.rows}
            columns={preview.columns.map(column => ({ title: column.name, dataIndex: column.name, key: column.name,
              render: value => value === null ? "NULL" : String(value) }))} />
        </Card>
      )}
    </>
  );
}

function DatasetQueryPage() {
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(0);
  const [dataset, setDataset] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function load(after) {
    setLoading(true);
    try {
      const data = await axios.get("api/datasets", { params: { after } });
      setItems(previous => [...previous, ...data.results.filter(item => item.published_revision)]);
      setCursor(data.next_after);
    } catch (failure) {
      setError(failure.response?.data?.message || failure.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(0); }, []);
  useEffect(() => {
    setDataset(null);
    if (!selected) return undefined;
    let active = true;
    axios.get(`api/datasets/${selected}`).then(item => { if (active) setDataset(item); })
      .catch(failure => { if (active) setError(failure.response?.data?.message || failure.message); });
    return () => { active = false; };
  }, [selected]);

  return (
    <main className="datasets-page">
      <header className="datasets-header"><h1>New Query</h1><Link href="queries/new">Open SQL editor</Link></header>
      {error && <Alert type="error" message={error} showIcon />}
      <Card title="Dataset">
        <div className="dataset-control">
          <Select aria-label="Published Dataset" showSearch optionFilterProp="label" placeholder="Choose a published Dataset"
            loading={loading} value={selected} options={items.map(item => ({ value: item.id, label: item.name }))}
            onChange={setSelected} />
          {cursor > 0 && <Button onClick={() => load(cursor)} disabled={loading}>Load more</Button>}
        </div>
      </Card>
      {dataset && <DatasetQueryForm key={dataset.id} dataset={dataset} />}
    </main>
  );
}

routes.register("Queries.NewDataset", routeWithUserSession({
  path: "/queries/new/dataset",
  title: "New Query from Dataset",
  render: () => clientConfig.datasetsEnabled ? <DatasetQueryPage /> : <p>Datasets are unavailable.</p>,
}));
