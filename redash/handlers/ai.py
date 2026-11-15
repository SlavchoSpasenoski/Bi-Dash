# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
import os

from flask import request, session
from flask_restful import abort

from redash import limiter, settings
from redash.ai_assistant import AssistantError, AssistantTimeoutError, ask_assistant, validate_messages
from redash.ai_settings import CONTEXT_LIMIT, public_configuration, runtime_configuration, update_configuration
from redash.handlers.base import BaseResource
from redash.models import db
from redash.permissions import require_admin, require_permission
from redash.security import csrf
from redash.ai_access import (available_sources, editor_chat_answer, is_editor,
                              require_ai_user, source_snapshot)


def json_body(limit=160000):
    if "_user_id" in session or "user_id" in session:
        csrf.protect()
    if request.content_length and request.content_length > limit:
        abort(413, message="Request is too large.")
    body = request.get_json()
    if not isinstance(body, dict):
        abort(400, message="Expected a JSON object.")
    return body


def ai_configuration(org):
    try:
        return runtime_configuration(org.settings.get("ai_configuration", {}), settings.DATASOURCE_SECRET_KEY)
    except ValueError as error:
        abort(503, message=str(error))


def require_unprotected_ai(org, ai_user=None):
    # Compatibility name retained for the existing routes. Editors must use
    # source_snapshot before provider access; shared context stays Admin-only.
    try:
        require_ai_user(org, ai_user)
    except ValueError as error:
        abort(403, message=str(error))


class AssistantSettingsResource(BaseResource):
    @require_admin
    def get(self):
        return public_configuration(self.current_org.settings.get("ai_configuration", {}))

    @require_admin
    def post(self):
        body = json_body(32000)
        try:
            configuration = update_configuration(
                self.current_org.settings.get("ai_configuration", {}), body, settings.DATASOURCE_SECRET_KEY)
        except ValueError as error:
            abort(400, message=str(error))
        self.current_org.settings["ai_configuration"] = configuration
        db.session.add(self.current_org)
        db.session.commit()
        self.record_event({"action": "edit", "object_type": "ai_configuration", "object_id": self.current_org.id})
        return public_configuration(configuration)


class AssistantContextResource(BaseResource):
    @require_admin
    def get(self):
        return {"context": self.current_org.settings.get("ai_context", "")}

    @require_admin
    def post(self):
        body = json_body()
        context = body.get("context")
        if not isinstance(context, str) or len(context) > CONTEXT_LIMIT:
            abort(400, message=f"Context must be text of at most {CONTEXT_LIMIT} characters.")
        self.current_org.settings["ai_context"] = context
        db.session.add(self.current_org)
        db.session.commit()
        self.record_event({"action": "edit", "object_type": "ai_context", "object_id": self.current_org.id})
        return {"context": context}


class AssistantResource(BaseResource):
    @require_permission("use_ai")
    def get(self):
        require_unprotected_ai(self.current_org, ai_user=self.current_user)
        configuration = ai_configuration(self.current_org)
        scoped = is_editor(self.current_user)
        return {key: configuration[key] for key in ("provider", "model", "enabled", "timeout_seconds")} | {
            "has_context": not scoped and bool(self.current_org.settings.get("ai_context")),
            "destination": configuration["base_url"],
            "scoped": scoped, "requires_data_source": scoped,
            "data_sources": [{"id": source.id, "name": source.name, "type": source.type}
                             for source in available_sources(self.current_org, self.current_user)] if scoped else [],
        }

    @require_permission("use_ai")
    @limiter.limit("10/minute")
    def post(self):
        require_unprotected_ai(self.current_org, ai_user=self.current_user)
        body = json_body()
        scoped = is_editor(self.current_user)
        configuration = ai_configuration(self.current_org)
        if not configuration["enabled"]:
            abort(503, message="Configure the AI provider and model in Settings → AI Configuration.")
        try:
            messages = validate_messages(body.get("messages"))
        except ValueError as error:
            abort(400, message=str(error))
        schema = body.get("schema", "")
        if not isinstance(schema, str) or len(schema) > 24000:
            abort(400, message="Schema must be text of at most 24000 characters.")
        snapshot, source_id = None, None
        org_id, user_id = self.current_org.id, self.current_user.id
        if scoped:
            from redash import models
            from redash.ai_dashboard import reference_schema
            source_id = body.get("data_source_id")
            if type(source_id) is not int:
                abort(400, message="Select a data source for Editor AI.")
            source = models.DataSource.query.filter_by(id=source_id, org_id=org_id).first()
            if source is None:
                abort(403, message="Choose an accessible MySQL data source.")
            try:
                snapshot = source_snapshot(source, self.current_user)
                schema = reference_schema(snapshot["schema"], None, "mysql")
            except ValueError as error:
                abort(403, message=str(error))
            messages = [messages[-1]]
            schema += ('\nEditor SQL response: return exactly one JSON object {"sql":"SELECT ..."} '
                       'or {"clarification":"..."}, without explanation or other fields. '
                       'Use only the assigned names above; no parameters, comments or commands.')
        # Shared administrator context is never scoped by substring filtering.
        context = "" if scoped else self.current_org.settings.get("ai_context", "")
        db.session.close()
        try:
            answer = ask_assistant(
                configuration["api_key"], configuration["model"], messages, schema,
                provider=configuration["provider"], base_url=configuration["base_url"],
                context=context,
                ollama_timeout=configuration["timeout_seconds"],
                request_timeout=configuration["timeout_seconds"],
                ollama_keep_alive=os.environ.get("REDASH_OLLAMA_KEEP_ALIVE", "30m"),
            )
            from redash import models
            db.session.expire_all()
            user = models.User.query.filter_by(id=user_id).populate_existing().first()
            org = models.Organization.query.filter_by(id=org_id).populate_existing().first()
            try:
                require_ai_user(org, user)
                if is_editor(user) != scoped:
                    raise ValueError("AI access changed while the response was generated.")
            except ValueError as error:
                abort(403, message=str(error))
            if scoped:
                source = models.DataSource.query.filter_by(id=source_id, org_id=org_id).populate_existing().first()
                try:
                    if source is None:
                        raise ValueError("AI access changed while the response was generated.")
                    current = source_snapshot(source, user)
                    if current["fingerprint"] != snapshot["fingerprint"]:
                        raise ValueError("AI access changed while the response was generated.")
                except ValueError as error:
                    abort(403, message=str(error))
                try:
                    answer = editor_chat_answer(answer, current)
                except ValueError:
                    abort(422, message="The generated SQL or response is outside your permitted data access.")
            return {"answer": answer}
        except AssistantTimeoutError as error:
            abort(504, message=str(error))
        except AssistantError as error:
            abort(502, message=str(error))
