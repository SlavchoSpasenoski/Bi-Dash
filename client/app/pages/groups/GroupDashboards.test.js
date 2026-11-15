import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GroupDashboards } from "./GroupDashboards";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";

jest.mock("@/services/axios", () => ({ axios: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/services/auth", () => ({ currentUser: { isAdmin: true } }));
jest.mock("@/components/ApplicationArea/routeWithUserSession", () => options => options);
jest.mock("@/components/SettingsWrapper", () => (id, options, component) => component);
jest.mock("@/components/items-list/components/Sidebar", () => ({ Menu: () => null }));

const board = { id: 2, name: "Sales report", is_draft: false, is_archived: false, assigned: false, revision: 5 };
function mockList(rows = [board]) {
  axios.get.mockImplementation((url, options) => Promise.resolve(url.endsWith("/dashboards")
    ? { results: rows, count: 60, page: options.params.page, page_size: options.params.page_size }
    : { id: 9, name: "Sales team" }));
}
beforeEach(() => { jest.clearAllMocks(); currentUser.isAdmin = true; mockList(); axios.post.mockResolvedValue({ ...board, assigned: true, revision: 6 }); });

test("loads a server-side page and saves only the reviewed group-dashboard assignment", async () => {
  render(<GroupDashboards groupId="9" />);
  await screen.findByText("Sales report");
  expect(axios.get).toHaveBeenCalledWith("api/groups/9/dashboards", { params: { q: "", page: 1, page_size: 25 } });
  fireEvent.click(screen.getByRole("button", { name: "Assign to group" }));
  await waitFor(() => expect(axios.post).toHaveBeenCalledWith("api/groups/9/dashboards", {
    dashboard_id: 2, revision: 5, assigned: true,
  }));
  await screen.findByText("Dashboard assigned to the group. Members still need the corresponding data permissions.");
});

test("sends dashboard search and pagination to the server", async () => {
  render(<GroupDashboards groupId="9" />);
  await screen.findByText("Sales report");
  fireEvent.click(screen.getByTitle("Next Page"));
  await waitFor(() => expect(axios.get).toHaveBeenCalledWith("api/groups/9/dashboards", { params: { q: "", page: 2, page_size: 25 } }));
  const search = screen.getByLabelText("Search dashboards");
  fireEvent.change(search, { target: { value: "Revenue" } });
  fireEvent.keyDown(search, { key: "Enter", keyCode: 13, charCode: 13 });
  await waitFor(() => expect(axios.get).toHaveBeenCalledWith("api/groups/9/dashboards", { params: { q: "Revenue", page: 1, page_size: 25 } }));
});

test("shows stale revision denial and offers reload instead of claiming success", async () => {
  axios.post.mockRejectedValue({ response: { data: { message: "Dashboard access changed. Reload before saving." } } });
  render(<GroupDashboards groupId="9" />);
  fireEvent.click(await screen.findByRole("button", { name: "Assign to group" }));
  await screen.findByText("Dashboard access changed. Reload before saving.");
  expect(screen.queryByText("Dashboard assigned to the group. Members still need the corresponding data permissions.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reload dashboards" }));
  await screen.findByText("Sales report");
});

test("disallows new draft assignments while allowing removal of existing assignments", async () => {
  mockList([{ ...board, is_draft: true }, { ...board, id: 3, name: "Old draft", is_draft: true, assigned: true }]);
  render(<GroupDashboards groupId="9" />);
  expect((await screen.findByRole("button", { name: "Assign to group" })).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Remove assignment" }));
  await waitFor(() => expect(axios.post).toHaveBeenCalledWith("api/groups/9/dashboards", { dashboard_id: 3, revision: 5, assigned: false }));
});

test("non-admins cannot load the group dashboard management page", () => {
  currentUser.isAdmin = false;
  render(<GroupDashboards groupId="9" />);
  expect(screen.getByText("Administrator access required.")).toBeTruthy();
  expect(axios.get).not.toHaveBeenCalled();
});
