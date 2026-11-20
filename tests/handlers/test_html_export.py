"""HTML downloads use the same dashboard/result and role authorization as other formats."""
import unittest
from redash import models
from redash.access_policy import policy_for
from tests.handlers import test_dashboard_access as dashboard_access

class TestHtmlResultExport(unittest.TestCase):
    def setUp(self):
        self.fixture = dashboard_access.TestDashboardAccess('test_default_deny_does_not_implicitly_share_old_published_dashboards')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def download(self, actor='reader'):
        c = self.fixture
        return c.make_request('get', f"/api/queries/{c.ids['cached_query']}/results/{c.ids['cached_result']}.html", user=c.user(actor))

    def test_authorized_reader_html_is_attachment_without_sql(self):
        response = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.content_type)
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertIn('.html', response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertIn("default-src &apos;none&apos;", response.get_data(as_text=True))
        self.assertIn('<td>42</td>', response.get_data(as_text=True))
        self.assertNotIn('SELECT 42', response.get_data(as_text=True))

    def test_revocation_and_foreign_users_cannot_download_html(self):
        self.assertEqual(self.download('unassigned').status_code, 403)
        self.assertNotEqual(self.download('foreign').status_code, 200)
        self.fixture.revoke()
        self.assertEqual(self.download().status_code, 403)

    def test_disabled_download_policy_blocks_html(self):
        c = self.fixture
        org = models.Organization.get_by_id(c.org_id)
        policy = policy_for(org)
        policy['reader']['allow_download'] = False
        org.settings['access_policy'] = policy
        models.db.session.commit()
        self.assertEqual(self.download().status_code, 403)
