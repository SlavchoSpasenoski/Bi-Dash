import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { axios } from "@/services/axios";
import ShareDashboardDialog from "./ShareDashboardDialog";

jest.mock("@/services/axios", () => ({ axios: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/services/notification", () => ({ success: jest.fn() }));

const configuration = {
  read_user_ids: [2], candidates: [{ id: 2, name: "Assigned reader", role: "reader" }],
  revision: 3, can_manage: true, is_draft: false, is_archived: false,
};
function renderDialog(canManageSharing = true) {
  return render(<ShareDashboardDialog.Component dashboard={{ id: 1, version: 3 }}
    canManageSharing={canManageSharing}
    dialog={{ props: { visible: true }, close: jest.fn(), dismiss: jest.fn() }} />);
}
beforeEach(() => { jest.clearAllMocks(); axios.get.mockResolvedValue(configuration); });

test("shows signed-in user grants rather than a public secret link", async () => {
  renderDialog();
  await screen.findByText("Assigned reader · reader");
  expect(axios.get).toHaveBeenCalledWith("api/dashboards/1/access");
  expect(screen.queryByText("Allow public access")).toBeNull();
  expect(screen.getByText(/Public links are disabled/)).toBeTruthy();
});

test("saves a reviewed replacement grant list with its revision", async () => {
  axios.post.mockResolvedValue({ ...configuration, read_user_ids: [], revision: 4 });
  renderDialog();
  fireEvent.click(await screen.findByRole("button", { name: "Remove all selected users" }));
  fireEvent.click(screen.getByRole("button", { name: "Save access" }));
  await waitFor(() => expect(axios.post).toHaveBeenCalledWith("api/dashboards/1/access", { read_user_ids: [], revision: 3 }));
  await screen.findByText("Dashboard access saved.");
});

test("displays a server conflict without claiming access was saved", async () => {
  axios.post.mockRejectedValue({ response: { data: { message: "Dashboard access changed. Reload before saving." } } });
  renderDialog();
  await screen.findByText("Assigned reader · reader");
  fireEvent.click(screen.getByRole("button", { name: "Save access" }));
  await screen.findByText("Dashboard access changed. Reload before saving.");
  expect(screen.queryByText("Dashboard access saved.")).toBeNull();
});

test("a viewer cannot submit grants even if a dialog is opened manually", async () => {
  renderDialog(false);
  await screen.findByText("Assigned reader · reader");
  const save = screen.getByRole("button", { name: "Save access" });
  expect(save.disabled).toBe(true);
  fireEvent.click(save);
  expect(axios.post).not.toHaveBeenCalled();
});

test("surfaces a denied access-management request", async () => {
  axios.get.mockRejectedValue({ response: { data: { message: "Only the owner or an administrator can manage access." } } });
  renderDialog();
  await screen.findByText("Only the owner or an administrator can manage access.");
  expect(screen.getByRole("button", { name: "Save access" }).disabled).toBe(true);
});

test("group grants save alongside direct users and retain the reviewed revision", async () => {
  const groupConfig = { ...configuration, read_group_ids: [9], group_candidates: [{ id: 9, name: "Sales team" }] };
  axios.get.mockResolvedValue(groupConfig);
  axios.post.mockResolvedValue({ ...groupConfig, read_group_ids: [], revision: 4 });
  renderDialog();
  await screen.findByText("Assigned reader · reader");
  fireEvent.click(screen.getByRole("tab", { name: "Groups" }));
  await screen.findByText("Sales team");
  fireEvent.click(screen.getByRole("button", { name: "Remove all selected groups" }));
  fireEvent.click(screen.getByRole("button", { name: "Save access" }));
  await waitFor(() => expect(axios.post).toHaveBeenCalledWith("api/dashboards/1/access", {
    read_user_ids: [2], read_group_ids: [], revision: 3,
  }));
});

test("draft group grants can be cleared but cannot be saved as new viewers", async () => {
  axios.get.mockResolvedValue({ ...configuration, read_user_ids: [], read_group_ids: [9],
    group_candidates: [{ id: 9, name: "Sales team" }], is_draft: true });
  renderDialog();
  await screen.findByText("Publish this dashboard before assigning viewers. Existing grants can be removed while it is unpublished or archived.");
  expect(screen.getByRole("button", { name: "Save access" }).disabled).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: "Groups" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove all selected groups" }));
  expect(screen.getByRole("button", { name: "Save access" }).disabled).toBe(false);
});
