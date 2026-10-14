"""task-0004, FR-5 ui_evidence.safe(): a non-ASCII UI label in the evidence must
not fail the stage on a cp1252 console.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import io
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import ui_evidence


class UiEvidenceEncodingTest(unittest.TestCase):
    """FR-5 (ui_evidence row): the gate message quotes the evidence file, so it
    carries whatever labels the UI uses. A non-ASCII label used to raise
    UnicodeEncodeError from print() on a cp1252 console and fail the stage for a
    reason unrelated to the evidence."""

    def test_a_non_ascii_label_is_replaced_not_carried(self):
        # chr() escapes rather than literals, so this file stays plain ASCII.
        label = chr(0x041a) + chr(0x043d) + chr(0x043e) + chr(0x043f)  # Cyrillic
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = "cp1252"
            out = ui_evidence.safe(f"UI verified on the {label} screen")
        self.assertIn("UI verified on the", out)
        self.assertIn("screen", out)
        # The point of the function: what cp1252 cannot carry is GONE, and a
        # replacement marker is there instead. Asserting a length here is what
        # made the previous version of this test pass with safe() replaced by
        # the identity function - '????' is as long as the label it replaced.
        self.assertNotIn(label, out)
        for ch in label:
            self.assertNotIn(ch, out)
        self.assertIn("?", out)

    def test_a_real_cp1252_stream_takes_the_message_print_cannot(self):
        # The defect this function exists for, against a live stream rather than
        # a mock: the raw message raises, the sanitised one writes.
        label = chr(0x041a) + chr(0x043d)
        message = f"UI verified on the {label} screen"

        raw = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with unittest.mock.patch.object(sys, "stdout", raw), \
                self.assertRaises(UnicodeEncodeError):
            print(message)
            raw.flush()

        clean = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with unittest.mock.patch.object(sys, "stdout", clean):
            print(ui_evidence.safe(message))
            clean.flush()
        written = clean.buffer.getvalue()
        self.assertIn(b"UI verified on the", written)
        self.assertIn(b"?", written)
        self.assertNotIn(label.encode("utf-8"), written)

    def test_an_ascii_message_is_unchanged(self):
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = "cp1252"
            self.assertEqual("2 routes, none failing",
                             ui_evidence.safe("2 routes, none failing"))

    def test_a_utf8_console_keeps_the_label(self):
        label = chr(0x041a) + chr(0x043d)
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = "utf-8"
            self.assertIn(label, ui_evidence.safe(f"screen {label}"))

    def test_no_encoding_at_all_does_not_raise(self):
        with unittest.mock.patch.object(sys, "stdout") as fake:
            fake.encoding = None
            self.assertEqual("plain", ui_evidence.safe("plain"))


if __name__ == "__main__":
    unittest.main()
