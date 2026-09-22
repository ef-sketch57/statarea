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

    def test_freezes_the_pre_match_prediction(self):
        """Une revision post-match des pourcentages ne doit pas remonter en base."""
        db.upsert_matches(self.conn, [_match(prediction="1", p1=60, px=25, p2=15)])
        # Re-scrape apres le match, avec des pourcentages differents.
        db.upsert_matches(self.conn, [_match(
            prediction="2", p1=20, px=25, p2=55, home_score=0, away_score=2)])

        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertEqual(row["prediction"], "1")   # le pronostic d'origine
        self.assertEqual((row["p1"], row["px"], row["p2"]), (60, 25, 15))
        self.assertEqual(row["favorite"], "home")  # derive des pourcentages d'origine
        self.assertEqual(row["outcome"], "2")      # mais le score, lui, est bien arrive
        self.assertEqual(row["surprise"], 1)
        self.assertEqual(row["upset"], 1)
        self.assertEqual(row["prediction_hit"], 0)

    def test_derived_columns_stay_consistent(self):
        """Les colonnes derivees sont recalculees, jamais laissees en desaccord."""
        db.upsert_matches(self.conn, [_match(p1=49, px=28, p2=23)])
        db.upsert_matches(self.conn, [_match(home_score=1, away_score=0)])
        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertEqual(row["favorite"], "home")
        self.assertEqual(row["fav_margin"], 21)
        self.assertEqual(row["outcome"], "1")
        self.assertEqual(row["surprise"], 0)

    def test_sql_and_python_rules_agree(self):
        """Le SQL de recompute doit dire exactement la meme chose que le parser."""
        cases = [(49, 28, 23), (28, 45, 27), (34, 34, 32), (40, 20, 40),
                 (55, 25, 20), (23, 28, 49), (38, 33, 29), (30, 32, 38)]
        scores = [(2, 0), (1, 1), (0, 3)]
        rows = []
        for i, (p1, px, p2) in enumerate(cases):
            for j, (hs, a_s) in enumerate(scores):
                rows.append(_match(match_id=f"m{i}-{j}", home=f"H{i}", away=f"A{j}",
                                   p1=p1, px=px, p2=p2, prediction="1X",
                                   home_score=hs, away_score=a_s))
        db.upsert_matches(self.conn, rows)

        for r in self.conn.execute("SELECT * FROM matches"):
            fav, margin = compute_favorite(r["p1"], r["px"], r["p2"])
            outcome = compute_outcome(r["home_score"], r["away_score"])
            surprise, upset = compute_flags(fav, outcome)
            self.assertEqual(r["favorite"], fav, r["match_id"])
            self.assertEqual(r["fav_margin"], margin, r["match_id"])
            self.assertEqual(r["outcome"], outcome, r["match_id"])
            self.assertEqual(r["surprise"], surprise, r["match_id"])
            self.assertEqual(r["upset"], upset, r["match_id"])
            self.assertEqual(r["prediction_hit"],
                             compute_prediction_hit(r["prediction"], outcome), r["match_id"])

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
        """Le score arrive au 2e passage et les colonnes derivees suivent."""
        db.upsert_matches(self.conn, [_match(p1=52, px=28, p2=20)])
        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertIsNone(row["outcome"])
        self.assertEqual(row["favorite"], "home")   # deja connu sans le score
        self.assertIsNone(row["surprise"])

        db.upsert_matches(self.conn, [_match(p1=52, px=28, p2=20,
                                             home_score=1, away_score=0)])
        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertEqual(row["outcome"], "1")
        self.assertEqual(row["favorite"], "home")
        self.assertEqual(row["fav_margin"], 24)
        self.assertEqual(row["surprise"], 0)

    def test_derived_columns_ignore_what_the_caller_passes(self):
        """Les colonnes derivees viennent des regles, pas de l'appelant."""
        db.upsert_matches(self.conn, [_match(
            p1=52, px=28, p2=20, home_score=0, away_score=3,
            favorite="away", fav_margin=99, surprise=0, upset=0, outcome="X")])
        row = self.conn.execute("SELECT * FROM matches").fetchone()
        self.assertEqual(row["favorite"], "home")
        self.assertEqual(row["fav_margin"], 24)
        self.assertEqual(row["outcome"], "2")
        self.assertEqual(row["surprise"], 1)
        self.assertEqual(row["upset"], 1)

    def test_corrects_a_changed_score(self):
        """Un score corrige par statarea doit bien remonter en base."""
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
