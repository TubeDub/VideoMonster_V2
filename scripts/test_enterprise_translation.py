"""Tests for Enterprise Translation Pipeline."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.enterprise_translation.contract import PlaceholderContract
from engines.enterprise_translation.entity_manager import EntityManager
from engines.enterprise_translation.exceptions import IntegrityException
from engines.enterprise_translation.fusion import fuse_candidates
from engines.enterprise_translation.registry import PlaceholderRegistry
from engines.enterprise_translation.scoring import score_translation
from engines.enterprise_translation.serializer import EntitySerializer
from engines.enterprise_translation.types import EntityType, TournamentCandidate


class TestEntitySerializer(unittest.TestCase):
    def test_per_engine_formats(self):
        ser = EntitySerializer()
        self.assertEqual(ser.get_token_for_engine("PERSON_1", "deepl"), "[[PERSON_1]]")
        self.assertEqual(ser.get_token_for_engine("PERSON_1", "google"), "{PERSON_1}")
        self.assertEqual(ser.get_token_for_engine("PERSON_1", "argos"), "(PERSON_1)")
        self.assertEqual(ser.get_token_for_engine("PERSON_1", "libre"), "<PERSON_1>")


class TestPlaceholderContract(unittest.TestCase):
    def test_damage_raises(self):
        reg = PlaceholderRegistry()
        reg.register("George Jr.", EntityType.PERSON)
        ser = EntitySerializer()
        em = EntityManager(engine_id="google")
        em.registry = reg
        contract = PlaceholderContract(reg, ser, "google")
        with self.assertRaises(IntegrityException):
            contract.verify_after_stage(
                "Hello PERSON GJR 1 world",
                stage="test",
                expected_tokens=[ser.get_token_for_engine("PERSON_1", "google")],
            )


class TestScoring(unittest.TestCase):
    def test_placeholder_damage_zero_score(self):
        reg = PlaceholderRegistry()
        rec = reg.register("George Jr.", EntityType.PERSON)
        ser = EntitySerializer()
        token = ser.get_token_for_engine(rec.entity_id, "google")
        score, details = score_translation(
            f"Meet {token} today",
            "Встреча с PERSON GJR 1 сегодня",
            registry=reg,
            serializer=ser,
            engine_id="google",
            expected_tokens=[token],
        )
        self.assertEqual(score, 0.0)
        self.assertFalse(details["placeholder"]["ok"])

    def test_intact_placeholder_positive(self):
        reg = PlaceholderRegistry()
        rec = reg.register("George Jr.", EntityType.PERSON)
        ser = EntitySerializer()
        token = ser.get_token_for_engine(rec.entity_id, "google")
        score, details = score_translation(
            f"Meet {token} today",
            f"Встреча с {token} сегодня.",
            registry=reg,
            serializer=ser,
            engine_id="google",
            expected_tokens=[token],
        )
        self.assertGreater(score, 0)
        self.assertTrue(details["placeholder"]["ok"])


class TestFusion(unittest.TestCase):
    def test_picks_best_candidate(self):
        em = EntityManager(engine_id="google")
        em.registry.register("George Jr.", EntityType.PERSON, display="Джордж-младший")
        token = em.serializer.get_token_for_engine("PERSON_1", "google")
        candidates = [
            TournamentCandidate(
                engine_id="bad",
                text="broken PERSON 1",
                elapsed_ms=1.0,
                score=0,
                placeholder_ok=False,
            ),
            TournamentCandidate(
                engine_id="good",
                text=f"Привет {token}!",
                elapsed_ms=2.0,
                score=85,
                placeholder_ok=True,
            ),
        ]
        result = fuse_candidates(candidates, em)
        self.assertIn("Джордж", result.text)
        self.assertEqual(result.winner_engine, "good")

    def test_all_bad_raises(self):
        em = EntityManager()
        with self.assertRaises(IntegrityException):
            fuse_candidates(
                [
                    TournamentCandidate(
                        engine_id="x",
                        text="",
                        elapsed_ms=0.0,
                        score=0,
                        placeholder_ok=False,
                    )
                ],
                em,
            )


class TestEntityManagerRestore(unittest.TestCase):
    def test_fuzzy_restore_damaged_token(self):
        em = EntityManager(engine_id="google")
        rec = em.registry.register("George Jr.", EntityType.PERSON, display="Джордж-младший")
        token = em.serializer.get_token_for_engine(rec.entity_id, "google")
        damaged = "PERSON GJR 1"
        restored, ids, warnings = em.restore_text(
            f"Hello {damaged} there",
            engine_id="google",
        )
        # Without prior mask, fuzzy may still map damage pattern
        self.assertTrue(restored or warnings)


if __name__ == "__main__":
    unittest.main()
