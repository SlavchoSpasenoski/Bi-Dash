// Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
// SPDX-License-Identifier: BSD-2-Clause
import React, { useEffect, useState } from "react";
import { Alert, Button, Form, Input, Select, Space, Typography } from "antd";

function useMetadata(load, kind, source, schema, table, refresh, enabled) {
  const [state, setState] = useState({ values: [], loading: false, error: "", truncated: false });
  useEffect(() => {
    let current = true;
    setState({ values: [], loading: Boolean(enabled), error: "", truncated: false });
    if (enabled) {
      const params = { data_source_id: source };
      if (schema !== undefined) params.schema = schema;
      if (table !== undefined) params.table = table;
      load(params).then(data => {
        if (!Array.isArray(data[kind])) throw new Error("Unexpected metadata response.");
        if (current) setState({ values: data[kind], loading: false, error: "", truncated: Boolean(data.truncated) });
      }).catch(error => {
        if (current) setState({ values: [], loading: false,
          error: error.response?.data?.message || "Could not load database metadata. Retry or check the connection.", truncated: false });
      });
    }
    return () => { current = false; };
  }, [load, kind, source, schema, table, refresh, enabled]);
  return state;
}

const choices = values => values.map(value => ({ value, label: value }));

export default function DataAccessRuleEditor({ form, policyIndex, rule, sources, disabled, loadMetadata, invalidateMetadata, onEdit }) {
  const path = ["policies", policyIndex, "rules", rule.name];
  const source = Form.useWatch(["policies", policyIndex, "data_source_id"], form);
  const schema = Form.useWatch([...path, "schema"], form);
  const table = Form.useWatch([...path, "table"], form);
  const [refresh, setRefresh] = useState(0);
  const supported = ["mysql", "pg"].includes(sources.find(item => item.id === source)?.type);
  const schemas = useMetadata(loadMetadata, "schemas", source, undefined, undefined, refresh, Boolean(supported && source));
  const tables = useMetadata(loadMetadata, "tables", source, schema, undefined, refresh, Boolean(supported && source && schema));
  const columns = useMetadata(loadMetadata, "columns", source, schema, table, refresh, Boolean(supported && source && schema && table));
  const error = schemas.error || tables.error || columns.error;
  const truncated = schemas.truncated || tables.truncated || columns.truncated;
  function clearFields(fields) {
    fields.forEach(([name, value]) => form.setFieldValue([...path, name], value));
    onEdit();
  }
  if (source && !supported) return <>
    <Alert type="info" message="Metadata browsing is currently supported for MySQL/MariaDB and PostgreSQL. Enter exact names for this draft-only connector." />
    <Form.Item name={[rule.name, "catalog"]} label="Database / catalog (if applicable)"><Input /></Form.Item>
    <Form.Item name={[rule.name, "schema"]} label="Schema (if applicable)"><Input /></Form.Item>
    <Form.Item name={[rule.name, "table"]} label="Table" rules={[{ required: true }]}><Input /></Form.Item>
    <Form.Item name={[rule.name, "columns"]} label="Allowed columns" rules={[{ required: true, type: "array", min: 1 }]}>
      <Select mode="tags" tokenSeparators={[","]} />
    </Form.Item>
  </>;
  return <>
    {!source && <Alert type="info" message="Choose a data source above to browse its schemas and tables." />}
    {error && <Alert type="error" showIcon message={error} />}
    {truncated && <Alert type="warning" message="The metadata list reached its limit. Some objects may not be shown." />}
    <Form.Item name={[rule.name, "schema"]} label="Schema / database" rules={[{ required: true }]}>
      <Select showSearch optionFilterProp="label" placeholder="Choose a schema / database" loading={schemas.loading}
        disabled={disabled || !source || schemas.loading || Boolean(schemas.error)} options={choices(schemas.values)}
        notFoundContent={schemas.loading ? "Loading schemas…" : "No accessible schemas found"}
        onChange={() => clearFields([["catalog", null], ["table", undefined], ["columns", []], ["column_mode", "explicit"]])} />
    </Form.Item>
    <Form.Item name={[rule.name, "table"]} label="Table" rules={[{ required: true }]}>
      <Select showSearch optionFilterProp="label" placeholder="Choose a table" loading={tables.loading}
        disabled={disabled || !schema || tables.loading || Boolean(tables.error)} options={choices(tables.values)}
        notFoundContent={tables.loading ? "Loading tables…" : "No supported tables found"}
        onChange={() => clearFields([["columns", []], ["column_mode", "explicit"]])} />
    </Form.Item>
    <Form.Item name={[rule.name, "columns"]} label="Allowed columns" rules={[{ required: true, type: "array", min: 1 }]}>
      <Select mode="multiple" showSearch optionFilterProp="label" placeholder="Choose allowed columns" maxTagCount="responsive"
        loading={columns.loading} disabled={disabled || !table || columns.loading || Boolean(columns.error)}
        options={choices(columns.values)} notFoundContent={columns.loading ? "Loading columns…" : "No accessible columns found"} />
    </Form.Item>
    <Space wrap style={{ marginBottom: 12 }}>
      <Button size="small" disabled={disabled || !columns.values.length || columns.loading || columns.truncated || columns.values.length > 1000}
        onClick={() => clearFields([["columns", [...columns.values]], ["column_mode", "all_current"]])}>Select all current columns</Button>
      <Button size="small" disabled={disabled || !source || schemas.loading || tables.loading || columns.loading}
        onClick={() => { invalidateMetadata(source); setRefresh(value => value + 1); }}>Refresh metadata</Button>
    </Space>
    <Typography.Paragraph type="secondary">Column selections are snapshots. New columns are not automatically allowed.</Typography.Paragraph>
  </>;
}
