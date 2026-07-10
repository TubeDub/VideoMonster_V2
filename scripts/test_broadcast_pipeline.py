"""Tests for Broadcast-grade pipeline."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.broadcast.exceptions import DataCorruptionException
from engines.broadcast.gatekeeper import PipelineGateKeeper
from engines.broadcast.masking import mask_text
from engines.broadcast.smart_restore import SmartRestore
from engines.broadcast.termbase import EntityKind, Termbase


class TestPipelineGateKeeper(unittest.TestCase):
    def test_assert_integrity_ok(self):
        orig = "Hello [##1##] world"
        proc = "Привет [##1##] мир"
        diag = PipelineGateKeeper.assert_integrity(orig, proc, stage="test")
        self.assertTrue(diag["ok"])

    def test_assert_integrity_missing_raises(self):
        orig = "Hello [##1##] and [##2##]"
        proc = "Hello [##1##]"
        os.environ["VM_BROADCAST_STRICT_GATE"] = "1"
        with self.assertRaises(DataCorruptionException):
            PipelineGateKeeper.assert_integrity(orig, proc, stage="test", allow_fuzzy=False)

    def test_validation_gate_fuzzy(self):
        orig = "Text [##5##] end"
        proc = "Text [## 5 ##] end"
        gate = PipelineGateKeeper.validation_gate(orig, proc, engine_id="test")
        self.assertFalse(gate.get("fatal", True))


class TestSmartRestore(unittest.TestCase):
    def test_fuzzy_token_fix(self):
        tb = Termbase()
        e = tb.register("George Jr.", EntityKind.PERSON, display="Джордж-младший")
        smart = SmartRestore()
        raw = f"Hello [## {e.term_id} ##] there"
        restored, incidents = smart.restore_tokens_in_text(
            raw,
            tb,
            engine="argos",
            original_masked=f"Hello [##{e.term_id}##] there",
        )
        self.assertIn("Джордж", restored)
        self.assertTrue(incidents or "Джордж" in restored)


class TestMasking(unittest.TestCase):
    def test_mask_locked_entity(self):
        tb = Termbase()
        tb.register("George Jr.", EntityKind.PERSON)
        mask = mask_text("Hello George Jr.", tb)
        self.assertIn("[##1##]", mask.masked_text)
        self.assertNotIn("George Jr.", mask.masked_text)


if __name__ == "__main__":
    unittest.main()
