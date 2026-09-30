"""Tests de la collecte des cotes."""

import tempfile
import unittest
from pathlib import Path

from statarea import db, odds

REPONSE = """
<div class='sublabel'><div class='label'>Latest bet coefficients</div></div>
<div class='betinfoblock'><div class='header'>Full time 1 X 2</div>
  <div class='betitem'><div class='value'>2.10</div><div class='label'>1</div></div>
  <div class='betitem'><div class='value'>2.90</div><div class='label'>X</div></div>
  <div class='betitem'><div class='value'>3.60</div><div class='label'>2</div></div>
</div>
<div class='betinfoblock'><div class='header'>Over/Under goals</div>
  <div class='betitem'><div class='value'>2.50</div><div class='label'>Over 2.5 goals</div></div>
  <div class='betitem'><div class='value'>1.50</div><div class='label'>Under 2.5 goals</div></div>
</div>
<div class='betinfoblock'><div class='header'>Inconnu</div>
  <div class='betitem'><div class='value'>9.99</div><div class='label'>Marche exotique</div></div>
</div>
"""


class TestParse(unittest.TestCase):
    def test_extracts_known_markets(self):
        d = odds.parse_odds(REPONSE)
        self.assertEqual(d["o1"], 2.10)
        self.assertEqual(d["ox"], 2.90)
        self.assertEqual(d["o2"], 3.60)
        self.assertEqual(d["over25"], 2.50)
        self.assertEqual(d["under25"], 1.50)

    def test_ignores_unknown_markets(self):
        """Un marche non reconnu ne doit pas polluer la ligne."""
        self.assertNotIn(9.99, odds.parse_odds(REPONSE).values())

    def test_empty_response(self):
        self.assertEqual(odds.parse_odds(""), {})
        self.assertEqual(odds.parse_odds("<div>rien</div>"), {})

    def test_unparsable_value_is_skipped(self):
        html = ("<div class='betitem'><div class='value'>-</div>"
                "<div class='label'>1</div></div>")
        self.assertEqual(odds.parse_odds(html), {})


class TestImplied(unittest.TestCase):
    def test_normalises_and_reports_margin(self):
        p1, px, p2, marge = odds.implied(2.10, 2.90, 3.60)
        self.assertAlmostEqual(p1 + px + p2, 1.0, places=9)
        self.assertAlmostEqual(p1 * 100, 43.3, delta=0.2)
        self.assertAlmostEqual(p2 * 100, 25.3, delta=0.2)
        self.assertAlmostEqual(marge * 100, 9.9, delta=0.2)

    def test_fair_book_has_no_margin(self):
        """Trois cotes a 3.00 : marche equitable, marge nulle."""
        p1, px, p2, marge = odds.implied(3.0, 3.0, 3.0)
        self.assertAlmostEqual(marge, 0.0, places=9)
        self.assertAlmostEqual(p1, 1 / 3, places=9)


class TestArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _rec(self, sa_id, stamp, o1):
        return {"sa_id": sa_id, "fetched_at": stamp, "odds": {"o1": o1, "ox": 3.0, "o2": 3.5}}

    def test_append_and_read(self):
        fresh = odds.append_day("2026-09-23", [self._rec("1", "T1", 2.1)], self.dir)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(len(odds.read_day("2026-09-23", self.dir)), 1)

    def test_unchanged_odds_are_not_rewritten(self):
        odds.append_day("2026-09-23", [self._rec("1", "T1", 2.1)], self.dir)
        fresh = odds.append_day("2026-09-23", [self._rec("1", "T2", 2.1)], self.dir)
        self.assertEqual(fresh, [])
        self.assertEqual(len(odds.read_day("2026-09-23", self.dir)), 1)

    def test_a_moved_odd_is_kept_as_a_new_record(self):
        """Le mouvement des cotes est une information : on garde les deux."""
        odds.append_day("2026-09-23", [self._rec("1", "T1", 2.1)], self.dir)
        fresh = odds.append_day("2026-09-23", [self._rec("1", "T2", 1.9)], self.dir)
        self.assertEqual(len(fresh), 1)
        records = odds.read_day("2026-09-23", self.dir)
        self.assertEqual([r["odds"]["o1"] for r in records], [2.1, 1.9])

    def test_earlier_records_are_never_overwritten(self):
        for i, o in enumerate((2.1, 2.0, 1.9)):
            odds.append_day("2026-09-23", [self._rec("1", f"T{i}", o)], self.dir)
        self.assertEqual(len(odds.read_day("2026-09-23", self.dir)), 3)

    def test_archived_days(self):
        odds.append_day("2026-09-23", [self._rec("1", "T1", 2.1)], self.dir)
        odds.append_day("2026-09-24", [self._rec("2", "T1", 2.1)], self.dir)
        self.assertEqual(odds.archived_days(self.dir), ["2026-09-23", "2026-09-24"])


class TestRebuild(unittest.TestCase):
    """La base doit etre une fonction exacte des archives.

    Le job GitHub et le build du site repartent d'une base vide : si une
    reconstruction perdait les cotes, les cotes d'avant-match des matchs joues
    depuis ne seraient jamais disponibles, et la rentabilite jamais mesurable.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _rec(self, stamp, o1):
        return {"sa_id": "42", "fetched_at": stamp, "odds": {"o1": o1, "ox": 3.0, "o2": 3.5}}

    def _dump(self, conn):
        return conn.execute("SELECT match_id, fetched_at, o1 FROM odds ORDER BY fetched_at").fetchall()

    def test_rebuilt_database_equals_live_database(self):
        live = db.connect(":memory:")
        # Trois passages : le deuxieme n'apporte rien de neuf.
        for stamp, o1 in (("T1", 2.1), ("T2", 2.1), ("T3", 1.9)):
            fresh = odds.append_day("2026-09-23", [self._rec(stamp, o1)], self.dir)
            db.upsert_odds(live, fresh)

        rebuilt = db.connect(":memory:")
        for _day, records in odds.iter_archive(self.dir):
            db.upsert_odds(rebuilt, records)

        self.assertEqual([tuple(r) for r in self._dump(live)],
                         [tuple(r) for r in self._dump(rebuilt)])
        self.assertEqual(len(self._dump(rebuilt)), 2)   # T2 inchange, non archive


class TestStore(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_insert_and_latest(self):
        db.upsert_odds(self.conn, [
            {"sa_id": "42", "fetched_at": "2026-09-23T08:00:00Z", "odds": {"o1": 2.5, "ox": 3.0, "o2": 3.0}},
            {"sa_id": "42", "fetched_at": "2026-09-23T20:00:00Z", "odds": {"o1": 2.1, "ox": 3.0, "o2": 3.6}},
        ])
        self.assertEqual(db.latest_odds(self.conn, "42")["o1"], 2.1)

    def test_same_instant_is_written_once(self):
        rec = {"sa_id": "42", "fetched_at": "T", "odds": {"o1": 2.5}}
        self.assertEqual(db.upsert_odds(self.conn, [rec]), 1)
        self.assertEqual(db.upsert_odds(self.conn, [rec]), 0)

    def test_missing_markets_are_null(self):
        db.upsert_odds(self.conn, [{"sa_id": "42", "fetched_at": "T", "odds": {"o1": 2.5}}])
        self.assertIsNone(db.latest_odds(self.conn, "42")["over25"])


if __name__ == "__main__":
    unittest.main()


class TestDivergence(unittest.TestCase):
    """La compression doit etre detectee quand elle existe, et pas sinon."""

    def setUp(self):
        self.conn = db.connect(":memory:")

    def _load(self, couples):
        """couples : (proba_marche_domicile, proba_statarea_domicile)."""
        from statarea.parser import Match
        matches, records = [], []
        for i, (q1, p1) in enumerate(couples):
            reste = 100 - p1
            matches.append(Match(
                match_id=str(i), sa_id=str(i), match_date="2026-09-20", kickoff="18:00",
                country="Test", competition="L", home=f"H{i}", away=f"A{i}",
                prediction="1", p1=p1, px=reste // 2, p2=reste - reste // 2,
                home_score=1, away_score=0, outcome=None, favorite=None,
                fav_margin=None, surprise=None, upset=None, prediction_hit=None))
            # Cote exacte pour q1, le reste partage a parts egales.
            o1 = 100 / q1
            autre = 2 * 100 / (100 - q1)
            records.append({"sa_id": str(i), "fetched_at": "2026-09-20T10:00:00Z",
                            "odds": {"o1": o1, "ox": autre, "o2": autre}})
        db.upsert_matches(self.conn, matches)
        db.upsert_odds(self.conn, records)

    def test_no_divergence_when_sources_agree(self):
        self._load([(q, q) for q in (8, 15, 25, 35, 45, 55, 70)] * 4)
        for b in odds.divergence_bands(odds.probability_pairs(self.conn)):
            if b["band"] in ("0-10", "65-101"):
                self.assertAlmostEqual(b["gap"], 0.0, delta=1.5)

    def test_compression_is_visible_as_a_sign_flip(self):
        """statarea comprime : +10 en bas, -10 en haut."""
        self._load([(8, 18), (12, 22), (70, 60), (75, 65)] * 6)
        bands = {b["band"]: b for b in odds.divergence_bands(odds.probability_pairs(self.conn))}
        self.assertGreater(bands["0-10"]["gap"], 5)
        self.assertLess(bands["65-101"]["gap"], -5)

    def test_matches_without_prematch_odds_are_excluded(self):
        self._load([(25, 25)] * 6)
        self.conn.execute("UPDATE odds SET fetched_at = '2026-09-20T23:00:00Z'")
        self.assertEqual(odds.probability_pairs(self.conn), [])


class TestPending(unittest.TestCase):
    def test_started_matches_are_not_queried(self):
        from datetime import datetime, timezone
        from statarea.parser import Match
        conn = db.connect(":memory:")
        mk = lambda i, ko: Match(
            match_id=str(i), sa_id=str(i), match_date="2026-09-25", kickoff=ko,
            country="T", competition="L", home=f"H{i}", away=f"A{i}", prediction="1",
            p1=50, px=25, p2=25, home_score=None, away_score=None, outcome=None,
            favorite=None, fav_margin=None, surprise=None, upset=None, prediction_hit=None)
        db.upsert_matches(conn, [mk(1, "10:00"), mk(2, "18:00")])
        now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
        ids = [r["match_id"] for r in odds.pending_matches(conn, "2026-09-25", now=now)]
        self.assertEqual(ids, ["2"])   # le match de 10:00 a commence


class TestRefresh(unittest.TestCase):
    """Seuls les matchs jamais cotes, ou proches du coup d'envoi, sont re-interroges."""

    def test_refresh_policy(self):
        from datetime import datetime, timezone
        from statarea.parser import Match
        conn = db.connect(":memory:")
        mk = lambda i, d, ko: Match(
            match_id=str(i), sa_id=str(i), match_date=d, kickoff=ko,
            country="T", competition="L", home=f"H{i}", away=f"A{i}", prediction="1",
            p1=50, px=25, p2=25, home_score=None, away_score=None, outcome=None,
            favorite=None, fav_margin=None, surprise=None, upset=None, prediction_hit=None)
        db.upsert_matches(conn, [
            mk(1, "2026-09-30", "18:00"),   # deja cote, commence dans 6 h -> oui
            mk(2, "2026-10-02", "18:00"),   # deja cote, dans 2 jours     -> non
            mk(3, "2026-10-02", "20:00"),   # jamais cote, dans 2 jours   -> oui
        ])
        db.upsert_odds(conn, [{"sa_id": s, "fetched_at": "2026-09-30T06:00:00Z",
                               "odds": {"o1": 2.0, "ox": 3.0, "o2": 4.0}} for s in ("1", "2")])
        now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        ids = [r["match_id"] for r in odds.pending_matches(conn, "2026-09-30", now=now)]
        self.assertEqual(ids, ["1", "3"])
        tous = [r["match_id"] for r in odds.pending_matches(conn, "2026-09-30", now=now, refresh_hours=None)]
        self.assertEqual(tous, ["1", "2", "3"])
