import { omit } from "lodash";
import React from "react";
import PropTypes from "prop-types";
import Alert from "antd/lib/alert";
import Button from "antd/lib/button";
import Card from "antd/lib/card";
import Table from "antd/lib/table";
import Tag from "antd/lib/tag";
import routeWithUserSession from "@/components/ApplicationArea/routeWithUserSession";
import Layout from "@/components/admin/Layout";
import * as StatusBlock from "@/components/admin/StatusBlock";
import { axios } from "@/services/axios";
import { currentUser } from "@/services/auth";
import routes from "@/services/routes";
import "./system-status.less";

function timestamp(value) {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Unknown" : parsed.toLocaleString();
}
function seconds(value) {
  return Number.isFinite(value) ? `${Math.max(0, Math.floor(value))}s` : "—";
}
const presenceLabels = { active: "Active", idle: "Idle", stale: "Stale / unknown", unknown: "Unknown" };

export class SystemStatus extends React.Component {
  static propTypes = { onError: PropTypes.func };
  static defaultProps = { onError: () => {} };
  state = {
    report: null,
    error: false,
    loading: false,
    usersPage: 1,
    jobsPage: 1,
    infrastructure: null,
    infrastructureError: false,
  };
  _timer = null;
  _mounted = false;
  _inFlight = false;
  _refreshPending = false;

  componentDidMount() {
    this._mounted = true;
    document.addEventListener("visibilitychange", this.visibilityChanged);
    this.refresh();
  }
  componentWillUnmount() {
    this._mounted = false;
    clearTimeout(this._timer);
    document.removeEventListener("visibilitychange", this.visibilityChanged);
  }
  visibilityChanged = () => {
    clearTimeout(this._timer);
    if (!document.hidden) this.refresh();
  };
  refresh = async () => {
    clearTimeout(this._timer);
    if (!this._mounted || document.hidden || !(currentUser.isAdmin || currentUser.hasPermission("super_admin"))) return;
    if (this._inFlight) {
      this._refreshPending = true;
      return;
    }
    this._inFlight = true;
    this.setState({ loading: true });
    const { usersPage, jobsPage } = this.state;
    let instanceAdmin = false;
    try {
      const report = await axios.get("api/admin/monitoring", {
        params: { users_page: usersPage, jobs_page: jobsPage, page_size: 25 },
      });
      instanceAdmin = report.instance_admin === true;
      if (this._mounted) this.setState({ report, error: false, ...(instanceAdmin ? {} : { infrastructure: null }) });
    } catch (error) {
      const denied = [401, 403].includes(error?.response?.status);
      if (this._mounted)
        this.setState({
          error: true,
          infrastructure: null,
          report: !denied && this.state.report ? { ...this.state.report, instance_admin: false } : null,
        });
    }
    // Instance-wide infrastructure remains behind the existing super-admin gate.
    if (instanceAdmin) {
      try {
        const infrastructure = await axios.get("/status.json");
        if (this._mounted) this.setState({ infrastructure, infrastructureError: false });
      } catch {
        if (this._mounted) this.setState({ infrastructureError: true });
      }
    }
    this._inFlight = false;
    if (!this._mounted) return;
    this.setState({ loading: false });
    if (this._refreshPending) {
      this._refreshPending = false;
      this.refresh();
    } else if (!document.hidden) {
      this._timer = setTimeout(this.refresh, 10000);
    }
  };
  pageChanged = (field, page) => this.setState({ [field]: page }, this.refresh);

  render() {
    if (!(currentUser.isAdmin || currentUser.hasPermission("super_admin"))) {
      return <Alert type="error" message="Administrator access required." />;
    }
    const { report, error, loading, infrastructure, infrastructureError } = this.state;
    const users = report?.users;
    const jobs = report?.jobs;
    const usersColumns = [
      { title: "User", dataIndex: "name" },
      { title: "Role", dataIndex: "role" },
      { title: "Presence", dataIndex: "presence", render: (value) => <Tag>{presenceLabels[value] || "Unknown"}</Tag> },
      { title: "Last heartbeat", dataIndex: "last_seen_at", render: timestamp },
      { title: "Last activity", dataIndex: "last_activity_at", render: timestamp },
    ];
    const jobsColumns = [
      {
        title: "Query",
        key: "query",
        render: (_, job) => job.query_name || (job.query_id ? `Query #${job.query_id}` : "Ad hoc"),
      },
      { title: "Initiated by", dataIndex: "user_name", render: (value) => value || "Attribution unavailable" },
      { title: "Source", dataIndex: "data_source_name", render: (value) => value || "Unavailable" },
      { title: "Trigger", dataIndex: "trigger" },
      {
        title: "State",
        dataIndex: "state",
        render: (value, job) => (
          <>
            <Tag>{value === "started" ? "Running" : value}</Tag>
            {job.long_running && <Tag color="orange">Long-running</Tag>}
            {job.worker_stale && <Tag color="red">Worker stale</Tag>}
          </>
        ),
      },
      { title: "Queued at", dataIndex: "enqueued_at", render: timestamp },
      { title: "Started at", dataIndex: "started_at", render: timestamp },
      { title: "Wait", dataIndex: "wait_seconds", render: seconds },
      { title: "Runtime", dataIndex: "runtime_seconds", render: seconds },
    ];
    const pagination = (section, field) => ({
      current: section?.page || this.state[field],
      pageSize: section?.page_size || 25,
      total: section?.total || 0,
      showSizeChanger: false,
      onChange: (page) => this.pageChanged(field, page),
    });
    const manager = infrastructure?.manager;
    return (
      <Layout activeTab="system_status">
        <div className="system-status-page">
          <div className="system-monitor-toolbar">
            <div>
              <h3>Organization activity</h3>
              <p>Updated: {timestamp(report?.generated_at)} · Refreshes every 10 seconds while this page is visible.</p>
            </div>
            <Button onClick={this.refresh} loading={loading}>
              Refresh monitoring
            </Button>
          </div>
          {error && (
            <Alert
              className="m-b-15"
              type="error"
              showIcon
              message="Monitoring could not refresh. Previously displayed data may be stale."
            />
          )}
          {report?.health?.activity === "unavailable" && (
            <Alert
              className="m-b-15"
              type="warning"
              message="Live activity telemetry is unavailable; presence is unknown."
            />
          )}
          {report?.health?.jobs && report.health.jobs !== "ok" && (
            <Alert
              className="m-b-15"
              type="warning"
              message="Job telemetry is incomplete or unavailable. This does not mean there are no active queries."
            />
          )}
          {(report?.warnings || []).map((warning, index) => (
            <Alert className="m-b-15" key={index} type="warning" message={warning} />
          ))}
          <Card title="Users and recent activity" className="m-b-15">
            <p>
              Presence uses short-lived browser heartbeats. Active means a recent interaction; Idle means a fresh
              heartbeat without recent interaction. Stale / unknown is not a confirmed logout. Last request activity
              alone does not mean a user is online.
            </p>
            <Table
              rowKey="id"
              columns={usersColumns}
              dataSource={users?.results || []}
              loading={loading && !report}
              pagination={pagination(users, "usersPage")}
              scroll={{ x: 800 }}
              locale={{
                emptyText:
                  report?.health?.activity === "unavailable" ? "Activity unavailable" : "No users in this page",
              }}
            />
          </Card>
          <Card title="Query execution" className="m-b-15">
            <p>
              Queued, running and bounded recent jobs from the last hour. Cache hits may not create jobs; a deduplicated
              job can serve multiple requests. Long-running is a diagnostic hint, not proof of a database lock.
            </p>
            {jobs?.truncated && (
              <Alert
                type="info"
                className="m-b-15"
                message="Only a bounded job sample is available. Counts do not represent the complete execution history."
              />
            )}
            <Table
              rowKey="id"
              columns={jobsColumns}
              dataSource={jobs?.results || []}
              loading={loading && !report}
              pagination={pagination(jobs, "jobsPage")}
              scroll={{ x: 1200 }}
              locale={{
                emptyText:
                  report?.health?.jobs === "unavailable" ? "Job telemetry unavailable" : "No jobs in this sample",
              }}
            />
          </Card>
          {report?.instance_admin === true && (
            <>
              <Card title="Instance workers" className="m-b-15">
                <Table
                  rowKey="name"
                  pagination={false}
                  dataSource={report?.workers || []}
                  scroll={{ x: 650 }}
                  columns={[
                    { title: "Worker", dataIndex: "name" },
                    { title: "State", dataIndex: "state" },
                    { title: "Last heartbeat", dataIndex: "last_heartbeat", render: timestamp },
                    {
                      title: "Health",
                      dataIndex: "stale",
                      render: (stale) => <Tag color={stale ? "red" : "green"}>{stale ? "Stale" : "Fresh"}</Tag>,
                    },
                    { title: "Current job", dataIndex: "current_job_id", render: (value) => value || "—" },
                  ]}
                />
              </Card>
              {infrastructureError && (
                <Alert type="warning" className="m-b-15" message="Instance infrastructure metrics could not refresh." />
              )}
              {infrastructure && (
                <div className="system-status-page-blocks">
                  <div className="system-status-page-block">
                    <StatusBlock.General info={omit(infrastructure, ["workers", "manager", "database_metrics"])} />
                  </div>
                  <div className="system-status-page-block">
                    <StatusBlock.Manager
                      info={
                        manager
                          ? {
                              startedAt: manager.started_at * 1000,
                              lastRefreshAt: manager.last_refresh_at * 1000,
                              outdatedQueriesCount: manager.outdated_queries_count,
                            }
                          : null
                      }
                    />
                  </div>
                  <div className="system-status-page-block">
                    <StatusBlock.Queues info={manager?.queues || []} />
                  </div>
                  <div className="system-status-page-block">
                    <StatusBlock.DatabaseMetrics info={infrastructure.database_metrics?.metrics || []} />
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </Layout>
    );
  }
}

routes.register(
  "Admin.SystemStatus",
  routeWithUserSession({
    path: "/admin/status",
    title: "System Status",
    render: (props) => <SystemStatus {...props} />,
  })
);
