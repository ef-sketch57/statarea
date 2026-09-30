"""Tests de la mesure de rentabilite.

Le test qui compte le plus est celui de la garantie d'anteriorite : utiliser
une cote relevee apres le coup d'envoi produirait des rendements imaginaires,
et le bug serait invisible dans les resultats.
"""

import random
import unittest

from statarea import db, value
from statarea.parser import Match


def _match(mid, p1, px, p2, hs, as_, prediction="1", date="2026-09-20", kickoff="18:00"):
    return Match(
        match_id=str(mid), sa_id=str(mid), match_date=date, kickoff=kickoff,
        country="T", competition="L", home=f"H{mid}", away=f"A{mid}",
        prediction=prediction, p1=p1, px=px, p2=p2,
        home_score=hs, away_score=as_, outcome=None, favorite=None,
        fav_margin=None, surprise=None, upset=None, prediction_hit=None,
    )


class TestOddsHelpers(unittest.TestCase):
    def test_double_chance_matches_implied_sum(self):
        o = {"o1": 2.10, "ox": 2.90, "o2": 3.60}
        dc = value.odds_for(o, "1X")
        self.assertAlmostEqual(1 / dc, 1 / 2.10 + 1 / 2.90, places=9)

    def test_missing_leg_gives_none(self):
        self.assertIsNone(value.odds_for({"o1": 2.0}, "1X"))

    def test_wins(self):
        self.assertTrue(value.wins("1X", "X"))
        self.assertFalse(value.wins("1X", "2"))
        self.assertTrue(value.wins("2", "2"))


class TestResult(unittest.TestCase):
    def test_roi_and_significance(self):
        r = value.Result("t", [1.0, -1.0, 1.0, -1.0])
        self.assertEqual(r.n, 4)
        self.assertEqual(r.hits, 2)
        self.assertEqual(r.roi, 0.0)
        self.assertFalse(r.significant)

    def test_empty(self):
        r = value.Result("t")
        self.assertIsNone(r.roi)
        self.assertIsNone(r.se)
        self.assertFalse(r.significant)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def _odds(self, mid, o1, ox, o2, fetched="2026-09-20T10:00:00Z"):
        return {"sa_id": str(mid), "fetched_at": fetched,
                "odds": {"o1": o1, "ox": ox, "o2": o2}}

    def test_fair_odds_give_zero_return(self):
        """Cote exactement juste : sur beaucoup de tirages, rendement nul."""
        rng = random.Random(3)
        matches, odds = [], []
        for i in range(3000):
            home_wins = rng.random() < 0.5
            matches.append(_match(i, 50, 25, 25, 1 if home_wins else 0, 0 if home_wins else 1))
            odds.append(self._odds(i, 2.0, 4.0, 4.0))   # 1 a 2.0 = 50 %, juste
        db.upsert_matches(self.conn, matches)
        db.upsert_odds(self.conn, odds)

        r = value.evaluate(self.conn)["statarea_simple"]
        self.assertEqual(r.n, 3000)
        self.assertAlmostEqual(r.roi, 0.0, delta=0.05)

    def test_bookmaker_margin_shows_as_negative_return(self):
        """Cote a 1.80 pour un evenement a 50 % : le parieur perd la marge."""
        rng = random.Random(5)
        matches, odds = [], []
        for i in range(3000):
            home_wins = rng.random() < 0.5
            matches.append(_match(i, 50, 25, 25, 1 if home_wins else 0, 0 if home_wins else 1))
            odds.append(self._odds(i, 1.80, 3.6, 3.6))
        db.upsert_matches(self.conn, matches)
        db.upsert_odds(self.conn, odds)

        r = value.evaluate(self.conn)["statarea_simple"]
        self.assertAlmostEqual(r.roi, -0.10, delta=0.05)   # 0.5*1.8 - 1 = -10 %

    def test_odds_fetched_after_kickoff_are_refused(self):
        """LA garantie : une cote posterieure au coup d'envoi ne doit jamais servir."""
        db.upsert_matches(self.conn, [_match(1, 50, 25, 25, 2, 0, kickoff="18:00")])
        db.upsert_odds(self.conn, [self._odds(1, 2.0, 4.0, 4.0,
                                              fetched="2026-09-20T19:30:00Z")])
        self.assertEqual(db.closing_odds(self.conn), {})
        self.assertEqual(value.evaluate(self.conn)["statarea_simple"].n, 0)

    def test_the_last_odds_before_kickoff_is_used(self):
        db.upsert_matches(self.conn, [_match(1, 50, 25, 25, 2, 0, kickoff="18:00")])
        db.upsert_odds(self.conn, [
            self._odds(1, 3.0, 4.0, 4.0, fetched="2026-09-20T08:00:00Z"),
            self._odds(1, 2.5, 4.0, 4.0, fetched="2026-09-20T17:00:00Z"),   # cloture
            self._odds(1, 1.1, 4.0, 4.0, fetched="2026-09-20T19:00:00Z"),   # trop tard
        ])
        self.assertEqual(db.closing_odds(self.conn)["1"]["o1"], 2.5)
        self.assertAlmostEqual(value.evaluate(self.conn)["statarea_simple"].returns[0], 1.5)

    def test_losing_bet_costs_one_unit(self):
        db.upsert_matches(self.conn, [_match(1, 50, 25, 25, 0, 2, prediction="1")])
        db.upsert_odds(self.conn, [self._odds(1, 2.0, 4.0, 4.0)])
        self.assertEqual(value.evaluate(self.conn)["statarea_simple"].returns, [-1.0])

    def test_doubles_are_counted_separately(self):
        db.upsert_matches(self.conn, [
            _match(1, 50, 25, 25, 2, 0, prediction="1"),
            _match(2, 50, 25, 25, 1, 1, prediction="1X"),
        ])
        db.upsert_odds(self.conn, [self._odds(1, 2.0, 4.0, 4.0), self._odds(2, 2.0, 4.0, 4.0)])
        r = value.evaluate(self.conn)
        self.assertEqual(r["statarea_simple"].n, 1)
        self.assertEqual(r["statarea_double"].n, 1)

    def test_controls_are_computed(self):
        db.upsert_matches(self.conn, [_match(1, 60, 25, 15, 2, 0, prediction="1")])
        db.upsert_odds(self.conn, [self._odds(1, 1.6, 4.0, 6.0)])
        r = value.evaluate(self.conn)
        self.assertEqual(r["favori"].n, 1)      # favori = domicile (p1 max)
        self.assertEqual(r["domicile"].n, 1)
        self.assertAlmostEqual(r["domicile"].returns[0], 0.6)

    def test_margin_is_measured(self):
        db.upsert_matches(self.conn, [_match(1, 50, 25, 25, 2, 0)])
        db.upsert_odds(self.conn, [self._odds(1, 1.8, 3.6, 3.6)])
        m = value.book_margin(self.conn)
        self.assertAlmostEqual(m, 1 / 1.8 + 2 / 3.6 - 1, places=6)


if __name__ == "__main__":
    unittest.main()


class TestCorrected(unittest.TestCase):
    def test_aligned_tip_is_kept(self):
        self.assertEqual(value.corrected_prediction("1", 55, 25, 20), "1")

    def test_contrary_tip_follows_percentages(self):
        self.assertEqual(value.corrected_prediction("X", 45, 30, 25), "1")
        self.assertEqual(value.corrected_prediction("2", 40, 28, 32), "1")

    def test_tie_at_top_keeps_the_tip(self):
        self.assertEqual(value.corrected_prediction("2", 40, 20, 40), "2")

    def test_doubles_are_not_corrected(self):
        self.assertIsNone(value.corrected_prediction("1X", 45, 30, 25))


class TestContrary(unittest.TestCase):
    def test_contrary_detection(self):
        self.assertTrue(value.is_contrary("X", 45, 30, 25))
        self.assertTrue(value.is_contrary("2", 40, 28, 32))
        self.assertFalse(value.is_contrary("1", 45, 30, 25))
        self.assertFalse(value.is_contrary("2", 40, 20, 40))   # egalite au sommet
        self.assertFalse(value.is_contrary("1X", 45, 30, 25))  # double chance

    def test_h2_only_counts_matches_from_its_start_date(self):
        conn = db.connect(":memory:")
        db.upsert_matches(conn, [
            _match(1, 45, 30, 25, 1, 1, prediction="X", date="2026-09-30"),
            _match(2, 45, 30, 25, 1, 1, prediction="X", date="2026-10-01"),
        ])
        db.upsert_odds(conn, [
            {"sa_id": "1", "fetched_at": "2026-09-30T10:00:00Z", "odds": {"o1": 2.0, "ox": 3.3, "o2": 4.0}},
            {"sa_id": "2", "fetched_at": "2026-10-01T10:00:00Z", "odds": {"o1": 2.0, "ox": 3.3, "o2": 4.0}},
        ])
        r = value.evaluate(conn)
        self.assertEqual(r["contraires"].n, 2)
        self.assertEqual(r["contraires_hors_echantillon"].n, 1)
        self.assertAlmostEqual(r["contraires_hors_echantillon"].returns[0], 2.3)
