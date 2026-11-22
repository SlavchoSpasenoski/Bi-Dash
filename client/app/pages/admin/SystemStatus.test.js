import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { SystemStatus } from "./SystemStatus";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";

jest.mock("@/services/axios", () => ({ axios: { get: jest.fn() } }));
jest.mock("@/services/auth", () => ({ currentUser: { isAdmin: true, hasPermission: jest.fn(() => false) } }));
jest.mock("@/services/routes", () => ({ __esModule: true, default: { register: jest.fn() } }));
jest.mock("@/components/admin/Layout", () => ({ __esModule: true, default: ({ children }) => <div>{children}</div> }));
jest.mock("@/components/ApplicationArea/routeWithUserSession", () => ({ __esModule: true, default: value => value }));
jest.mock("@/components/admin/StatusBlock", () => ({ General: () => null, Manager: () => null, Queues: () => null, DatabaseMetrics: () => null }));
const report = { instance_admin: false, generated_at: "2026-11-20T12:00:00Z", health: { activity: "ok", jobs: "ok" }, workers: [],
  users: { results: [{ id: 1, name: "Scoped editor", role: "editor", presence: "idle" }], page: 1, page_size: 25, total: 1 },
  jobs: { results: [{ id: "j1", query_name: "Synthetic report", user_name: "Scoped editor", state: "started", long_running: true }], page: 1, page_size: 25, total: 1 } };
beforeEach(() => {
  jest.clearAllMocks(); currentUser.isAdmin = true; currentUser.hasPermission.mockReturnValue(false);
  Object.defineProperty(document, "hidden", { configurable: true, value: false }); axios.get.mockResolvedValue(report);
});

test("org Admin sees safe monitoring without requesting instance-wide status", async () => {
  render(<SystemStatus />);
  await screen.findByText("Synthetic report");
  expect(screen.getAllByText("Scoped editor")).toHaveLength(2);
  expect(screen.getByText("Idle")).toBeTruthy();
  expect(screen.getByText("Long-running")).toBeTruthy();
  expect(screen.getByText("Running")).toBeTruthy();
  expect(axios.get).toHaveBeenCalledTimes(1);
  expect(axios.get).toHaveBeenCalledWith("api/admin/monitoring", { params: { users_page: 1, jobs_page: 1, page_size: 25 } });
});

test("does not fetch monitoring for a non-admin", () => {
  currentUser.isAdmin = false;
  render(<SystemStatus />);
  expect(screen.getByText("Administrator access required.")).toBeTruthy();
  expect(axios.get).not.toHaveBeenCalled();
});

test("marks unavailable telemetry without falsely claiming no active queries", async () => {
  axios.get.mockResolvedValue({ ...report, health: { activity: "unavailable", jobs: "unavailable" }, jobs: { ...report.jobs, results: [] } });
  render(<SystemStatus />);
  await screen.findByText("Job telemetry is incomplete or unavailable. This does not mean there are no active queries.");
  expect(screen.getByText("Live activity telemetry is unavailable; presence is unknown.")).toBeTruthy();
});

test("visibility refresh does not overlap a slow request", async () => {
  let resolve;
  axios.get.mockImplementation(() => new Promise(done => { resolve = done; }));
  const view = render(<SystemStatus />);
  act(() => { document.dispatchEvent(new Event("visibilitychange")); document.dispatchEvent(new Event("visibilitychange")); });
  expect(axios.get).toHaveBeenCalledTimes(1);
  axios.get.mockResolvedValue(report);
  await act(async () => { resolve(report); });
  await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
  view.unmount();
});


test("effective super-admin permission alone does not fetch instance-wide metrics", async () => {
  currentUser.hasPermission.mockReturnValue(true);
  render(<SystemStatus />);
  await screen.findByText("Synthetic report");
  expect(axios.get).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("Instance workers")).toBeNull();
});
