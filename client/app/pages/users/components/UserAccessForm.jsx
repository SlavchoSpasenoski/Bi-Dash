// Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
// SPDX-License-Identifier: BSD-2-Clause
import React, { useEffect, useState } from "react";
import { Alert, Button, Form, InputNumber, Select } from "antd";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";

export default function UserAccessForm({ user }) {
  const [form] = Form.useForm();
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const role = Form.useWatch("role", form);
  useEffect(() => {
    if (!currentUser.isAdmin) return;
    axios.get(`api/users/${user.id}/access`).then((data) => {
      form.setFieldsValue({ role: data.role, ...data.limits });
      setReady(true);
    }).catch(() => setError("Could not load user access settings."));
  }, [user.id, form]);
  if (!currentUser.isAdmin) return null;
  async function save(values) {
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await axios.post(`api/users/${user.id}/access`, {
        role: values.role, limits: {
          query_timeout_seconds: values.query_timeout_seconds ?? null,
          max_concurrent_queries: values.max_concurrent_queries ?? null,
        },
      });
      setSaved(true);
    } catch (err) { setError(err.response?.data?.message || "Could not save user access."); }
    finally { setSaving(false); }
  }
  return <>
    <hr /><h4>Role &amp; query limits</h4>
    <Form form={form} layout="vertical" onFinish={save} disabled={!ready || saving}
      onValuesChange={() => setSaved(false)}>
      <Form.Item label="Role" name="role" rules={[{ required: true }]}>
        <Select disabled={currentUser.id === user.id} options={[
          { value: "admin", label: "Admin · Full access" },
          { value: "editor", label: "Editor · Build reports" },
          { value: "reader", label: "Reader · Report results only" },
        ]} />
      </Form.Item>
      {role === "editor" && <>
        <p>Optional stricter limits. Leave blank to use the role policy.</p>
        <Form.Item name="query_timeout_seconds" label="Maximum query duration (seconds)">
          <InputNumber min={1} max={3600} placeholder="Role default" />
        </Form.Item>
        <Form.Item name="max_concurrent_queries" label="Maximum running and queued queries">
          <InputNumber min={1} max={20} placeholder="Role default" />
        </Form.Item>
      </>}
      {error && <Alert type="error" message={error} />}
      {saved && <Alert type="success" message="User access saved." />}
      <Button type="primary" htmlType="submit" loading={saving} disabled={!ready} style={{ marginTop: 12 }}>Save access</Button>
    </Form>
  </>;
}
