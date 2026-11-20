"""Standalone, inert HTML reports; only explicitly supplied result columns/rows."""
import json
from html import escape


def render_html_report(data, title="Bi-Dash report", retrieved_at=None):
    def cell(value):
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        return escape(str(value), quote=True)

    columns = [column["name"] for column in data.get("columns", [])]
    heading = cell(title)
    headers = "".join('<th scope="col">' + cell(name) + '</th>' for name in columns)
    rows = "".join('<tr>' + "".join('<td>' + cell(row.get(name)) + '</td>' for name in columns) + '</tr>'
                   for row in data.get("rows", []))
    if not rows:
        rows = '<tr><td colspan="' + str(max(1, len(columns))) + '">No rows</td></tr>'
    timestamp = '<p>Retrieved: ' + cell(retrieved_at) + '</p>' if retrieved_at else ''
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; style-src &apos;unsafe-inline&apos;; base-uri &apos;none&apos;; form-action &apos;none&apos;">'
            '<title>' + heading + '</title><style>'
            'body{font:15px/1.5 system-ui,sans-serif;color:#202938;margin:32px}'
            'h1{font-size:26px}p{color:#596579}.table-scroll{overflow-x:auto}'
            'table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9e0e8;padding:10px;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}'
            'th{background:#edf3fb}tr:nth-child(even){background:#f8fafc}'
            '@media print{body{margin:0}thead{display:table-header-group}tr{break-inside:avoid}}'
            '</style></head><body><h1>' + heading + '</h1>' + timestamp +
            '<div class="table-scroll"><table><thead><tr>' + headers + '</tr></thead><tbody>' + rows +
            '</tbody></table></div><p>Exported from Bi-Dash</p></body></html>')
