# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
from flask import request, session
from flask_restful import abort

from redash.access_policy import ROLES, policy_for, validate_limits, validate_policy
from redash.handlers.base import BaseResource, get_object_or_404
from redash.models import Organization, User, db
from redash.permissions import require_admin
from redash.security import csrf


def access_body():
    if "_user_id" in session or "user_id" in session:
        csrf.protect()
    if request.content_length and request.content_length > 16000:
        abort(413)
    body = request.get_json()
    if not isinstance(body, dict):
        abort(400, message="Expected a JSON object.")
    return body


def lock_administrator(org, actor):
    Organization.query.filter_by(id=org.id).with_for_update().one()
    db.session.refresh(actor)
    if actor.is_disabled or not actor.has_permission("admin"):
        abort(403, message="Administrator access is no longer available.")


class AccessPolicyResource(BaseResource):
    @require_admin
    def get(self):
        return policy_for(self.current_org)

    @require_admin
    def post(self):
        try:
            policy = validate_policy(access_body())
        except ValueError as error:
            abort(400, message=str(error))
        self.current_org.settings["access_policy"] = policy
        db.session.commit()
        self.record_event({"action": "edit", "object_type": "access_policy", "object_id": self.current_org.id})
        return policy


class UserAccessResource(BaseResource):
    @require_admin
    def get(self, user_id):
        user = get_object_or_404(User.get_by_id_and_org, user_id, self.current_org)
        return {"role": user.role, "limits": (user.details or {}).get("access_limits", {})}

    @require_admin
    def post(self, user_id):
        body = access_body()
        role = body.get("role")
        if not isinstance(role, str) or role not in ROLES:
            abort(400, message="Choose Admin, Editor or Reader.")
        try:
            limits = validate_limits(body.get("limits", {}))
        except ValueError as error:
            abort(400, message=str(error))
        # Serialize role changes within an organization; never permit self-demotion.
        lock_administrator(self.current_org, self.current_user)
        user = get_object_or_404(User.get_by_id_and_org, user_id, self.current_org)
        if user.id == self.current_user.id and role != "admin":
            abort(400, message="An administrator cannot demote their own account.")
        previous_role = user.role
        user.details = {**(user.details or {}), "role": role, "access_limits": limits}
        db.session.commit()
        self.record_event({"action": "edit", "object_type": "user_access", "object_id": user.id,
                           "previous_role": previous_role, "role": role, "limits": limits})
        return {"role": role, "limits": limits}
