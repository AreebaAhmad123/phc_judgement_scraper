"""Smoke tests for the HTML parser, using small synthetic table fragments
so we're not dependent on the live site being reachable to test locally."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.parser import parse_results_table, clean_date_to_iso


SINGLE_PDF_ROW_HTML = """
<table id="employee_list">
<tr>
  <td>1</td><td>Case Info A</td><td>Remarks A</td><td>awaited</td>
  <td>2025 PHC 1</td><td>24/12/2025</td><td></td><td>Civil</td>
  <td><a href="/PHCCMS//judgments/foo.pdf">Download</a></td>
</tr>
</table>
"""

TWO_PDF_ROW_HTML = """
<table id="employee_list">
<tr>
  <td>2</td><td>Case Info B</td><td>Remarks B</td><td>awaited</td>
  <td>2025 PHC 2</td><td>24/12/2025</td>
  <td>Upheld <a href="/PHCCMS//judgments/sc_bar.pdf">SC Judgment</a></td>
  <td>Criminal</td>
  <td><a href="/PHCCMS//judgments/bar.pdf">Download</a></td>
</tr>
</table>
"""

NO_RESULTS_HTML = "<html><body>No records found.</body></html>"


class TestParser(unittest.TestCase):
    def test_single_pdf_row(self):
        rows, _ = parse_results_table(SINGLE_PDF_ROW_HTML, 2025)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["id"], "PHC_2025_1")
        self.assertTrue(r["judgment_pdf_url"].endswith("foo.pdf"))
        self.assertIsNone(r["sc_judgment_pdf_url"])
        self.assertIsNone(r["sc_status"])

    def test_two_pdf_row_detects_sc_judgment(self):
        rows, _ = parse_results_table(TWO_PDF_ROW_HTML, 2025)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertTrue(r["judgment_pdf_url"].endswith("bar.pdf"))
        self.assertTrue(r["sc_judgment_pdf_url"].endswith("sc_bar.pdf"))
        self.assertEqual(r["sc_status"], "Upheld SC Judgment")

    def test_no_results(self):
        rows, total = parse_results_table(NO_RESULTS_HTML, 2025)
        self.assertEqual(rows, [])
        self.assertIsNone(total)

    def test_date_parsing(self):
        self.assertEqual(clean_date_to_iso("24/12/2025"), "2025-12-24")
        self.assertIsNone(clean_date_to_iso("Judgment awaited"))
        self.assertIsNone(clean_date_to_iso(""))


if __name__ == "__main__":
    unittest.main()
