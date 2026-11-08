// Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
// SPDX-License-Identifier: BSD-2-Clause
import React, { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Form, Input, Select, Skeleton, Typography } from "antd";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import wrapSettingsTab from "@/components/SettingsWrapper";
import { currentUser } from "@/services/auth";
import { axios } from "@/services/axios";
import routes from "@/services/routes";

const providers = [
  { value: "ollama", label: "Local · Ollama" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic · Claude" },
  { value: "compatible", label: "Other · OpenAI-compatible API" },
];

function AiSettings({ contextOnly = false }) {
  const [form] = Form.useForm();
  const [profiles, setProfiles] = useState({});
  const [provider, setProvider] = useState("ollama");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [ready, setReady] = useState(false);
  const endpoint = contextOnly ? "api/settings/ai/context" : "api/settings/ai";

  useEffect(() => {
    if (!currentUser.isAdmin) return;
    let active = true;
    axios.get(endpoint).then((data) => {
      if (!active) return;
      setReady(true);
      if (contextOnly) form.setFieldsValue({ context: data.context });
      else {
        setProfiles(data.profiles);
        setProvider(data.provider);
        form.setFieldsValue({ provider: data.provider, ...data.profiles[data.provider] });
      }
    }).catch((err) => {
      if (active) setError(err.response?.data?.message || "Could not load AI settings.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [contextOnly, endpoint, form]);

  if (!currentUser.isAdmin) return <Alert type="error" message="Administrator access required." />;

  async function save(values) {
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const data = await axios.post(endpoint, contextOnly ? { context: values.context || "" } : {
        provider, model: values.model, base_url: values.base_url,
        api_key: values.api_key || "", clear_api_key: !!values.clear_api_key,
      });
      if (!contextOnly) {
        setProfiles(data.profiles);
        form.setFieldsValue({ api_key: "", clear_api_key: false });
      }
      setSaved(true);
    } catch (err) {
      setError(err.response?.data?.message || "Could not save AI settings.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ maxWidth: 880, margin: "8px auto 24px" }}>
      <Typography.Title level={3}>{contextOnly ? "AI Context" : "AI Configuration"}</Typography.Title>
      <Typography.Paragraph type="secondary">
        {contextOnly
          ? "Save your business definitions, table schemas, SQL dialect and reporting conventions once. This context is included automatically in every AI request for this organization."
          : "Choose the AI service for this organization. Use a local model or connect your own provider account. Saved provider profiles are kept when you switch."}
      </Typography.Paragraph>
      {loading ? <Skeleton active /> : (
        <Form form={form} layout="vertical" onFinish={save} disabled={saving}
          onValuesChange={() => setSaved(false)}>
          {contextOnly ? (
            <>
              <Alert type="info" showIcon message="Shared across your organization"
                description="This context is available to members who can use AI and is sent to the selected provider. Include only information those members and that provider may access." />
              <Form.Item name="context" label="Persistent context" style={{ marginTop: 20 }}>
                <Input.TextArea rows={16} maxLength={24000} showCount
                  style={{ fontSize: 16, lineHeight: 1.7 }}
                  placeholder={"SQL dialect: PostgreSQL\nBusiness: …\nTables: orders(id, customer_id, total, created_at)\nDefinitions: revenue = …\nReporting timezone: …"} />
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item name="provider" label="AI provider" rules={[{ required: true }]}>
                <Select options={providers} onChange={(value) => {
                  setProvider(value);
                  form.setFieldsValue({ ...profiles[value], api_key: "", clear_api_key: false });
                }} />
              </Form.Item>
              <Form.Item name="model" label="Model ID" rules={[{ required: true, whitespace: true, max: 200 }]}
                extra="Enter the exact model ID offered by your provider or installed in Ollama.">
                <Input maxLength={200} placeholder={provider === "ollama" ? "qwen2.5-coder:7b" : "Your provider's model ID"} />
              </Form.Item>
              <Form.Item name="base_url" label="API base URL" rules={[{ required: true, type: "url" }]}
                extra={provider === "ollama"
                  ? "Address reachable from the Bi-Dash server. Docker Ollama: http://ollama:11434; host Ollama: http://host.docker.internal:11434."
                  : "For compatible services, include the API version path (for example /v1), without /chat/completions. Use HTTPS for hosted services."}>
                <Input disabled={provider === "openai" || provider === "anthropic" || saving} />
              </Form.Item>
              {provider !== "ollama" && (
                <>
                  <Form.Item name="api_key" label="API key" extra={profiles[provider]?.has_api_key
                    ? "A key is saved. Leave blank to keep it; enter a new key to replace it. Changing the base URL requires entering the key again."
                    : "Enter your provider's API key. Optional for compatible servers that do not require authentication."}>
                    <Input.Password autoComplete="new-password" maxLength={4096} />
                  </Form.Item>
                  {profiles[provider]?.has_api_key && (
                    <Form.Item name="clear_api_key" valuePropName="checked">
                      <Checkbox>Remove the saved API key</Checkbox>
                    </Form.Item>
                  )}
                  <Alert type="info" showIcon message="Your own provider account"
                    description="Messages and saved context are sent to this provider. API usage may be billed by the provider. Keys are encrypted on the server and never returned to the browser." />
                </>
              )}
              {provider === "compatible" && <Typography.Paragraph style={{ marginTop: 16 }}>
                Supports services implementing the OpenAI Chat Completions API, including compatible local servers and gateways.
                Providers with a different API need a dedicated adapter.
              </Typography.Paragraph>}
            </>
          )}
          {error && <Alert type="error" showIcon message={error} style={{ margin: "16px 0" }} />}
          {saved && <Alert type="success" showIcon message="Settings saved." style={{ margin: "16px 0" }} />}
          <Button type="primary" htmlType="submit" loading={saving} disabled={!ready} style={{ marginTop: 20 }}>Save</Button>
        </Form>
      )}
    </div>
  );
}

const ConfigurationPage = wrapSettingsTab("Settings.AiConfiguration", {
  permission: "admin", title: "AI Configuration", path: "settings/ai", order: 7,
  isActive: (path) => path === "/settings/ai",
}, AiSettings);
const ContextPage = wrapSettingsTab("Settings.AiContext", {
  permission: "admin", title: "AI Context", path: "settings/ai-context", order: 8,
}, () => <AiSettings contextOnly />);

routes.register("Settings.AiConfiguration", routeWithUserSession({
  path: "/settings/ai", title: "AI Configuration", render: (props) => <ConfigurationPage {...props} />,
}));
routes.register("Settings.AiContext", routeWithUserSession({
  path: "/settings/ai-context", title: "AI Context", render: (props) => <ContextPage {...props} />,
}));
