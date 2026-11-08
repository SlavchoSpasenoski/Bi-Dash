// Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
// SPDX-License-Identifier: BSD-2-Clause
import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Collapse, Drawer, Input, Radio, Spin } from "antd";
import { currentUser } from "@/services/auth";
import Link from "@/components/Link";
import { axios } from "@/services/axios";
import "./index.less";
import DashboardBuilder from "./DashboardBuilder";

export default function AiAssistant() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [prompt, setPrompt] = useState("");
  const [schema, setSchema] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [enabled, setEnabled] = useState(null);
  const [provider, setProvider] = useState(null);
  const [model, setModel] = useState("");
  const [copied, setCopied] = useState(null);
  const [hasContext, setHasContext] = useState(false);
  const [destination, setDestination] = useState("");
  const [mode, setMode] = useState("chat");
  const bottom = useRef(null);
  useEffect(() => {
    if (open) {
      setEnabled(null);
      axios
        .get("api/ai/chat")
        .then((data) => {
          setEnabled(data.enabled);
          setProvider(data.provider);
          setModel(data.model);
          setHasContext(data.has_context);
          setDestination(data.destination);
        })
        .catch(() => {
          setEnabled(false);
          setError("AI assistant is unavailable. Check your permissions and AI Configuration.");
        });
    }
  }, [open]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function send() {
    if (!prompt.trim() || busy || !enabled) return;
    const next = [...messages, { role: "user", content: prompt.trim() }];
    setBusy(true);
    setError("");
    try {
      const result = await axios.post("api/ai/chat", { messages: next.slice(-15), schema });
      setMessages([...next, { role: "assistant", content: result.answer }]);
      setPrompt("");
    } catch (err) {
      setError(err.response?.data?.message || "Could not reach the AI assistant. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function copyAnswer(content, index) {
    try {
      const sql = content.match(/```sql\s*\n([\s\S]*?)```/i);
      await navigator.clipboard.writeText(sql ? sql[1].trim() : content);
      setCopied(index);
    } catch {
      setError("Clipboard access failed. Select and copy the answer manually.");
    }
  }

  if (!currentUser.hasPermission("use_ai")) return null;

  return (
    <>
      <Button className="redash-ai-launch" type="primary" onClick={() => setOpen(true)}>
        ✦ AI Assistant
      </Button>
      <Drawer
        className="redash-ai-drawer"
        title="✦ Bi-Dash AI Assistant"
        open={open}
        onClose={() => setOpen(false)}
        width={880}
      >
        <div className="redash-ai-panel">
          <Radio.Group value={mode} disabled={busy} onChange={(event) => setMode(event.target.value)}
            optionType="button" options={[{ value: "chat", label: "Ask AI" }, { value: "dashboard", label: "Build dashboard" }]} />
          {mode === "dashboard" ? <DashboardBuilder extraContext={schema} onBusyChange={setBusy} onOpenDraft={() => setOpen(false)} /> : <>
          <Alert
            type="info"
            showIcon
            message="Review generated SQL before running it."
            description={destination
              ? `Messages and context are sent to ${destination}. Queries are never executed by this assistant.`
              : "Queries are never executed by this assistant."}
          />
          {provider && <div className="redash-ai-status">
            <span>{provider === "ollama" ? "Ollama" : provider} · {model}</span>
            <span>{hasContext ? "Saved organization context applied" : "No saved organization context"}</span>
            {currentUser.isAdmin && <Link href="settings/ai">AI settings</Link>}
          </div>}
          {enabled === false && (
            <Alert type="warning" message="The selected AI provider is not configured. Check the server settings." />
          )}
          <Collapse ghost className="redash-ai-context">
            <Collapse.Panel header="Extra context for this conversation (optional)" key="context">
              <p>Saved organization context is included automatically.
                {currentUser.isAdmin && <> <Link href="settings/ai-context">Manage saved context</Link></>}
              </p>
              <label htmlFor="ai-schema">Additional schema or details</label>
          <Input.TextArea
            id="ai-schema"
            value={schema}
            onChange={(e) => setSchema(e.target.value)}
            rows={3}
            maxLength={24000}
            disabled={busy}
            placeholder="Example: orders(id, customer_id, total, created_at). Include SQL dialect."
          />
            </Collapse.Panel>
          </Collapse>
          <div className="redash-ai-messages" role="log" aria-label="AI conversation" aria-live="polite">
            {!messages.length && (
              <p className="redash-ai-empty">
                Try: “Write a query for monthly sales” or “Explain how to build a cohort chart”.
                You can write in Macedonian too.
              </p>
            )}
            {messages.map((message, index) => (
              <div className={`redash-ai-message ${message.role}`} key={index}>
                <strong>{message.role === "user" ? "You" : "Bi-Dash AI"}</strong>
                <pre>{message.content}</pre>
                {message.role === "assistant" && (
                  <Button size="small" onClick={() => copyAnswer(message.content, index)}>
                    {copied === index ? "Copied" : "Copy SQL / answer"}
                  </Button>
                )}
              </div>
            ))}
            {busy && <Spin tip="Thinking…" />}
            <div ref={bottom} />
          </div>
          <div className="redash-ai-composer">
          {error && <Alert type="error" message={error} closable onClose={() => setError("")} />}
          <label className="redash-ai-prompt-label" htmlFor="ai-prompt">What would you like to analyze?</label>
          <Input.TextArea
            id="ai-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            maxLength={8000}
            rows={6}
            className="redash-ai-prompt"
            disabled={busy}
            placeholder="Describe what you want to analyze…"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                send();
              }
            }}
          />
          <small className="redash-ai-shortcut">Ctrl / ⌘ + Enter to send · Conversation clears when the page reloads</small>
          <div className="redash-ai-actions">
            <Button
              disabled={busy || !messages.length}
              onClick={() => {
                setMessages([]);
                setCopied(null);
                setError("");
              }}
            >
              Clear chat
            </Button>
            <Button type="primary" loading={busy} disabled={!enabled || !prompt.trim()} onClick={send}>
              Send
            </Button>
          </div>
          </div>
          </>}
        </div>
      </Drawer>
    </>
  );
}
