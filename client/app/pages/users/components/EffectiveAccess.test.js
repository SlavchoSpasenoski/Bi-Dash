import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import EffectiveAccess from "./EffectiveAccess";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";

jest.mock("@/services/axios", () => ({ axios: { get: jest.fn() } }));
jest.mock("@/services/auth", () => ({ currentUser: { isAdmin: true } }));
const report = {
  user: { id: 3, name: "Example editor", email: "example@invalid.test", role: "editor", disabled: false },
  groups: [{ id: 9, name: "Team Three" }], limits: { query_timeout_seconds: 60, max_concurrent_queries: 2 },
  actions: [{ key: "ai", label: "Use AI", allowed: true, reason: "Role permits AI; source checks apply." }],
  sources: [{ id: 1, name: "Sales source", type: "mysql", mode: "restricted", groups: [],
    view: { allowed: false, reason: "No group data access rules are assigned." },
    execute: { allowed: false, reason: "No group data access rules are assigned." }, tables: [], table_scope: "none" }],
  dashboards: [{ id: 1, name: "Sales dashboard", state: "published", view: { allowed: true, reason: "Explicit viewer assignment." },
    edit: { allowed: false, reason: "Only the owner can edit." }, share: { allowed: false, reason: "Only Admin or owner can manage viewers." },
    widgets: [{ id: 1, name: "Order totals", source_id: 1, allowed: false, reason: "Source access denied." }] }],
  notes: ["This does not test source connections."],
};
beforeEach(() => { jest.clearAllMocks(); currentUser.isAdmin = true; axios.get.mockResolvedValue(report); });
async function open() {
  render(<EffectiveAccess userId={3} />);
  fireEvent.click(screen.getByRole("button", { name: "Effective access" }));
  await screen.findByText("1. Identity and role");
}
test("organizes role, actions, data and dashboards with explicit denial reasons", async () => {
  await open();
  expect(axios.get).toHaveBeenCalledWith("api/users/3/effective-access");
  expect(screen.getByText("Team Three")).toBeTruthy();
  fireEvent.click(screen.getByText("Sales source", { selector: "strong" }));
  expect(screen.getAllByText("No group data access rules are assigned.").length).toBe(2);
  fireEvent.click(screen.getByText("Sales dashboard", { selector: "strong" }));
  expect(screen.getByText("Explicit viewer assignment.")).toBeTruthy();
  expect(screen.getByText("Source access denied.")).toBeTruthy();
  expect(screen.getByText("0/1 query widgets authorized")).toBeTruthy();
});
test("reloads current permissions instead of retaining previous grants", async () => {
  await open();
  axios.get.mockResolvedValue({ ...report, groups: [] });
  fireEvent.click(screen.getByRole("button", { name: "Reload permissions" }));
  await screen.findByText("No groups");
  expect(axios.get).toHaveBeenCalledTimes(2);
});
test("shows server failure with retry and never claims access", async () => {
  axios.get.mockRejectedValue({ response: { data: { message: "Administrator access required." } } });
  render(<EffectiveAccess userId={3} />);
  fireEvent.click(screen.getByRole("button", { name: "Effective access" }));
  await screen.findByText("Administrator access required.");
  expect(screen.queryByText("1. Identity and role")).toBeNull();
});
test("does not load an admin report for a non-admin viewer", () => {
  currentUser.isAdmin = false;
  render(<EffectiveAccess userId={3} />);
  expect(screen.queryByRole("button", { name: "Effective access" })).toBeNull();
  expect(axios.get).not.toHaveBeenCalled();
});
test("ignores an old user response after switching targets", async () => {
  let resolveOld;
  axios.get.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
  const view = render(<EffectiveAccess userId={3} />);
  fireEvent.click(screen.getByRole("button", { name: "Effective access" }));
  view.rerender(<EffectiveAccess userId={4} />);
  await screen.findByText("1. Identity and role");
  resolveOld({ ...report, groups: [{ id: 1, name: "Stale group" }] });
  await waitFor(() => expect(screen.queryByText("Stale group")).toBeNull());
});
