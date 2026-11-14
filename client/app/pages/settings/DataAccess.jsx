// Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
// SPDX-License-Identifier: BSD-2-Clause
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Collapse, Form, Input, Select, Skeleton, Space, Typography } from "antd";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import wrapSettingsTab from "@/components/SettingsWrapper";
import { currentUser } from "@/services/auth";
import { axios } from "@/services/axios";
import routes from "@/services/routes";
import DataAccessRuleEditor from "./DataAccessRuleEditor";

function GroupRuleSet({ form, field, sources, onRemove, disabled, validationFailure, children }) {
  const name = Form.useWatch(["policies", field.name, "name"], form);
  const sourceId = Form.useWatch(["policies", field.name, "data_source_id"], form);
  const rules = Form.useWatch(["policies", field.name, "rules"], form);
  const [open, setOpen] = useState(() => !form.getFieldValue(["policies", field.name, "id"]));
  useEffect(() => {
    if (validationFailure?.errorFields.some(error => error.name[0] === "policies" && error.name[1] === field.name)) {
      setOpen(true);
    }
  }, [validationFailure, field.name]);
  const source = sources.find(item => item.id === sourceId);
  return <Collapse activeKey={open ? ["rule"] : []} onChange={keys => setOpen(keys.includes("rule"))}
    style={{ marginBottom: 16 }}>
    <Collapse.Panel key="rule" forceRender header={<Space wrap>
      <Typography.Text strong>{name || `Group rule set ${field.name + 1}`}</Typography.Text>
      <Typography.Text type="secondary">{source ? `${source.name} · ` : ""}{rules?.length || 0} table rules</Typography.Text>
    </Space>} extra={<Button size="small" danger disabled={disabled}
      onClick={event => { event.stopPropagation(); onRemove(field.name); }}>Remove rule set</Button>}>
      {children}
    </Collapse.Panel>
  </Collapse>;
}

function DataAccess() {
  const [form] = Form.useForm();
  const [configuration, setConfiguration] = useState(null);
  const [sources, setSources] = useState([]);
  const [groups, setGroups] = useState([]);
  const [users, setUsers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [previewUser, setPreviewUser] = useState(null);
  const [previewSource, setPreviewSource] = useState(null);
  const [preview, setPreview] = useState(null);
  const [validationFailure, setValidationFailure] = useState(null);
  const metadataRequests = useRef(new Map());
  const loadMetadata = useCallback(params => {
    const key = JSON.stringify(params);
    if (!metadataRequests.current.has(key)) {
      const request = axios.get("api/settings/data-access/metadata", { params }).catch(err => {
        if (metadataRequests.current.get(key) === request) metadataRequests.current.delete(key);
        throw err;
      });
      metadataRequests.current.set(key, request);
    }
    return metadataRequests.current.get(key);
  }, []);
  const invalidateMetadata = useCallback(sourceId => {
    for (const key of metadataRequests.current.keys()) {
      if (JSON.parse(key).data_source_id === sourceId) metadataRequests.current.delete(key);
    }
  }, []);
  const markEdited = () => setSaved(false);
  useEffect(() => {
    if (!currentUser.isAdmin) return;
    Promise.all([axios.get("api/settings/data-access"), axios.get("api/data_sources"), axios.get("api/groups"),
      axios.get("api/users", { params: { page_size: 100 } })])
      .then(([config, dataSources, groupList, userPage]) => {
        setConfiguration(config); setSources(dataSources); setGroups(groupList); setUsers(userPage.results || []);
        form.setFieldsValue({ policies: config.policies });
      }).catch(() => setError("Could not load group access rules. Reload to retry."));
  }, [form]);
  if (!currentUser.isAdmin) return <Alert type="error" message="Administrator access required." />;
  async function save(values) {
    setBusy(true); setError(""); setSaved(false);
    try {
      const result = await axios.post("api/settings/data-access", {
        revision: configuration.revision,
        policies: (values.policies || []).map(policy => ({
          ...policy, status: "active", default_effect: "deny", user_ids: [], group_ids: policy.group_ids || [],
          rules: (policy.rules || []).map(rule => ({ ...rule, catalog: rule.catalog || null, schema: rule.schema || null,
            column_mode: rule.column_mode || "explicit", columns: rule.columns || [] })),
        })),
      });
      setConfiguration({ ...configuration, ...result });
      form.setFieldsValue({ policies: result.policies }); setSaved(true); setPreview(null);
    } catch (err) { setError(err.response?.data?.message || "Could not save group access rules."); }
    finally { setBusy(false); }
  }
  return <div style={{ maxWidth: 1000, margin: "8px auto 24px" }}>
    <Typography.Title level={3}>Data Access</Typography.Title>
    <Alert type="info" showIcon message="One shared connection, Editor access rules by group"
      description="Keep the existing MySQL/MariaDB connection, including accounts with broader database privileges. Bi-Dash permits only validated SELECT queries. Save rules applies schema, table and column restrictions to Editors in the selected groups. No additional database account or connection is required." />
    <Typography.Paragraph style={{ marginTop: 16 }}>
      Administrators can SELECT across the source. Editors need an assigned group rule on protected sources.
      Permissions from matching groups are combined. New columns are not automatically included.
      Manage group members in Settings → Groups. Source Full Access permits SQL execution within these rules.
    </Typography.Paragraph>
    <Alert type="info" showIcon message="Reader dashboard permissions are a separate phase"
      description="These rules apply only to Editors. Readers retain existing authorized dashboard and result access. They can refresh saved dashboard reports with safe typed parameters, but cannot submit arbitrary SQL. This page does not configure which dashboards a Reader may see." />
    <Alert type="warning" showIcon message="Saving rules protects the selected data sources"
      description="Editors can open shared query definitions only when their SQL dependencies are allowed. Results, exports and caches remain isolated by permission scope. Public links, schedules and Editor query dropdown dependencies are unavailable for protected sources. AI is available to administrators; Editor and Reader AI remains unavailable while protection is configured. Query Results/Python execution is unavailable. Removing all rules keeps the source protected and denies Editors without rules." />
    {error && <Alert type="error" message={error} />}
    {!configuration ? (!error && <Skeleton active />) : <Form form={form} layout="vertical" onFinish={save}
      onFinishFailed={setValidationFailure}
      disabled={busy} onValuesChange={markEdited}>
      <Form.List name="policies">{(policies, { add, remove }) => <>
        {policies.map(field => <GroupRuleSet key={field.key} form={form} field={field} sources={sources}
          onRemove={remove} disabled={busy} validationFailure={validationFailure}>
          <Form.Item name={[field.name, "id"]} label="Stable policy ID" rules={[{ required: true }]}
            extra="Use a unique ID, for example sales-reporting."><Input /></Form.Item>
          <Form.Item name={[field.name, "name"]} label="Name" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name={[field.name, "data_source_id"]} label="Data source" rules={[{ required: true }]}>
            <Select onChange={() => { form.setFieldValue(["policies", field.name, "rules"], []); markEdited(); }}
              options={sources.map(source => ({ value: source.id, label: `${source.name} (${source.type})`,
                disabled: !configuration.supported_connectors?.includes(source.type) }))} />
          </Form.Item>
          <Form.Item name={[field.name, "group_ids"]} label="Groups" rules={[{ required: true, type: "array", min: 1 }]}>
            <Select mode="multiple" showSearch optionFilterProp="label"
              options={groups.map(group => ({ value: group.id, label: group.name }))} />
          </Form.Item>
          <Form.List name={[field.name, "rules"]}>{(rules, operations) => <>
            {rules.map(rule => <Card key={rule.key} size="small" style={{ marginBottom: 12 }}>
              <DataAccessRuleEditor form={form} policyIndex={field.name} rule={rule} sources={sources} disabled={busy}
                loadMetadata={loadMetadata} invalidateMetadata={invalidateMetadata} onEdit={markEdited} />
              <Button danger onClick={() => operations.remove(rule.name)}>Remove table</Button>
            </Card>)}
            <Button onClick={() => operations.add({ catalog: null, schema: null, table: "", columns: [] })}>Add table rule</Button>
          </>}</Form.List>
        </GroupRuleSet>)}
        <Space wrap>
          <Button onClick={() => add({ id: "", name: "", rules: [], user_ids: [], group_ids: [] })}>Add group rules</Button>
          <Button type="primary" htmlType="submit" loading={busy}>Save rules</Button>
        </Space>
      </>}</Form.List>
      {saved && <Alert style={{ marginTop: 16 }} type="success" message="Group access rules saved and applied." />}
      {!configuration.enforced && configuration.policies.length > 0 && <Alert type="info" style={{ marginTop: 16 }}
        message="Previous drafts loaded for editing. Save rules to apply them using the shared connection." />}
    </Form>}
    {configuration && <Card title="Check effective assignment" style={{ marginTop: 24 }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Select style={{ width: "100%" }} placeholder="User" value={previewUser} onChange={setPreviewUser}
          showSearch filterOption={false} onSearch={q => axios.get("api/users", { params: { q, page_size: 100 } })
            .then(page => setUsers(page.results || [])).catch(() => setError("Could not search users."))}
          options={users.map(user => ({ value: user.id, label: `${user.name} (${user.email})` }))} />
        <Select style={{ width: "100%" }} placeholder="Data source" value={previewSource} onChange={setPreviewSource}
          options={sources.map(source => ({ value: source.id, label: source.name }))} />
        <Button disabled={!previewUser || !previewSource || busy} onClick={() => axios.get("api/settings/data-access/effective",
          { params: { user_id: previewUser, data_source_id: previewSource } }).then(setPreview)
          .catch(() => setError("Could not check effective assignment."))}>Check assignment</Button>
        {preview && <Alert type={preview.assigned ? "success" : "info"} message={preview.assigned
          ? `Effective group access: ${preview.policy.name}` : preview.message} />}
      </Space>
    </Card>}
  </div>;
}

const Page = wrapSettingsTab("Settings.DataAccess", {
  permission: "admin", title: "Data Access", path: "settings/data-access", order: 6.6,
}, DataAccess);
routes.register("Settings.DataAccess", routeWithUserSession({
  path: "/settings/data-access", title: "Data Access", render: props => <Page {...props} />,
}));
