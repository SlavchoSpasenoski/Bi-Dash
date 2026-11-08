// Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
// SPDX-License-Identifier: BSD-2-Clause
import React, { useEffect, useState } from "react";
import { Alert, Button, Form, InputNumber, Skeleton, Switch, Typography } from "antd";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import wrapSettingsTab from "@/components/SettingsWrapper";
import { currentUser } from "@/services/auth";
import { axios } from "@/services/axios";
import routes from "@/services/routes";

function AccessPolicy() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!currentUser.isAdmin) return;
    axios.get("api/settings/access-policy").then((data) => {
      form.setFieldsValue(data);
      setReady(true);
    }).catch(() => setError("Could not load access policy.")).finally(() => setLoading(false));
  }, [form]);
  if (!currentUser.isAdmin) return <Alert type="error" message="Administrator access required." />;
  async function save(values) {
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await axios.post("api/settings/access-policy", values);
      setSaved(true);
    } catch (err) {
      setError(err.response?.data?.message || "Could not save access policy.");
    } finally { setSaving(false); }
  }
  return <div style={{ maxWidth: 880, margin: "8px auto 24px" }}>
    <Typography.Title level={3}>Roles &amp; Access</Typography.Title>
    <Typography.Paragraph>Admin manages the entire workspace. Editor builds queries and dashboards within these limits.
      Reader browses report results, sorts and pages tables, and downloads permitted results without access to SQL.
      Assign a role and optional stricter query limits in each user's profile. Groups continue to control which data sources a user can access.</Typography.Paragraph>
    {loading ? <Skeleton active /> : <Form form={form} layout="vertical" onFinish={save}
      disabled={saving || !ready} onValuesChange={() => setSaved(false)}>
      <Typography.Title level={4}>Editor safeguards</Typography.Title>
      <Form.Item label="Maximum query duration (seconds)" name={["editor", "query_timeout_seconds"]}
        rules={[{ required: true, type: "number", min: 1, max: 3600 }]}
        extra="Applies to new interactive, API and scheduled jobs. Worker execution is interrupted at the limit; PostgreSQL also receives a statement timeout. Remote cancellation support varies by connector.">
        <InputNumber min={1} max={3600} />
      </Form.Item>
      <Form.Item label="Maximum running and queued queries per user" name={["editor", "max_concurrent_queries"]}
        rules={[{ required: true, type: "number", min: 1, max: 20 }]}>
        <InputNumber min={1} max={20} />
      </Form.Item>
      {[
        ["allow_schedule", "Allow scheduled queries"],
        ["allow_public_sharing", "Allow public dashboard sharing"],
        ["allow_delete", "Allow archiving queries and dashboards"],
        ["allow_ai", "Allow AI Assistant"],
      ].map(([key, label]) => <Form.Item key={key} label={label} name={["editor", key]} valuePropName="checked"><Switch /></Form.Item>)}
      <Typography.Title level={4}>Reader permissions</Typography.Title>
      <Form.Item label="Allow CSV, TSV and Excel downloads" name={["reader", "allow_download"]} valuePropName="checked">
        <Switch />
      </Form.Item>
      <Alert type="info" showIcon message="Readers use saved results"
        description="Filtering, paging and sorting loaded report data do not execute SQL. Parameter selections can load an existing cached result; an editor must refresh combinations that have no saved result. Admin remains subject to deployment-wide query limits." />
      {error && <Alert type="error" message={error} style={{ marginTop: 16 }} />}
      {saved && <Alert type="success" message="Access policy saved." style={{ marginTop: 16 }} />}
      <Button type="primary" htmlType="submit" loading={saving} disabled={!ready} style={{ marginTop: 20 }}>Save policy</Button>
    </Form>}
  </div>;
}

const Page = wrapSettingsTab("Settings.AccessPolicy", {
  permission: "admin", title: "Roles & Access", path: "settings/access", order: 6.5,
}, AccessPolicy);
routes.register("Settings.AccessPolicy", routeWithUserSession({
  path: "/settings/access", title: "Roles & Access", render: (props) => <Page {...props} />,
}));
