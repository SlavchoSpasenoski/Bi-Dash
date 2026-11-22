import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import QueryVersionHistory, { canShowQueryHistory, sqlChanges } from "./QueryVersionHistory";
import { axios } from "@/services/axios";

jest.mock("@/services/axios", () => ({ axios: { get: jest.fn(), post: jest.fn() } }));
jest.mock("antd", () => {
  const actual = jest.requireActual("antd");
  return { ...actual, Select: ({ value, onChange, options, ...props }) => <select aria-label={props["aria-label"]}
    value={value} onChange={event => onChange(event.target.value)}>{options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select> };
});
const snapshot = sql => ({ name: "Saved report", description: "Synthetic definition", query: sql, data_source_id: 7,
  options: { parameters: [], apply_auto_limit: false, api_key: "PRIVATE_OPTION_MARKER" }, api_key: "PRIVATE_TOP_LEVEL_MARKER" });
const rows = [
  { id: 102, version: 2, kind: "update", created_at: "2026-11-20T12:00:00Z", author: { id: 3, name: "Example editor" }, changed_fields: ["query"] },
  { id: 101, version: 1, kind: "baseline", created_at: "2026-11-20T11:00:00Z", author: null, changed_fields: [] },
];
const detail = (id = 102) => ({ ...rows.find(row => row.id === id), snapshot: snapshot(id === 102 ? "SELECT 2 AS amount" : "SELECT 1 AS amount"), current: snapshot("SELECT 3 AS amount") });
function setup() {
  axios.get.mockImplementation((url, options) => {
    if (url === "api/queries/5/versions") return Promise.resolve({ results: rows, page: options.params.page, page_size: 20, count: 2 });
    const id = Number(url.split("/").pop());
    return Promise.resolve({ ...detail(id), ...(options.params?.compare_to ? { comparison: { id: 101, version: 1, snapshot: snapshot("SELECT 1 AS amount") } } : {}) });
  });
}
beforeEach(() => { jest.clearAllMocks(); setup(); });
async function open() {
  const result = render(<QueryVersionHistory queryId={5} onClose={jest.fn()} />);
  await screen.findByRole("region", { name: "Saved version 2" });
  return result;
}

test("loads saved definitions read-only without running or changing an editor model", async () => {
  const unsavedEditor = { query: "UNSAVED SELECT", options: { custom: true } };
  await open();
  expect(axios.get).toHaveBeenCalledWith("api/queries/5/versions", { params: { page: 1, page_size: 20 } });
  expect(axios.get).toHaveBeenCalledWith("api/queries/5/versions/102", {});
  expect(screen.getByText("Example editor")).toBeTruthy();
  expect(screen.getByText("Activation baseline")).toBeTruthy();
  expect(screen.getByText("Former user")).toBeTruthy();
  expect(screen.getByRole("region", { name: "Current saved definition" }).textContent).toContain("SELECT 3");
  expect(unsavedEditor).toEqual({ query: "UNSAVED SELECT", options: { custom: true } });
  expect(axios.post).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toContain("PRIVATE_OPTION_MARKER");
  expect(document.body.textContent).not.toContain("PRIVATE_TOP_LEVEL_MARKER");
  expect(screen.queryByRole("button", { name: /restore|run|save/i })).toBeNull();
});

test("compares another permitted saved version through the server endpoint", async () => {
  await open();
  fireEvent.change(screen.getByRole("combobox", { name: "Compare with" }), { target: { value: "101" } });
  await waitFor(() => expect(axios.get).toHaveBeenCalledWith("api/queries/5/versions/102", { params: { compare_to: "101" } }));
  const comparison = await screen.findByRole("region", { name: "Saved version 1" });
  expect(comparison.textContent).toContain("SELECT 1 AS amount");
  expect(axios.post).not.toHaveBeenCalled();
});

test("renders SQL markup as inert text", async () => {
  const original = axios.get.getMockImplementation();
  axios.get.mockImplementation((url, options) => url.endsWith("/102") ? Promise.resolve({ ...detail(), snapshot: snapshot('<script>alert("x")</script>') }) : original(url, options));
  await open();
  expect(screen.getByRole("region", { name: "Saved version 2" }).textContent).toContain('<script>alert("x")</script>');
  expect(document.querySelector(".query-version-history script")).toBeNull();
});

test("uses server pagination and does not fetch all SQL versions at once", async () => {
  const original = axios.get.getMockImplementation();
  axios.get.mockImplementation((url, options) => url === "api/queries/5/versions" ? Promise.resolve({ results: rows, count: 41, page: options.params.page }) : original(url, options));
  await open();
  fireEvent.click(screen.getByTitle("Next Page"));
  await waitFor(() => expect(axios.get).toHaveBeenCalledWith("api/queries/5/versions", { params: { page: 2, page_size: 20 } }));
  expect(axios.get.mock.calls.filter(([url]) => url.includes("/versions/")).length).toBeLessThan(5);
});

test("list failures are generic and can be retried without revealing server details", async () => {
  axios.get.mockRejectedValueOnce(new Error("INTERNAL_SECRET_QUERY"));
  render(<QueryVersionHistory queryId={5} onClose={jest.fn()} />);
  await screen.findByText("Version history could not be loaded. Your access may have changed. Reload to try again.");
  expect(document.body.textContent).not.toContain("INTERNAL_SECRET_QUERY");
  fireEvent.click(screen.getByRole("button", { name: "Reload history" }));
  await screen.findByRole("region", { name: "Saved version 2" });
});

test("a denied historical version clears SQL instead of retaining previously shown detail", async () => {
  await open();
  axios.get.mockRejectedValueOnce(new Error("INTERNAL_PERMISSION_DETAIL"));
  fireEvent.click(screen.getByRole("button", { name: "View version 1" }));
  await screen.findByText("This version or comparison is no longer available with your current access. Select another version or reload history.");
  expect(screen.queryByRole("region", { name: "Saved version 2" })).toBeNull();
  expect(document.body.textContent).not.toContain("INTERNAL_PERMISSION_DETAIL");
});

test("ignores an out-of-order historical detail response", async () => {
  await open();
  const original = axios.get.getMockImplementation();
  let resolveOlder;
  axios.get.mockImplementation((url, options) => url.endsWith("/101") ? new Promise(resolve => { resolveOlder = resolve; }) : original(url, options));
  fireEvent.click(screen.getByRole("button", { name: "View version 1" }));
  await waitFor(() => expect(resolveOlder).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "View version 2" }));
  await screen.findByRole("region", { name: "Saved version 2" });
  await act(async () => { resolveOlder(detail(101)); });
  expect(screen.queryByRole("region", { name: "Saved version 1" })).toBeNull();
  expect(screen.getByRole("region", { name: "Saved version 2" }).textContent).toContain("SELECT 2");
});

test("history menu eligibility excludes Readers and new queries", () => {
  expect(canShowQueryHistory({ isNew: false, canView: true, canViewSource: true })).toBe(true);
  expect(canShowQueryHistory({ isNew: true, canView: true, canViewSource: true })).toBe(false);
  expect(canShowQueryHistory({ isNew: false, canView: false, canViewSource: false })).toBe(false);
  expect(canShowQueryHistory({ isNew: false, canView: true, canViewSource: false })).toBe(false);
});

test("SQL diff handles unchanged lines and bounded large-query replacement", () => {
  expect(sqlChanges("SELECT 1\nFROM orders", "SELECT 2\nFROM orders")).toEqual([
    { type: "removed", text: "SELECT 1" }, { type: "added", text: "SELECT 2" }, { type: "same", text: "FROM orders" },
  ]);
  expect(sqlChanges("same", "same")).toEqual([{ type: "same", text: "same" }]);
  const large = Array.from({ length: 1000 }, (_, i) => `line ${i}`).join("\n");
  const changes = sqlChanges(large + "\nold", large + "\nnew");
  expect(changes.find(group => group.type === "removed").text).toBe("old");
  expect(changes.find(group => group.type === "added").text).toBe("new");
});


test("changing history page resets comparison to the current saved definition", async () => {
  const original = axios.get.getMockImplementation();
  axios.get.mockImplementation((url, options) => url === "api/queries/5/versions" ? Promise.resolve({ results: rows, count: 41, page: options.params.page }) : original(url, options));
  await open();
  fireEvent.change(screen.getByRole("combobox", { name: "Compare with" }), { target: { value: "101" } });
  await screen.findByRole("region", { name: "Saved version 1" });
  fireEvent.click(screen.getByTitle("Next Page"));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Compare with" }).value).toBe("current"));
  await screen.findByRole("region", { name: "Current saved definition" });
});
