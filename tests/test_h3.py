import unittest

from statarea import db, h3


class TestCoverage(unittest.TestCase):
    def test_exact_names(self):
        self.assertTrue(h3.is_covered("England", "NATIONAL LEAGUE"))
        self.assertFalse(h3.is_covered("England", "NATIONAL LEAGUE NORTH"))
        self.assertFalse(h3.is_covered("Greece", "SUPER LEAGUE 2"))
        self.assertFalse(h3.is_covered("Japan", "J. LEAGUE 2"))
        self.assertFalse(h3.is_covered("Italy", "SERIE A FEMMINILE"))
        self.assertFalse(h3.is_covered("International", "CHAMPIONS LEAGUE"))
        self.assertFalse(h3.is_covered(None, None))

    def test_phases(self):
        self.assertTrue(h3.is_covered("Finland", "VEIKKAUSLIIGA CHAMPIONSHIP ROUND"))
        self.assertTrue(h3.is_covered("Mexico", "LIGA MX APERTURA"))
        self.assertFalse(h3.is_covered("Mexico", "LIGA MX FEMENIL APERTURA GROUP A"))
        self.assertFalse(h3.is_covered("Mexico", "LIGA DE EXPANSION MX APERTURA"))


class TestEvaluate(unittest.TestCase):
    def test_groups(self):
        conn = db.connect(":memory:")
        conn.execute("INSERT INTO matches (match_id, home, away, match_date, kickoff, country, "
                     "competition, home_score, away_score, outcome) VALUES "
                     "('a','H','V','2026-10-23','18:00','England','LEAGUE TWO',0,1,'2'),"
                     "('b','H','V','2026-10-23','18:00','Nigeria','NPFL',1,1,'X'),"
                     "('c','H','V','2026-10-20','18:00','Nigeria','NPFL',1,1,'X')")
        for mid in "abc":
            conn.execute("INSERT INTO odds (match_id, fetched_at, o1, ox, o2) "
                         "VALUES (?, '2026-10-23T10:00:00Z', 1.5, 4.0, 6.0)", (mid,))
        g = h3.evaluate(conn)
        self.assertEqual((g["couvert"].n, g["non_couvert"].n), (1, 1))   # 'c' avant H3_SINCE
        self.assertEqual(g["couvert"].obs["underdog"], 1)
        self.assertEqual(g["non_couvert"].obs, {"underdog": 0, "favorite": 1})
        self.assertIn("trop tot", h3.build_report(conn))


if __name__ == "__main__":
    unittest.main()
