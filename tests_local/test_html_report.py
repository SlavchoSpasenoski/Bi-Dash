import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('html_report', Path(__file__).parents[1] / 'redash/html_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

class HtmlReportTests(unittest.TestCase):
    def test_untrusted_content_is_text_and_only_declared_columns_are_exported(self):
        html = report.render_html_report({'columns': [{'name': '<script>'}], 'rows': [
            {'<script>': '<img src=x onerror=alert(1)>', 'private': 'HIDDEN'}]}, title='</title><script>alert(1)</script>')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('HIDDEN', html)
        self.assertIn('&lt;img', html)
        self.assertIn('Content-Security-Policy', html)

    def test_unicode_null_nested_and_empty_values(self):
        html = report.render_html_report({'columns': [{'name': 'Име'}, {'name': 'empty'}],
            'rows': [{'Име': {'текст': '<value>'}, 'empty': None}]})
        self.assertIn('Име', html)
        self.assertIn('&lt;value&gt;', html)
        self.assertIn('<td></td>', html)
        self.assertIn('No rows', report.render_html_report({'columns': [], 'rows': []}))
