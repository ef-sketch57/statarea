"""Tests de la logique derivee et de la regle de l'upsert."""

import sqlite3
import unittest

from statarea import db
from statarea.parser import (
    Match, compute_favorite, compute_flags, compute_outcome, compute_prediction_hit,
)


class TestOutcome(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(compute_outcome(2, 0), "1")
        self.assertEqual(compute_outcome(1, 1), "X")
        self.assertEqual(compute_outcome(0, 2), "2")
        self.assertIsNone(compute_outcome(None, 2))
        self.assertIsNone(compute_outcome(2, None))


class TestFavorite(unittest.TestCase):
    def test_home_favorite(self):
        # Venezia - Lazio : 49% sur le 2 -> favori exterieur, marge 49-28=21
        self.assertEqual(compute_favorite(28, 23, 49), ("away", 21))

    def test_away_favorite(self):
        self.assertEqual(compute_favorite(55, 25, 20), ("home", 30))

    def test_draw_favorite_is_none(self):
        """X en tete : aucun favori, le match est exclu des indices."""
        self.assertEqual(compute_favorite(28, 45, 27), (None, None))

    def test_tie_at_top_is_none(self):
        """Egalite au sommet : designer un favori serait arbitraire."""
        self.assertEqual(compute_favorite(34, 34, 32), (None, None))
        self.assertEqual(compute_favorite(40, 20, 40), (None, None))

    def test_margin_distinguishes_clear_from_balanced(self):
        self.assertEqual(compute_favorite(49, 28, 23)[1], 21)  # favori net
        self.assertEqual(compute_favorite(38, 33, 29)[1], 5)   # quasi equilibre

    def test_missing_percentages(self):
        self.assertEqual(compute_favorite(None, 30, 40), (None, None))


class TestFlags(unittest.TestCase):
    def test_home_favorite_confirms(self):
        self.assertEqual(compute_flags("home", "1"), (0, 0))

    def test_home_favorite_draws(self):
        """Nul : le favori n'a pas gagne (surprise) mais l'outsider non plus (pas d'upset)."""
        self.assertEqual(compute_flags("home", "X"), (1, 0))

    def test_home_favorite_loses(self):
        self.assertEqual(compute_flags("home", "2"), (1, 1))

    def test_away_favorite(self):
        self.assertEqual(compute_flags("away", "2"), (0, 0))
        self.assertEqual(compute_flags("away", "X"), (1, 0))
        self.assertEqual(compute_flags("away", "1"), (1, 1))

    def test_no_favorite(self):
        self.assertEqual(compute_flags(None, "1"), (None, None))

    def test_not_played(self):
        self.assertEqual(compute_flags("home", None), (None, None))


class TestPredictionHit(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(compute_prediction_hit("1", "1"), 1)
        self.assertEqual(compute_prediction_hit("1", "X"), 0)

    def test_double_chance(self):
        self.assertEqual(compute_prediction_hit("1X", "X"), 1)
        self.assertEqual(compute_prediction_hit("1X", "2"), 0)
        self.assertEqual(compute_prediction_hit("X2", "2"), 1)
        self.assertEqual(compute_prediction_hit("12", "X"), 0)

    def test_unknown(self):
        self.assertIsNone(compute_prediction_hit(None, "1"))
        self.assertIsNone(compute_prediction_hit("1", None))


def _match(**kw) -> Match:
    base = dict(
        match_id="2026-09-19|Roma|Inter", sa_id="1", match_date="2026-09-19",
        kickoff="12:00", country="Italy", competition="SERIE A",
        home="Roma", away="Inter", prediction="X2", p1=34, px=34, p2=32,
        home_score=None, away_score=None, outcome=None, favorite=None,
        fav_margin=None, surprise=None, upset=None, prediction_hit=None,
    )
    base.update(kw)
    return Match(**base)


class TestUpsert(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_never_overwrites_a_known_value_with_null(self):
        """Le coeur du besoin : on re-scrape la meme date plusieurs fois."""
        # 1er passage, apres le match : score connu.
        db.upsert_matches(self.conn, [_match(
            home_score=2, away_score=2, outcome="X", prediction_hit=1)])
        # 2e passage sur une page ou le score a disparu.
        db.upsert_matches(self.conn, [_match(
            home_score=None, away_score=None, outcome=None, prediction_hit=None)])

        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertEqual(row["home_score"], 2)
        self.assertEqual(row["away_score"], 2)
        self.assertEqual(row["outcome"], "X")
        self.assertEqual(row["prediction_hit"], 1)

    def test_fills_in_missing_values(self):
        db.upsert_matches(self.conn, [_match()])
        db.upsert_matches(self.conn, [_match(
            home_score=1, away_score=0, outcome="1", favorite="home",
            fav_margin=10, surprise=0, upset=0)])

        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertEqual(row["outcome"], "1")
        self.assertEqual(row["favorite"], "home")
        self.assertEqual(row["fav_margin"], 10)

    def test_updates_a_changed_value(self):
        """Une valeur non NULLE ecrase bien l'ancienne (correction de score)."""
        db.upsert_matches(self.conn, [_match(home_score=1, away_score=0, outcome="1")])
        db.upsert_matches(self.conn, [_match(home_score=2, away_score=0, outcome="1")])
        self.assertEqual(self.conn.execute("SELECT home_score FROM matches").fetchone()[0], 2)

    def test_counts(self):
        self.assertEqual(db.upsert_matches(self.conn, [_match()]), (1, 0))
        self.assertEqual(db.upsert_matches(self.conn, [_match()]), (0, 1))

    def test_empty(self):
        self.assertEqual(db.upsert_matches(self.conn, []), (0, 0))


if __name__ == "__main__":
    unittest.main()
