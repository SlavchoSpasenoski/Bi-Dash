import React, { useEffect, useRef, useState } from "react";
import { Alert, Button, Drawer, Input, Spin } from "antd";
import { axios } from "@/services/axios";
import "./index.less";

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
  const bottom = useRef(null);
  useEffect(() => {
    if (open) {
      axios
        .get("api/ai/chat")
        .then((data) => {
          setEnabled(data.enabled);
          setProvider(data.provider);
          setModel(data.model);
        })
        .catch(() => setError("AI assistant is unavailable for this account."));
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
        width={560}
      >
        <div className="redash-ai-panel">
          <p>Ask questions, draft SQL, or explore visualization ideas.</p>
          <Alert
            type="info"
            showIcon
            message="Review generated SQL before running it."
            description={provider === "ollama"
              ? "Messages and schema are processed by Ollama on this computer. Queries are never executed by this assistant."
              : provider === "openai"
                ? "Messages and the schema you enter are sent to OpenAI. Queries are never executed by this assistant."
                : "Queries are never executed by this assistant."}
          />
          {provider === "ollama" && <small>Local AI · {model} · No API key required</small>}
          {enabled === false && (
            <Alert type="warning" message="The selected AI provider is not configured. Check the server settings." />
          )}
          <label htmlFor="ai-schema">Schema context (optional)</label>
          <Input.TextArea
            id="ai-schema"
            value={schema}
            onChange={(e) => setSchema(e.target.value)}
            rows={3}
            maxLength={24000}
            disabled={busy}
            placeholder="Example: orders(id, customer_id, total, created_at). Include SQL dialect."
          />
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
          {error && <Alert type="error" message={error} closable onClose={() => setError("")} />}
          <label htmlFor="ai-prompt">Your message</label>
          <Input.TextArea
            id="ai-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            maxLength={8000}
            rows={3}
            disabled={busy}
            placeholder="Describe what you want to analyze…"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                send();
              }
            }}
          />
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
      </Drawer>
    </>
  );
}
