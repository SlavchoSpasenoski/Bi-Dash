from flask import request
from flask_restful import abort
from funcy import project

from redash import models, utils
from redash.handlers.base import (
    BaseResource,
    get_object_or_404,
    require_fields,
)
from redash.permissions import (
    has_access,
    require_access,
    require_admin_or_owner,
    require_permission,
    view_only,
)
from redash.serializers import serialize_alert
from redash.tasks.alerts import (
    notify_subscriptions,
    should_notify,
)


def readable_alerts(user, org):
    alerts = models.Alert.all(group_ids=user.group_ids)
    if user.has_permission("admin"):
        alerts = models.Alert.query.join(models.Query)
    alerts = alerts.filter(models.Query.org_id == org.id)
    return [alert for alert in alerts if has_access(alert, user, view_only)]


class AlertResource(BaseResource):
    def get(self, alert_id):
        alert = get_object_or_404(models.Alert.get_by_id_and_org, alert_id, self.current_org)
        require_access(alert, self.current_user, view_only)
        self.record_event({"action": "view", "object_id": alert.id, "object_type": "alert"})
        return serialize_alert(alert)

    def post(self, alert_id):
        req = request.get_json(True)
        params = project(req, ("options", "name", "query_id", "rearm"))
        alert = get_object_or_404(models.Alert.get_by_id_and_org, alert_id, self.current_org)
        require_admin_or_owner(alert.user.id)

        if "query_id" in params:
            query = get_object_or_404(models.Query.get_by_id_and_org, params.pop("query_id"), self.current_org)
        else:
            query = alert.query_rel
        require_access(query, self.current_user, view_only)
        alert.query_rel = query

        self.update_model(alert, params)
        models.db.session.commit()

        self.record_event({"action": "edit", "object_id": alert.id, "object_type": "alert"})

        return serialize_alert(alert)

    def delete(self, alert_id):
        alert = get_object_or_404(models.Alert.get_by_id_and_org, alert_id, self.current_org)
        require_admin_or_owner(alert.user_id)
        models.db.session.delete(alert)
        models.db.session.commit()


class AlertEvaluateResource(BaseResource):
    def post(self, alert_id):
        alert = get_object_or_404(models.Alert.get_by_id_and_org, alert_id, self.current_org)
        from redash.data_access_runtime import is_protected
        if alert.query_rel.data_source and is_protected(alert.query_rel.data_source):
            abort(403, message="Alerts are unavailable for protected data sources.")
        require_admin_or_owner(alert.user.id)
        require_access(alert, self.current_user, view_only)

        new_state = alert.evaluate()
        if should_notify(alert, new_state):
            alert.state = new_state
            alert.last_triggered_at = utils.utcnow()
            models.db.session.commit()

        notify_subscriptions(alert, new_state, {})
        self.record_event({"action": "evaluate", "object_id": alert.id, "object_type": "alert"})


class AlertMuteResource(BaseResource):
    def post(self, alert_id):
        alert = get_object_or_404(models.Alert.get_by_id_and_org, alert_id, self.current_org)
        require_admin_or_owner(alert.user.id)

        alert.options["muted"] = True
        models.db.session.commit()

        self.record_event({"action": "mute", "object_id": alert.id, "object_type": "alert"})

    def delete(self, alert_id):
        alert = get_object_or_404(models.Alert.get_by_id_and_org, alert_id, self.current_org)
        require_admin_or_owner(alert.user.id)

        alert.options["muted"] = False
        models.db.session.commit()

        self.record_event({"action": "unmute", "object_id": alert.id, "object_type": "alert"})


class AlertListResource(BaseResource):
    def post(self):
        req = request.get_json(True)
        require_fields(req, ("options", "name", "query_id"))

        query = models.Query.get_by_id_and_org(req["query_id"], self.current_org)
        require_access(query, self.current_user, view_only)

        alert = models.Alert(
            name=req["name"],
            query_rel=query,
            user=self.current_user,
            rearm=req.get("rearm"),
            options=req["options"],
        )

        models.db.session.add(alert)
        models.db.session.flush()
        models.db.session.commit()

        self.record_event({"action": "create", "object_id": alert.id, "object_type": "alert"})

        return serialize_alert(alert)

    @require_permission("list_alerts")
    def get(self):
        self.record_event({"action": "list", "object_type": "alert"})
        return [serialize_alert(alert) for alert in readable_alerts(self.current_user, self.current_org)]


class AlertSubscriptionListResource(BaseResource):
    def post(self, alert_id):
        req = request.get_json(True)

        alert = models.Alert.get_by_id_and_org(alert_id, self.current_org)
        require_access(alert, self.current_user, view_only)
        kwargs = {"alert": alert, "user": self.current_user}

        if "destination_id" in req:
            destination = models.NotificationDestination.get_by_id_and_org(req["destination_id"], self.current_org)
            kwargs["destination"] = destination

        subscription = models.AlertSubscription(**kwargs)
        models.db.session.add(subscription)
        models.db.session.commit()

        self.record_event(
            {
                "action": "subscribe",
                "object_id": alert_id,
                "object_type": "alert",
                "destination": req.get("destination_id"),
            }
        )

        d = subscription.to_dict()
        return d

    def get(self, alert_id):
        alert = models.Alert.get_by_id_and_org(alert_id, self.current_org)
        require_access(alert, self.current_user, view_only)

        subscriptions = models.AlertSubscription.all(alert_id)
        return [s.to_dict() for s in subscriptions]


class AlertSubscriptionResource(BaseResource):
    def delete(self, alert_id, subscriber_id):
        alert = get_object_or_404(models.Alert.get_by_id_and_org, alert_id, self.current_org)
        subscription = models.AlertSubscription.query.filter_by(id=subscriber_id, alert_id=alert.id).first_or_404()
        require_admin_or_owner(subscription.user.id)
        models.db.session.delete(subscription)
        models.db.session.commit()

        self.record_event({"action": "unsubscribe", "object_id": alert_id, "object_type": "alert"})
