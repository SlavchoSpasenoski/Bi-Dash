import React from "react";
import PropTypes from "prop-types";
import { Alert, Button, Form, Modal, Select, Spin, Tabs } from "antd";
import { axios } from "@/services/axios";
import notification from "@/services/notification";
import { wrap as wrapDialog, DialogPropType } from "@/components/DialogWrapper";

class ShareDashboardDialog extends React.Component {
  static propTypes = {
    dashboard: PropTypes.object.isRequired,
    canManageSharing: PropTypes.bool.isRequired,
    dialog: DialogPropType.isRequired,
  };

  state = { loading: true, saving: false, configuration: null, selected: [], selectedGroups: [], error: "", saved: false };
  alive = false;

  componentDidMount() { this.alive = true; this.loadAccess(); }
  componentWillUnmount() { this.alive = false; }

  loadAccess = () => {
    this.setState({ loading: true, error: "", saved: false });
    axios.get(`api/dashboards/${this.props.dashboard.id}/access`)
      .then(configuration => {
        if (this.alive) this.setState({ configuration, selected: configuration.read_user_ids, selectedGroups: configuration.read_group_ids || [] });
      })
      .catch(error => {
        if (this.alive) this.setState({ error: error.response?.data?.message || "Could not load dashboard access." });
      })
      .finally(() => { if (this.alive) this.setState({ loading: false }); });
  };

  saveAccess = () => {
    const { configuration, selected, selectedGroups, saving, loading } = this.state;
    if (!configuration?.can_manage || !this.props.canManageSharing || saving || loading) return;
    this.setState({ saving: true, error: "", saved: false });
    axios.post(`api/dashboards/${this.props.dashboard.id}/access`, {
      read_user_ids: selected, revision: configuration.revision,
      ...(configuration.read_group_ids ? { read_group_ids: selectedGroups } : {}),
    }).then(configuration => {
      this.props.dashboard.version = configuration.revision;
      if (this.alive) this.setState({ configuration, selected: configuration.read_user_ids, selectedGroups: configuration.read_group_ids || [], saved: true });
      notification.success("Dashboard access saved");
    }).catch(error => {
      if (this.alive) this.setState({ error: error.response?.data?.message || "Could not save dashboard access. Reload and try again." });
    }).finally(() => { if (this.alive) this.setState({ saving: false }); });
  };

  render() {
    const { dialog, canManageSharing } = this.props;
    const { configuration, selected, selectedGroups, loading, saving, error, saved } = this.state;
    const unavailable = !canManageSharing || !configuration?.can_manage;
    const unpublished = configuration?.is_draft || configuration?.is_archived;
    return <Modal {...dialog.props} title="Dashboard access" footer={[
      <Button key="close" onClick={dialog.dismiss}>Close</Button>,
      <Button key="save" type="primary" loading={saving} disabled={loading || unavailable || (unpublished && selected.length + selectedGroups.length > 0)} onClick={this.saveAccess}>Save access</Button>,
    ]}>
      <p><strong>{this.props.dashboard.name}</strong></p>
      <Alert type="info" showIcon message="Choose who can view this dashboard"
        description="Selected users and current members of selected groups must sign in. The owner and organization administrators retain access. Organization administrators and the owner can edit dashboard content. Viewing does not grant access to additional data sources, tables or columns." />
      <p style={{ marginTop: 12 }}>Public links are disabled. Assign viewing to users or groups. Removing one grant does not remove access granted by another user or group assignment.</p>
      {error && <Alert type="error" message={error} action={<Button size="small" disabled={saving} onClick={this.loadAccess}>Reload access</Button>} />}
      {saved && <Alert type="success" message="Dashboard access saved." />}
      {loading ? <Spin /> : configuration && <Form layout="vertical" style={{ marginTop: 16 }}>
        {unpublished && <Alert type="warning" message="Publish this dashboard before assigning viewers. Existing grants can be removed while it is unpublished or archived." />}
        <Tabs defaultActiveKey="users">
        <Tabs.TabPane key="users" tab="Users">
        <Form.Item label="Users who can view" htmlFor="dashboard-viewers" extra="Editors can share dashboards they own. Readers cannot share dashboards.">
          <Select id="dashboard-viewers" mode="multiple" showSearch optionFilterProp="label"
            style={{ width: "100%" }} value={selected} disabled={saving || unavailable || unpublished}
            placeholder="Select Editors or Readers"
            options={configuration.candidates.map(user => ({ value: user.id, label: `${user.name} · ${user.role}` }))}
            onChange={value => this.setState({ selected: value, saved: false })} />
        </Form.Item>
        {selected.length > 0 && <Button disabled={saving || unavailable} onClick={() => this.setState({ selected: [], saved: false })}>Remove all selected users</Button>}
        </Tabs.TabPane>
        <Tabs.TabPane key="groups" tab="Groups">
        <Form.Item label="Groups who can view" htmlFor="dashboard-viewer-groups" extra="Membership is evaluated dynamically. Adding a member to a selected group grants visibility; leaving the group removes that path to access. Source permissions still apply.">
          <Select id="dashboard-viewer-groups" mode="multiple" showSearch optionFilterProp="label"
            style={{ width: "100%" }} value={selectedGroups} disabled={saving || unavailable || unpublished}
            placeholder="Select groups"
            options={(configuration.group_candidates || []).map(group => ({ value: group.id, label: group.name }))}
            onChange={value => this.setState({ selectedGroups: value, saved: false })} />
        </Form.Item>
        {selectedGroups.length > 0 && <Button disabled={saving || unavailable} onClick={() => this.setState({ selectedGroups: [], saved: false })}>Remove all selected groups</Button>}
        </Tabs.TabPane>
        </Tabs>
      </Form>}
    </Modal>;
  }
}

export default wrapDialog(ShareDashboardDialog);
