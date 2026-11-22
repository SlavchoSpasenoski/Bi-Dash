import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Descriptions, Modal, Select, Spin, Table, Tag, Typography } from "antd";
import { axios } from "@/services/axios";
import "./QueryVersionHistory.less";

const PAGE_SIZE = 20;
const fieldNames = { query: "SQL", name: "Name", description: "Description", data_source_id: "Data source", options: "Parameters / options" };
export const canShowQueryHistory = flags => !flags.isNew && flags.canView && flags.canViewSource;

// Limit the matrix size so a large saved query cannot freeze the history view.
export function sqlChanges(previous = "", current = "") {
  if (previous === current) return [{ type: "same", text: previous }];
  const a = previous.split("\n"), b = current.split("\n");
  let lines = [];
  if (a.length * b.length <= 250000) {
    const width = b.length + 1;
    const matrix = new Uint32Array((a.length + 1) * width);
    for (let i = a.length - 1; i >= 0; i--) {
      for (let j = b.length - 1; j >= 0; j--) {
        matrix[i * width + j] = a[i] === b[j] ? 1 + matrix[(i + 1) * width + j + 1]
          : Math.max(matrix[(i + 1) * width + j], matrix[i * width + j + 1]);
      }
    }
    let i = 0, j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) { lines.push({ type: "same", text: a[i++] }); j++; }
      else if (i < a.length && (j === b.length || matrix[(i + 1) * width + j] >= matrix[i * width + j + 1])) lines.push({ type: "removed", text: a[i++] });
      else lines.push({ type: "added", text: b[j++] });
    }
  } else {
    let start = 0, end = 0;
    while (start < Math.min(a.length, b.length) && a[start] === b[start]) start++;
    while (end < Math.min(a.length, b.length) - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end++;
    lines = [
      { type: "same", text: a.slice(0, start).join("\n") },
      { type: "removed", text: a.slice(start, a.length - end).join("\n") },
      { type: "added", text: b.slice(start, b.length - end).join("\n") },
      { type: "same", text: end ? a.slice(-end).join("\n") : "" },
    ].filter(item => item.text);
  }
  return lines.reduce((groups, line) => {
    const last = groups[groups.length - 1];
    if (last?.type === line.type) last.text += "\n" + line.text;
    else groups.push({ ...line });
    return groups;
  }, []);
}

function savedAt(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString() : "Unknown";
}
function Definition({ snapshot, title }) {
  return <section className="query-history-definition" aria-label={title}>
    <Typography.Title level={5}>{title}</Typography.Title>
    <Descriptions size="small" bordered column={1}>
      <Descriptions.Item label="Name">{snapshot.name || "Untitled query"}</Descriptions.Item>
      <Descriptions.Item label="Description">{snapshot.description || "—"}</Descriptions.Item>
      <Descriptions.Item label="Data source">{snapshot.data_source_id ?? "Unavailable"}</Descriptions.Item>
      <Descriptions.Item label="Auto limit">{snapshot.options?.apply_auto_limit ? "Enabled" : "Disabled"}</Descriptions.Item>
    </Descriptions>
    <Typography.Title level={5}>Parameters</Typography.Title>
    <pre>{JSON.stringify(snapshot.options?.parameters || [], null, 2)}</pre>
    <Typography.Title level={5}>SQL</Typography.Title><pre>{snapshot.query || ""}</pre>
  </section>;
}
Definition.propTypes = { snapshot: PropTypes.object.isRequired, title: PropTypes.string.isRequired };

export default function QueryVersionHistory({ queryId, open, onClose }) {
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState([]);
  const [count, setCount] = useState(0);
  const [selected, setSelected] = useState(null);
  const [compareTo, setCompareTo] = useState("current");
  const [detail, setDetail] = useState(null);
  const [listBusy, setListBusy] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [listError, setListError] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [reload, setReload] = useState(0);
  const listRequest = useRef(0), detailRequest = useRef(0);
  useEffect(() => {
    setRows([]); setCount(0); setSelected(null); setDetail(null); setPage(1); setCompareTo("current");
  }, [queryId, open]);
  useEffect(() => {
    if (!open) return undefined;
    const request = ++listRequest.current;
    setListBusy(true); setListError(false); setDetail(null); setSelected(null); setCompareTo("current");
    axios.get(`api/queries/${queryId}/versions`, { params: { page, page_size: PAGE_SIZE } }).then(data => {
      if (request !== listRequest.current) return;
      setRows(data.results || []); setCount(data.count || 0); setSelected(data.results?.[0]?.id ?? null);
    }).catch(() => {
      if (request !== listRequest.current) return;
      setRows([]); setCount(0); setListError(true); setSelected(null); setDetail(null);
    }).finally(() => { if (request === listRequest.current) setListBusy(false); });
    return () => { listRequest.current++; };
  }, [open, queryId, page, reload]);
  useEffect(() => {
    if (!open || selected === null) { setDetail(null); return undefined; }
    const request = ++detailRequest.current;
    setDetailBusy(true); setDetailError(false); setDetail(null);
    const options = compareTo === "current" ? {} : { params: { compare_to: compareTo } };
    axios.get(`api/queries/${queryId}/versions/${selected}`, options).then(data => {
      if (request === detailRequest.current) setDetail(data);
    }).catch(() => {
      if (request === detailRequest.current) { setDetail(null); setDetailError(true); }
    }).finally(() => { if (request === detailRequest.current) setDetailBusy(false); });
    return () => { detailRequest.current++; };
  }, [open, queryId, selected, compareTo, reload]);

  const comparison = compareTo === "current" ? detail?.current : detail?.comparison?.snapshot;
  const comparisonTitle = compareTo === "current" ? "Current saved definition" : `Saved version ${detail?.comparison?.version ?? ""}`;
  const comparisonOptions = [{ value: "current", label: "Current saved definition" }, ...rows
    .filter(row => String(row.id) !== String(selected))
    .map(row => ({ value: String(row.id), label: `Saved version ${row.version}` }))];
  const choose = id => { setSelected(id); if (String(id) === compareTo) setCompareTo("current"); };
  return <Modal title="Version history" open={open} onCancel={onClose} footer={null} width={1200} destroyOnClose
    className="query-version-history">
    <Typography.Paragraph type="secondary">Read-only saved definitions. Opening history does not save or run SQL and does not replace unsaved editor work. Existing queries begin with an activation baseline; earlier unrecorded saves are unavailable.</Typography.Paragraph>
    <Button onClick={() => setReload(value => value + 1)} loading={listBusy} className="m-b-15">Reload history</Button>
    {listError && <Alert type="error" showIcon message="Version history could not be loaded. Your access may have changed. Reload to try again." />}
    <Table rowKey="id" size="small" loading={listBusy} dataSource={rows} scroll={{ x: 600 }}
      rowClassName={row => String(row.id) === String(selected) ? "query-history-selected" : ""}
      pagination={{ current: page, pageSize: PAGE_SIZE, total: count, showSizeChanger: false, onChange: setPage }}
      locale={{ emptyText: listError ? "History unavailable" : "No recorded versions are available with your current access." }}
      columns={[
        { title: "Version", key: "version", render: (_, row) => <Button type="link" onClick={() => choose(row.id)} aria-label={`View version ${row.version}`}>{row.version}</Button> },
        { title: "Recorded", dataIndex: "created_at", render: savedAt },
        { title: "Saved by", dataIndex: "author", render: author => author?.name || "Former user" },
        { title: "Type", dataIndex: "kind", render: kind => <Tag>{kind === "baseline" ? "Activation baseline" : kind === "created" ? "Created" : "Saved change"}</Tag> },
        { title: "Changes", dataIndex: "changed_fields", render: fields => (fields || []).map(field => fieldNames[field] || field).join(", ") || "Baseline" },
      ]} />
    {selected !== null && <div className="query-history-comparison-controls"><label htmlFor="query-version-compare">Compare with</label>
      <Select id="query-version-compare" value={compareTo} onChange={setCompareTo} options={comparisonOptions} aria-label="Compare with" />
    </div>}
    {detailError && <Alert type="error" showIcon message="This version or comparison is no longer available with your current access. Select another version or reload history." />}
    {detailBusy && <div className="query-history-loading"><Spin tip="Loading saved definition…" /></div>}
    {!detailBusy && detail?.snapshot && <>
      {comparison && <section aria-label="SQL changes" className="query-history-diff"><Typography.Title level={4}>SQL changes</Typography.Title>
        <p><span className="query-history-legend-removed">− Selected saved version</span> · <span className="query-history-legend-added">+ {comparisonTitle}</span></p>
        <pre>{sqlChanges(detail.snapshot.query || "", comparison.query || "").map((group, index) => <span key={index} className={`query-history-diff-${group.type}`}>{group.type === "removed" ? "− " : group.type === "added" ? "+ " : "  "}{group.text}{"\n"}</span>)}</pre>
      </section>}
      <div className="query-history-definitions"><Definition snapshot={detail.snapshot} title={`Saved version ${detail.version}`} />
        {comparison && <Definition snapshot={comparison} title={comparisonTitle} />}</div>
    </>}
  </Modal>;
}
QueryVersionHistory.propTypes = { queryId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired, open: PropTypes.bool, onClose: PropTypes.func.isRequired };
QueryVersionHistory.defaultProps = { open: true };
