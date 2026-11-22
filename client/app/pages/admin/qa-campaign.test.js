import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { SystemStatus } from "./SystemStatus";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";

jest.mock("@/services/axios", () => ({ axios: { get: jest.fn() } }));
jest.mock("@/services/auth", () => ({ currentUser: { isAdmin: true, hasPermission: jest.fn(() => false) } }));
jest.mock("@/services/routes", () => ({ __esModule: true, default: { register: jest.fn() } }));
jest.mock("@/components/admin/Layout", () => ({ __esModule: true, default: ({ children }) => <div>{children}</div> }));
jest.mock("@/components/ApplicationArea/routeWithUserSession", () => ({ __esModule: true, default: (value) => value }));
jest.mock("@/components/admin/StatusBlock", () => ({
  General: () => null,
  Manager: () => null,
  Queues: () => null,
  DatabaseMetrics: () => null,
}));
const report = {
  instance_admin: false,
  generated_at: "2026-11-20T12:00:00Z",
  health: { activity: "ok", jobs: "ok" },
  workers: [],
  users: {
    results: [{ id: 1, name: "Scoped editor", role: "editor", presence: "idle" }],
    page: 1,
    page_size: 25,
    total: 1,
  },
  jobs: {
    results: [
      { id: "j1", query_name: "Synthetic report", user_name: "Scoped editor", state: "started", long_running: true },
    ],
    page: 1,
    page_size: 25,
    total: 1,
  },
};
beforeEach(() => {
  jest.clearAllMocks();
  currentUser.isAdmin = true;
  currentUser.hasPermission.mockReturnValue(false);
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  axios.get.mockResolvedValue(report);
});

test.each([401, 403])("QA: monitoring removes cached identities after HTTP %s denial", async (status) => {
  render(<SystemStatus />);
  await screen.findByText("Synthetic report");
  axios.get.mockRejectedValue({ response: { status } });
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await screen.findByText("Monitoring could not refresh. Previously displayed data may be stale.");
  expect(screen.queryByText("Synthetic report")).toBeNull();
  expect(screen.queryAllByText("Scoped editor")).toHaveLength(0);
});

test("QA: temporary monitoring failure retains the explicitly stale report", async () => {
  render(<SystemStatus />);
  await screen.findByText("Synthetic report");
  axios.get.mockRejectedValue(new Error("offline"));
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await screen.findByText("Monitoring could not refresh. Previously displayed data may be stale.");
  expect(screen.getByText("Synthetic report")).toBeTruthy();
});
