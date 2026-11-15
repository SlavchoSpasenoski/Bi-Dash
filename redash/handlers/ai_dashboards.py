# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
import os
import uuid

from flask_restful import abort
from redis.exceptions import LockError

from redash import limiter, models, redis_connection
from redash.ai_assistant import AssistantError, AssistantTimeoutError, ask_assistant
from redash.ai_dashboard import SUPPORTED_SOURCES, parse_plan, reference_schema, visualization_options
from redash.handlers.ai import ai_configuration, json_body, require_unprotected_ai
from redash.handlers.base import BaseResource, get_object_or_404
from redash.permissions import has_access, not_view_only, require_permissions
from redash.ai_access import (available_sources, is_editor, selected_tables,
                              source_snapshot, validate_editor_sql)

BUILDER_PERMISSIONS = ("use_ai", "view_source", "view_query", "create_query", "create_dashboard",
                       "edit_query", "edit_dashboard", "execute_query")


def draft_response(dashboard):
    widgets = [w for w in dashboard.widgets if w.visualization_id]
    return {"dashboard_id": dashboard.id, "url": f"dashboards/{dashboard.id}", "title": dashboard.name,
            "description": dashboard.options.get("ai_description", ""),
            "queries": [{"id": w.visualization.query_rel.id, "title": w.visualization.name,
                         "visualization_id": w.visualization_id, "type": w.visualization.type,
                         "parameters": {p["name"]: p["value"] for p in w.visualization.query_rel.options.get("parameters", [])},
                         "columns": list(w.visualization.options.get("columnMapping", {}))
                         if w.visualization.type == "CHART" else
                         ([w.visualization.options["counterColName"]] if w.visualization.type == "COUNTER" else [])}
                        for w in sorted(widgets, key=lambda w: w.id)]}


class AssistantDashboardResource(BaseResource):
    @require_permissions(BUILDER_PERMISSIONS)
    def get(self):
        require_unprotected_ai(self.current_org, ai_user=self.current_user)
        if is_editor(self.current_user):
            return {"scoped": True, "data_sources": [{"id": ds.id, "name": ds.name, "type": ds.type}
                                                     for ds in available_sources(self.current_org, self.current_user)]}
        sources = models.DataSource.query.filter_by(org_id=self.current_org.id).all()
        return {"scoped": False, "data_sources": [{"id": ds.id, "name": ds.name, "type": ds.type} for ds in sources
                                 if ds.type in SUPPORTED_SOURCES and not ds.paused
                                 and has_access(ds, self.current_user, not_view_only)]}

    @require_permissions(BUILDER_PERMISSIONS)
    @limiter.limit("4/minute")
    def post(self):
        require_unprotected_ai(self.current_org, ai_user=self.current_user)
        body = json_body()
        scoped = is_editor(self.current_user)
        prompt = body.get("prompt")
        extra = body.get("context", "")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 8000:
            abort(400, message="Describe the report in 1–8000 characters.")
        if not isinstance(extra, str) or len(extra) > 12000:
            abort(400, message="Extra context must be at most 12000 characters.")
        if type(body.get("data_source_id")) is not int:
            abort(400, message="Select a data source.")
        try:
            request_id = str(uuid.UUID(body.get("request_id", "")))
        except (ValueError, TypeError, AttributeError):
            abort(400, message="A valid dashboard request ID is required.")
        org_id, user_id = self.current_org.id, self.current_user.id

        def existing_draft():
            return models.Dashboard.query.filter_by(org_id=org_id, user_id=user_id).filter(
                models.Dashboard.options["ai_request_id"].astext == request_id).first()

        source = get_object_or_404(models.DataSource.get_by_id_and_org, body["data_source_id"], self.current_org)
        if source.type not in SUPPORTED_SOURCES or source.paused or not has_access(source, self.current_user, not_view_only):
            abort(403, message="Choose an accessible, active PostgreSQL or MySQL/MariaDB data source.")
        snapshot = None
        if scoped:
            try:
                snapshot = source_snapshot(source, self.current_user)
            except ValueError as error:
                abort(403, message=str(error))

        def checked_draft(dashboard, current):
            if any(w.visualization and w.visualization.query_rel.data_source_id != source.id for w in dashboard.widgets):
                abort(409, message="Use a new request ID when changing the data source.")
            if scoped:
                if dashboard.options.get("ai_access_scope") != current["fingerprint"]:
                    abort(403, message="AI data access changed. Use a new dashboard request.")
                try:
                    for widget in dashboard.widgets:
                        if widget.visualization:
                            query = widget.visualization.query_rel
                            validate_editor_sql(query.query_text, current, query.options.get("parameters", []))
                except ValueError:
                    abort(403, message="This AI draft is outside your current data access.")
            return draft_response(dashboard)

        existing = existing_draft()
        if existing:
            return checked_draft(existing, snapshot)
        schema = snapshot["schema"] if scoped else source.get_cached_schema()
        if not schema:
            abort(409, message="Load the data-source schema before creating a dashboard.")
        try:
            tables = selected_tables(body.get("tables"), snapshot) if scoped else body.get("tables")
            reference = reference_schema(schema, tables, source.type)
        except ValueError as error:
            abort(400, message=str(error))
        configuration = ai_configuration(self.current_org)
        if not configuration["enabled"]:
            abort(503, message="Configure the AI provider in Settings → AI Configuration.")
        context = "" if scoped else self.current_org.settings.get("ai_context", "")
        if scoped:
            extra = ""
        if len(context) + len(reference) + len(extra) > 30000:
            abort(400, message="The combined context is too large. Choose fewer tables or shorten the AI context.")
        # Do not hold a database connection while a local model is generating its plan.
        models.db.session.close()
        try:
            answer = ask_assistant(
                configuration["api_key"], configuration["model"], [{"role": "user", "content": prompt.strip()}],
                reference, provider=configuration["provider"], base_url=configuration["base_url"],
                context="\n\n".join(part for part in (context, extra) if part), dashboard=True,
                ollama_timeout=configuration["timeout_seconds"], request_timeout=configuration["timeout_seconds"],
                ollama_keep_alive=os.environ.get("REDASH_OLLAMA_KEEP_ALIVE", "30m"),
            )
            plan = parse_plan(answer)
        except AssistantTimeoutError as error:
            abort(504, message=str(error))
        except AssistantError as error:
            abort(502, message=str(error))
        except ValueError as error:
            abort(422, message=str(error))
        # Recheck permissions and source access after the provider request.
        def recheck():
            models.db.session.expire_all()
            user = models.User.query.filter_by(id=user_id).populate_existing().first()
            org = models.Organization.query.filter_by(id=org_id).populate_existing().first()
            require_unprotected_ai(org, ai_user=user)
            source = models.DataSource.query.filter_by(id=body["data_source_id"], org_id=org_id).populate_existing().first()
            if (user is None or user.is_disabled or user.org_id != org_id or not user.has_permissions(BUILDER_PERMISSIONS)
                    or is_editor(user) != scoped or source is None or source.paused or source.type not in SUPPORTED_SOURCES
                    or not has_access(source, user, not_view_only)):
                abort(403, message="Your permissions or data-source access changed while the plan was being generated.")
            current = None
            if scoped:
                try:
                    current = source_snapshot(source, user)
                    if current["fingerprint"] != snapshot["fingerprint"]:
                        raise ValueError("AI data access changed while the plan was generated.")
                except ValueError as error:
                    abort(403, message=str(error))
            return user, source, current

        user, source, current = recheck()
        if "clarification" in plan:
            return {"clarification": "Please clarify the report using the tables and columns assigned to this data source."} if scoped else plan
        if scoped:
            try:
                for widget in plan["widgets"]:
                    validate_editor_sql(widget["sql"], current, widget["parameters"])
            except ValueError:
                abort(422, message="The generated SQL is outside your permitted data access.")
        from redash.handlers.queries import scope_query_options
        query_options = scope_query_options(source, user, {"parameters": [], "ai_generated": True,
                                                                "apply_auto_limit": False})
        try:
            with redis_connection.lock(f"ai:draft:{org_id}:{user_id}:{request_id}", timeout=20, blocking_timeout=5):
                user, source, current = recheck()
                existing = existing_draft()
                if existing:
                    return checked_draft(existing, current)
                dashboard = models.Dashboard(org_id=org_id, user=user, name=plan["title"], is_draft=True,
                                             layout=[], options={"ai_request_id": request_id,
                                                                 "ai_description": plan["description"],
                                                                 **({"ai_access_scope": current["fingerprint"]} if scoped else {})})
                models.db.session.add(dashboard)
                models.db.session.flush()
                layout = []
                for index, widget in enumerate(plan["widgets"]):
                    query = models.Query.create(org_id=org_id, user=user, data_source=source,
                                                name=widget["title"], query_text=widget["sql"],
                                                description="AI-generated draft. Review its business assumptions.",
                                                is_draft=True, options={**query_options, "parameters": widget["parameters"]})
                    models.db.session.add(query)
                    models.db.session.flush()
                    kind, options = visualization_options(widget)
                    visualization = models.Visualization(query_rel=query, type=kind, name=widget["title"], options=options)
                    models.db.session.add(visualization)
                    models.db.session.flush()
                    item = models.Widget(dashboard=dashboard, visualization=visualization, width=1,
                                         options={"parameterMappings": {p["name"]: {"name": p["name"],
                                                  "type": "dashboard-level", "mapTo": p["name"], "value": None,
                                                  "title": p["title"]} for p in widget["parameters"]},
                                                  "position": {"col": (index % 2) * 6, "row": (index // 2) * 8,
                                                               "sizeX": 6, "sizeY": 8}})
                    models.db.session.add(item)
                    models.db.session.flush()
                    if index % 2 == 0:
                        layout.append([])
                    layout[-1].append(item.id)
                dashboard.layout = layout
                models.db.session.commit()
        except LockError:
            models.db.session.rollback()
            abort(409, message="This dashboard is already being created. Retry the same request shortly.")
        except Exception:
            models.db.session.rollback()
            raise
        self.record_event({"action": "create", "object_type": "ai_dashboard", "object_id": dashboard.id,
                           "data_source_id": source.id, "visualizations": len(plan["widgets"])})
        return draft_response(dashboard)
