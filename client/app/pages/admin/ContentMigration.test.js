import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ContentMigration, selectedComponents } from "./ContentMigration";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";

jest.mock("@/services/axios", () => ({ axios: { post: jest.fn() } }));
jest.mock("@/services/auth", () => ({ currentUser: { hasPermission: jest.fn(() => true) } }));
jest.mock("@/services/settingsMenu", () => ({ add: jest.fn() }));
jest.mock("@/services/routes", () => ({ register: jest.fn() }));
jest.mock("@/components/ApplicationArea/routeWithUserSession", () => (configuration) => configuration);
jest.mock("@/components/admin/Layout", () => ({ children }) => <div>{children}</div>);

beforeEach(() => {
  jest.clearAllMocks();
  currentUser.hasPermission.mockReturnValue(true);
  URL.createObjectURL = jest.fn(() => "blob:synthetic-package");
  URL.revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

async function choosePackage() {
  fireEvent.change(screen.getByLabelText("Migration package"), {
    target: {
      files: [
        new File([JSON.stringify({ format: "encrypted-test", ciphertext: "opaque" })], "test.bidash", {
          type: "application/json",
        }),
      ],
    },
  });
  fireEvent.change(screen.getByLabelText("Package passphrase", { exact: true }), {
    target: { value: "long-synthetic-password" },
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "Inspect package" }).disabled).toBe(false));
}

test("component dependencies are explicit and user-free defaults export no accounts", async () => {
  expect(selectedComponents(["dashboards", "rules", "history"])).toEqual([
    "dashboards",
    "rules",
    "history",
    "queries",
    "groups",
    "sources",
  ]);
  axios.post.mockResolvedValue({ filename: "synthetic.bidash", package: { ciphertext: "opaque" } });
  render(<ContentMigration />);
  fireEvent.change(screen.getByLabelText("Package passphrase (at least 12 characters)"), {
    target: { value: "long-synthetic-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Export package" }));
  await screen.findByText("Encrypted package downloaded. Keep its passphrase separately; it is required for import.");
  const [url, body] = axios.post.mock.calls[0];
  expect(url).toBe("api/admin/migration/export");
  expect(body.components).toEqual(["queries", "dashboards", "sources"]);
  expect(body.include_credentials).toBe(false);
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:synthetic-package");
  expect(screen.getByLabelText("Package passphrase (at least 12 characters)").value).toBe("");
});

test("Reader and Editor cannot access migration UI or call an endpoint", () => {
  currentUser.hasPermission.mockReturnValue(false);
  render(<ContentMigration />);
  expect(screen.queryByRole("button", { name: "Export package" })).toBeNull();
  expect(screen.getByText("Only organization administrators can export or import content.")).toBeTruthy();
  expect(axios.post).not.toHaveBeenCalled();
});

test("inspection and explicit review are required before import; changing passphrase invalidates preview", async () => {
  axios.post.mockResolvedValue({
    preview_token: "synthetic-preview",
    counts: { queries: 2 },
    errors: [],
    warnings: [],
    components: ["queries", "sources"],
  });
  render(<ContentMigration />);
  await choosePackage();
  fireEvent.click(screen.getByRole("button", { name: "Inspect package" }));
  await screen.findByRole("region", { name: "Import preview" });
  expect(screen.getByRole("button", { name: "Import reviewed content" }).disabled).toBe(true);
  fireEvent.click(screen.getByText("I reviewed the components, source mappings, ownership and access rules."));
  expect(screen.getByRole("button", { name: "Import reviewed content" }).disabled).toBe(false);
  fireEvent.change(screen.getByLabelText("Package passphrase", { exact: true }), {
    target: { value: "another-synthetic-password" },
  });
  expect(screen.getByRole("button", { name: "Import reviewed content" }).disabled).toBe(true);
  expect(axios.post).toHaveBeenCalledTimes(1);
});

test("blocking dependencies prevent import and source labels are escaped", async () => {
  axios.post.mockResolvedValue({
    counts: { queries: 2 },
    errors: ["Map all source references."],
    warnings: [],
    components: ["queries"],
    sources: [{ id: "source-1", name: "<script>source</script>", type: "pg" }],
    target_sources: [],
  });
  const { container } = render(<ContentMigration />);
  await choosePackage();
  fireEvent.click(screen.getByRole("button", { name: "Inspect package" }));
  await screen.findByText("Map all source references.");
  expect(screen.getByRole("button", { name: "Import reviewed content" }).disabled).toBe(true);
  expect(container.querySelector("script")).toBeNull();
});

test("reviewed import sends preview token then clears the package and password", async () => {
  axios.post.mockImplementation((url) =>
    Promise.resolve(
      url.endsWith("/inspect")
        ? {
            preview_token: "synthetic-preview",
            counts: { queries: 1 },
            errors: [],
            warnings: [],
            components: ["queries", "sources"],
          }
        : { counts: { queries: 1 } }
    )
  );
  render(<ContentMigration />);
  await choosePackage();
  fireEvent.click(screen.getByRole("button", { name: "Inspect package" }));
  await screen.findByRole("region", { name: "Import preview" });
  fireEvent.click(screen.getByText("I reviewed the components, source mappings, ownership and access rules."));
  fireEvent.click(screen.getByRole("button", { name: "Import reviewed content" }));
  await screen.findByRole("region", { name: "Import result" });
  const [url, body] = axios.post.mock.calls[1];
  expect(url).toBe("api/admin/migration/import");
  expect(body.preview_token).toBe("synthetic-preview");
  expect(body.source_mapping).toEqual({});
  expect(screen.getByLabelText("Package passphrase", { exact: true }).value).toBe("");
  expect(screen.getByRole("button", { name: "Inspect package" }).disabled).toBe(true);
});

test("invalid files fail before inspection without sending contents", async () => {
  render(<ContentMigration />);
  fireEvent.change(screen.getByLabelText("Migration package"), {
    target: { files: [new File(["not-json"], "bad.bidash")] },
  });
  await screen.findByText("Choose a valid Bi-Dash migration package.");
  expect(axios.post).not.toHaveBeenCalled();
});

test("can select Queries only without silently including Data sources or Dashboards", async () => {
  axios.post.mockResolvedValue({ filename: "queries.bidash", package: { ciphertext: "opaque" } });
  render(<ContentMigration />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Dashboards and widgets (includes Queries)", exact: true }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Data sources", exact: true }));
  expect(screen.getByRole("checkbox", { name: "Data sources", exact: true }).checked).toBe(false);
  fireEvent.change(screen.getByLabelText("Package passphrase (at least 12 characters)"), {
    target: { value: "long-synthetic-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Export package" }));
  await screen.findByText("Encrypted package downloaded. Keep its passphrase separately; it is required for import.");
  expect(axios.post.mock.calls[0][1].components).toEqual(["queries"]);
});
