"""Admin browser-only portable import/export; preview is mandatory and bound."""
import hashlib
import hmac
from uuid import UUID
from flask import request, session
from flask_restful import abort
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.exc import IntegrityError
from redash import limiter, models, settings
from redash.handlers.base import BaseResource, json_response
from redash.migration_package import canonical, seal, unseal, MAX_FILE_BYTES
from redash.security import csrf
from redash.data_access_runtime import is_internal


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _target_state(org):
    """Opaque HMAC; never serialize source options/settings into a preview token."""
    state = {"settings": org.settings,
        "users": [(u.id, u.name, u.email, u.role, u.is_disabled, u.group_ids) for u in models.User.query.filter_by(org_id=org.id).order_by(models.User.id)],
        "groups": [(g.id, g.name, g.permissions) for g in models.Group.query.filter_by(org_id=org.id).order_by(models.Group.id)],
        "sources": [(s.id, s.name, s.type, s.options.to_dict()) for s in models.DataSource.query.filter_by(org_id=org.id).order_by(models.DataSource.id) if not is_internal(s)],
        "queries": [(q.id, q.name, q.version) for q in models.Query.query.filter_by(org_id=org.id).order_by(models.Query.id)],
        "dashboards": [(d.id, d.name, d.version) for d in models.Dashboard.query.filter_by(org_id=org.id).order_by(models.Dashboard.id)],
        "bindings": [(b.data_source_id, b.group_id, b.view_only) for b in models.DataSourceGroup.query.join(models.DataSource).filter(models.DataSource.org_id == org.id).order_by(models.DataSourceGroup.data_source_id, models.DataSourceGroup.group_id)],
        "imports": models.MigrationRun.query.filter_by(org_id=org.id).count()}
    return hmac.new(str(settings.SECRET_KEY).encode(), canonical(state), hashlib.sha256).hexdigest()


class MigrationBase(BaseResource):
    def actor(self):
        user = self.current_user
        if (user.is_api_user() or user.is_disabled or user.org_id != self.current_org.id or user.role != "admin"
                or not user.has_permission("admin") or session.get('_user_id') != user.get_id()
                or 'api_key' in request.args or request.headers.get('Authorization')):
            abort(403)
        csrf.protect()
        # Serialize migration commits/receipts and settings changes using the org row.
        org = models.Organization.query.filter_by(id=self.current_org.id).populate_existing().with_for_update().one()
        models.db.session.refresh(user)
        if user.is_disabled or user.role != "admin" or user.org_id != org.id or not user.has_permission("admin"):
            abort(403)
        return user, org

    def body(self):
        if request.content_length is None or request.content_length > MAX_FILE_BYTES + 1048576:
            abort(413, message="Migration request exceeds the upload limit.")
        try:
            data = request.get_json(force=True)
        except (ValueError, RecursionError):
            abort(400, message="Invalid migration request.")
        if not isinstance(data, dict):
            abort(400, message="Expected a JSON object.")
        return data

    def response(self, data):
        response = json_response(data)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Pragma'] = 'no-cache'
        return response

    def content(self, data):
        content = unseal(data.get('package'), data.get('passphrase'))
        try:
            value = content['package_id']
            if str(UUID(value)) != value:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("Invalid package identifier.") from None
        mapping = data.get('source_mapping', {})
        if not isinstance(mapping, dict) or len(mapping) > 1000:
            raise ValueError("Invalid source mapping.")
        return content, mapping

    def binding(self, data, user, org):
        return {"org": org.id, "actor": user.id, "package": _digest(data.get('package')),
                "mapping": _digest(data.get('source_mapping', {})), "target": _target_state(org)}

    def signer(self):
        return URLSafeTimedSerializer(settings.SECRET_KEY, salt='bi-dash-content-import-preview-v1')


class MigrationExportResource(MigrationBase):
    @limiter.limit('3/minute')
    def post(self):
        user, org = self.actor()
        data = self.body()
        try:
            from redash.content_migration import export_content
            credentials = data.get('include_credentials', False)
            if not isinstance(credentials, bool):
                raise ValueError("Invalid credentials selection.")
            content = export_content(org, user, data.get('components', []), credentials)
            envelope = seal(content, data.get('passphrase'))
            models.db.session.rollback()
            return self.response({"filename": 'bi-dash-' + content['package_id'] + '.bidash', "package": envelope})
        except (ValueError, RecursionError) as error:
            models.db.session.rollback()
            abort(400, message=str(error) if isinstance(error, ValueError) else "Invalid package content.")


class MigrationInspectResource(MigrationBase):
    @limiter.limit('8/minute')
    def post(self):
        user, org = self.actor()
        data = self.body()
        try:
            from redash.content_migration import inspect_content
            content, mapping = self.content(data)
            summary = inspect_content(content, org, user, mapping)
            summary['sources'] = summary.get('source_requirements', [])
            summary['target_sources'] = [{"id": s.id, "name": s.name, "type": s.type} for s in models.DataSource.query.filter_by(org_id=org.id).order_by(models.DataSource.id) if not is_internal(s)]
            summary['owners'] = 'imported_users' if 'users' in summary.get('components', []) else 'importing_admin'
            if models.MigrationRun.query.filter_by(org_id=org.id, package_id=content['package_id']).first():
                summary.setdefault('errors', []).append("This package has already been imported into this organization.")
            if not summary.get('errors'):
                summary['preview_token'] = self.signer().dumps(self.binding(data, user, org))
            models.db.session.rollback()
            return self.response(summary)
        except (ValueError, RecursionError) as error:
            models.db.session.rollback()
            abort(400, message=str(error) if isinstance(error, ValueError) else "Invalid package content.")


class MigrationImportResource(MigrationBase):
    @limiter.limit('3/minute')
    def post(self):
        user, org = self.actor()
        data = self.body()
        try:
            from redash.content_migration import inspect_content, import_content
            content, mapping = self.content(data)
            token = data.get('preview_token')
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError("Preview the package before importing.")
            try:
                binding = self.signer().loads(token, max_age=1200)
            except (BadSignature, SignatureExpired):
                raise ValueError("Preview expired or invalid. Inspect the package again.") from None
            if binding != self.binding(data, user, org):
                abort(409, message="Package, mapping or destination changed. Inspect the package again.")
            if models.MigrationRun.query.filter_by(org_id=org.id, package_id=content['package_id']).first():
                abort(409, message="This package has already been imported.")
            summary = inspect_content(content, org, user, mapping)
            if summary.get('errors'):
                raise ValueError("Import dependencies are unresolved. Inspect the package again.")
            result = import_content(content, org, user, mapping)
            models.db.session.add(models.MigrationRun(org_id=org.id, actor_user_id=user.id, package_id=content['package_id']))
            models.db.session.commit()
        except (ValueError, RecursionError) as error:
            models.db.session.rollback()
            abort(400, message=str(error) if isinstance(error, ValueError) else "Invalid package content.")
        except IntegrityError:
            models.db.session.rollback()
            abort(409, message="Import conflicts with current destination content. No changes were saved.")
        warnings = list(result.get('warnings', []))
        for ident in result.pop('created_source_ids', []):
            try:
                source = models.DataSource.query.filter_by(id=ident, org_id=org.id).one()
                source.pause('Imported source: review connection and permissions before use.')
            except Exception:
                warnings.append('An imported source could not be paused. It retains isolated permissions and disabled schedules.')
        result['warnings'] = warnings
        return self.response(result)
