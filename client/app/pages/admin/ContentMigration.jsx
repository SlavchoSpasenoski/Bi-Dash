import React, { useRef, useState } from "react";
import Alert from "antd/lib/alert";
import Button from "antd/lib/button";
import Card from "antd/lib/card";
import Checkbox from "antd/lib/checkbox";
import Input from "antd/lib/input";
import Select from "antd/lib/select";
import Table from "antd/lib/table";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import Layout from "@/components/admin/Layout";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";
import routes from "@/services/routes";
import settingsMenu from "@/services/settingsMenu";

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const components = [
  ["queries", "Queries and visualizations"],
  ["dashboards", "Dashboards and widgets (includes Queries)"],
  ["sources", "Data sources"],
  ["users", "Users (identity and role; imported disabled; includes Groups)"],
  ["groups", "Groups and memberships, when Users are included"],
  ["rules", "Source/table/column rules (includes Groups and Data sources)"],
  ["policies", "Role restrictions and execution limits"],
  ["history", "Query version history (includes Queries)"],
  ["ai", "AI settings and context"],
];

export function selectedComponents(values) {
  const result = new Set(values);
  if (result.has("dashboards") || result.has("history")) result.add("queries");
  if (result.has("rules") || result.has("users")) result.add("groups");
  if (result.has("rules")) result.add("sources");
  return [...result];
}

function message(error) {
  return (
    error.response?.data?.message ||
    "Migration request failed. Check the package, passphrase and connection, then try again."
  );
}

export function downloadPackage(result) {
  const blob = new Blob([JSON.stringify(result.package)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = (result.filename || "bi-dash-content.bidash").replace(/[\\/]/g, "_");
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

const componentNames = {
  sources: "Data sources",
  groups: "Groups",
  users: "Users",
  queries: "Queries",
  visualizations: "Visualizations",
  dashboards: "Dashboards",
  widgets: "Widgets",
  history: "Query versions",
  source_groups: "Source group permissions",
  grants: "Dashboard viewers",
};

function Counts({ counts }) {
  return (
    <Table
      size="small"
      pagination={false}
      rowKey="name"
      columns={[
        { title: "Component", dataIndex: "name" },
        { title: "Items", dataIndex: "count" },
      ]}
      dataSource={Object.entries(counts || {}).map(([name, count]) => ({ name: componentNames[name] || name, count }))}
    />
  );
}

export function ContentMigration() {
  const [selected, setSelected] = useState(["queries", "dashboards", "sources"]);
  const [includeCredentials, setIncludeCredentials] = useState(false);
  const [exportPassphrase, setExportPassphrase] = useState("");
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState("");
  const [exported, setExported] = useState(false);
  const [envelope, setEnvelope] = useState(null);
  const [fileName, setFileName] = useState("");
  const [importPassphrase, setImportPassphrase] = useState("");
  const [mapping, setMapping] = useState({});
  const [inspection, setInspection] = useState(null);
  const [previewToken, setPreviewToken] = useState(null);
  const [reviewed, setReviewed] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState("");
  const [imported, setImported] = useState(null);
  const version = useRef(0);
  const fileInput = useRef(null);
  const fileVersion = useRef(0);
  const effective = selectedComponents(selected);

  const invalidate = () => {
    version.current += 1;
    setPreviewToken(null);
    setReviewed(false);
    setImported(null);
    setImportError("");
  };
  const exportContent = async () => {
    setExportBusy(true);
    setExportError("");
    setExported(false);
    try {
      const result = await axios.post("api/admin/migration/export", {
        components: effective,
        include_credentials: includeCredentials,
        passphrase: exportPassphrase,
      });
      downloadPackage(result);
      setExported(true);
      setExportPassphrase("");
    } catch (error) {
      setExportError(message(error));
    } finally {
      setExportBusy(false);
    }
  };

  const chooseFile = async (event) => {
    invalidate();
    setInspection(null);
    setEnvelope(null);
    setMapping({});
    setImportPassphrase("");
    const requestVersion = ++fileVersion.current;
    const file = event.target.files?.[0];
    setFileName(file?.name || "");
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      setImportError("The package exceeds the 20 MiB upload limit.");
      return;
    }
    try {
      const text = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("File read failed"));
        reader.readAsText(file);
      });
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Invalid package");
      if (requestVersion === fileVersion.current) setEnvelope(parsed);
    } catch {
      if (requestVersion === fileVersion.current) setImportError("Choose a valid Bi-Dash migration package.");
    }
  };

  const inspect = async () => {
    setImportBusy(true);
    setImportError("");
    setPreviewToken(null);
    setReviewed(false);
    const requestVersion = version.current;
    try {
      const result = await axios.post("api/admin/migration/inspect", {
        package: envelope,
        passphrase: importPassphrase,
        source_mapping: mapping,
      });
      if (requestVersion !== version.current) return;
      setInspection(result);
      setPreviewToken(result.errors?.length ? null : result.preview_token);
    } catch (error) {
      if (requestVersion === version.current) {
        setInspection(null);
        setImportError(message(error));
      }
    } finally {
      setImportBusy(false);
    }
  };

  const importContent = async () => {
    if (!reviewed || !previewToken) return;
    setImportBusy(true);
    setImportError("");
    try {
      const result = await axios.post("api/admin/migration/import", {
        package: envelope,
        passphrase: importPassphrase,
        source_mapping: mapping,
        preview_token: previewToken,
      });
      setImported(result);
      setPreviewToken(null);
      setReviewed(false);
      setImportPassphrase("");
      setEnvelope(null);
      setFileName("");
      setInspection(null);
      if (fileInput.current) fileInput.current.value = "";
    } catch (error) {
      setImportError(message(error));
      setPreviewToken(null);
      setReviewed(false);
    } finally {
      setImportBusy(false);
    }
  };

  if (!currentUser.hasPermission("admin"))
    return <Alert type="error" message="Only organization administrators can export or import content." />;
  const mappedSources = inspection && !inspection.components?.includes("sources") ? inspection.sources || [] : [];
  return (
    <Layout activeTab="migration">
      <div className="p-15">
        <h3>Backup &amp; Migration</h3>
        <p>
          Transfer selected content to another compatible Bi-Dash installation. This is a content import, not a database
          restore.
        </p>
        <Alert
          type="info"
          className="m-b-15"
          message="Without Users, all imported Queries and Dashboards belong to the importing Admin. Groups and rules are optional; imported groups have no members when Users are omitted."
          description="Import does not execute queries, connect to sources or activate schedules. New data sources are isolated until an Admin configures their access. Existing installation encryption keys are preserved."
        />
        <Card title="Export content" className="m-b-15">
          <Checkbox.Group
            value={selected}
            disabled={exportBusy}
            onChange={(value) => {
              setSelected(value);
              setExported(false);
            }}
          >
            {components.map(([value, label]) => (
              <div key={value} className="m-b-5">
                <Checkbox value={value}>{label}</Checkbox>
              </div>
            ))}
          </Checkbox.Group>
          <p className="m-t-10">
            Dashboards include the active organization query set and its visualizations. Rules include group definitions
            and Data sources. Users include their group definitions. Query history also includes Queries.
          </p>
          <div className="m-b-10">
            <Checkbox
              checked={includeCredentials}
              disabled={exportBusy}
              onChange={(event) => setIncludeCredentials(event.target.checked)}
            >
              Include saved source/provider credentials in the encrypted package
            </Checkbox>
          </div>
          <label htmlFor="migration-export-passphrase">Package passphrase (at least 12 characters)</label>
          <Input.Password
            id="migration-export-passphrase"
            autoComplete="new-password"
            value={exportPassphrase}
            disabled={exportBusy}
            onChange={(event) => setExportPassphrase(event.target.value)}
            className="m-b-10"
          />
          {exportError && <Alert type="error" className="m-b-10" message={exportError} />}
          {exported && (
            <Alert
              type="success"
              className="m-b-10"
              message="Encrypted package downloaded. Keep its passphrase separately; it is required for import."
            />
          )}
          <Button
            type="primary"
            loading={exportBusy}
            disabled={!effective.length || exportPassphrase.length < 12}
            onClick={exportContent}
          >
            Export package
          </Button>
        </Card>
        <Card title="Import content">
          <p>
            Choose a package, inspect it, review dependencies and ownership, then import. Existing reports and accounts
            are not overwritten.
          </p>
          <label htmlFor="migration-import-file">Migration package</label>
          <input
            id="migration-import-file"
            ref={fileInput}
            type="file"
            accept=".bidash,.json,application/json"
            disabled={importBusy}
            onChange={chooseFile}
            className="d-block m-b-10"
          />
          {fileName && <p>Selected: {fileName}</p>}
          <label htmlFor="migration-import-passphrase">Package passphrase</label>
          <Input.Password
            id="migration-import-passphrase"
            autoComplete="off"
            value={importPassphrase}
            disabled={importBusy}
            onChange={(event) => {
              invalidate();
              setImportPassphrase(event.target.value);
            }}
            className="m-b-10"
          />
          {importError && <Alert type="error" className="m-b-10" message={importError} />}
          <Button
            disabled={!envelope || importPassphrase.length < 12}
            loading={importBusy}
            onClick={inspect}
            className="m-b-15"
          >
            Inspect package
          </Button>
          {inspection && (
            <section aria-label="Import preview">
              <h4>Import preview</h4>
              <Counts counts={inspection.counts} />
              <p className="m-t-10">
                Ownership:{" "}
                {inspection.components?.includes("users")
                  ? "Imported resources belong to their newly imported, disabled user accounts."
                  : "All imported resources belong to the importing Admin."}
              </p>
              {(inspection.warnings || []).map((warning, index) => (
                <Alert key={`${index}-${warning}`} type="warning" className="m-b-10" message={warning} />
              ))}
              {(inspection.errors || []).map((error, index) => (
                <Alert key={`${index}-${error}`} type="error" className="m-b-10" message={error} />
              ))}
              {mappedSources.length > 0 && (
                <div className="m-b-15">
                  <h4>Map data sources</h4>
                  <p>
                    Sources were not included. Select an existing source of the matching connector type for every
                    reference, then inspect again.
                  </p>
                  {mappedSources.map((source) => (
                    <div key={source.id} className="m-b-10">
                      <label htmlFor={`migration-source-${source.id}`}>
                        {source.name} ({source.type})
                      </label>
                      <Select
                        id={`migration-source-${source.id}`}
                        aria-label={`Map source ${source.name}`}
                        style={{ width: "100%" }}
                        disabled={importBusy}
                        value={mapping[source.id]}
                        placeholder="Choose destination source"
                        onChange={(value) => {
                          invalidate();
                          setMapping({ ...mapping, [source.id]: value });
                        }}
                      >
                        {(inspection.target_sources || [])
                          .filter((target) => target.type === source.type)
                          .map((target) => (
                            <Select.Option key={target.id} value={target.id}>
                              {target.name} ({target.type})
                            </Select.Option>
                          ))}
                      </Select>
                    </div>
                  ))}
                </div>
              )}
              <div className="m-b-10">
                <Checkbox
                  disabled={!previewToken || importBusy}
                  checked={reviewed}
                  onChange={(event) => setReviewed(event.target.checked)}
                >
                  I reviewed the components, source mappings, ownership and access rules.
                </Checkbox>
              </div>
              <Button type="primary" disabled={!previewToken || !reviewed} loading={importBusy} onClick={importContent}>
                Import reviewed content
              </Button>
            </section>
          )}
          {imported && (
            <section aria-label="Import result">
              <Alert
                type="success"
                className="m-b-10"
                message="Content imported successfully. Configure source access and credentials before enabling users or schedules."
              />
              <Counts counts={imported.counts} />
              {(imported.warnings || []).map((warning, index) => (
                <Alert key={`${index}-${warning}`} type="warning" className="m-t-10" message={warning} />
              ))}
            </section>
          )}
        </Card>
      </div>
    </Layout>
  );
}

settingsMenu.add("Settings.Migration", {
  permission: "admin",
  title: "Migration",
  path: "admin/migration",
  order: 7.5,
});

routes.register(
  "Admin.ContentMigration",
  routeWithUserSession({ path: "/admin/migration", title: "Backup & Migration", render: () => <ContentMigration /> })
);

export default ContentMigration;
