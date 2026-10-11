"""Turn a list of packages into CSV, JSON or plain text (no GTK needed)."""

from __future__ import annotations

import csv
import io
import json
import os
import time

EXPORT_FIELDS = ("name", "version", "source", "size_bytes", "installed_by_you", "updated",
                 "update_available", "summary", "homepage")


def export_rows(items) -> list[dict]:
    rows = []
    for item in items:
        pkg = item.pkg
        rows.append({
            "name": pkg.name, "version": pkg.version, "source": item.source.label,
            "size_bytes": pkg.size, "installed_by_you": pkg.explicit,
            "updated": time.strftime("%Y-%m-%d", time.localtime(pkg.installed))
            if pkg.installed else "",
            "update_available": pkg.update, "summary": pkg.summary, "homepage": pkg.homepage,
        })
    return rows


def render_export(items, path: str) -> str:
    rows = export_rows(items)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return json.dumps(rows, indent=2, ensure_ascii=False)
    if ext in (".txt", ".md"):
        return "".join(f"{r['name']}\t{r['version']}\t{r['source']}\n" for r in rows)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows({key: csv_safe(value) for key, value in row.items()} for row in rows)
    return out.getvalue()


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Stop spreadsheets from running a cell as a formula (CSV injection).

    Package summaries and names come from the packages themselves, so one starting with
    "=" could otherwise run as a formula when the file is opened in LibreOffice or Excel.
    """
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value
