import csv
import io
import json
import unittest
from types import SimpleNamespace

from urpm.export import EXPORT_FIELDS, render_export
from urpm.sources.base import Package


def item(name, **kw):
    return SimpleNamespace(pkg=Package(name, "1.0", "apt", **kw),
                           source=SimpleNamespace(label="APT"))


class ExportTests(unittest.TestCase):
    ITEMS = [item("7zip", summary='Archiver, with "quotes"', size=10, installed=1700000000.0),
             item("zstd", explicit=False, update="1.1")]

    def test_csv(self):
        rows = list(csv.DictReader(io.StringIO(render_export(self.ITEMS, "out.csv"))))
        self.assertEqual(tuple(rows[0].keys()), EXPORT_FIELDS)
        self.assertEqual(rows[0]["summary"], 'Archiver, with "quotes"')
        self.assertEqual(rows[1]["update_available"], "1.1")
        self.assertEqual(rows[1]["installed_by_you"], "False")

    def test_csv_formulas_are_neutralised(self):
        """S7: a summary like '=HYPERLINK(...)' must stay text in spreadsheets."""
        items = [item("evil", summary='=HYPERLINK("http://x","click")'),
                 item("@sum", summary="+1+1"), item("ok", summary="-just a dash")]
        rows = list(csv.DictReader(io.StringIO(render_export(items, "out.csv"))))
        self.assertEqual(rows[0]["summary"], '\'=HYPERLINK("http://x","click")')
        self.assertEqual(rows[1]["name"], "'@sum")
        self.assertEqual(rows[1]["summary"], "'+1+1")
        self.assertEqual(rows[2]["summary"], "'-just a dash")
        # JSON keeps the exact text: it's data, not a spreadsheet.
        self.assertEqual(json.loads(render_export(items, "x.json"))[0]["summary"][0], "=")

    def test_json_and_text(self):
        data = json.loads(render_export(self.ITEMS, "OUT.JSON"))
        self.assertEqual([r["name"] for r in data], ["7zip", "zstd"])
        self.assertEqual(render_export(self.ITEMS, "list.txt"),
                         "7zip\t1.0\tAPT\nzstd\t1.0\tAPT\n")


if __name__ == "__main__":
    unittest.main()
