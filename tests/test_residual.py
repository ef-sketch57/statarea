"""Tests de l'indice residuel.

Les deux tests qui comptent : le module doit voir un signal qu'on y plante,
et ne PAS en voir dans du bruit pur. Sans ces deux garanties, un classement
d'equipes ne vaut rien.
"""

import random
import unittest

from statarea import db, residual
from statarea.parser import Match


def _match(i, p1, px, p2, hs, as_, home="H", away="A") -> Match:
    total = p1 + px + p2
    assert total == 100, "les pourcentages doivent sommer a 100"
    return Match(
        match_id=f"m{i}", sa_id=str(i), match_date="2026-09-19", kickoff="12:00",
        country="Test", competition="LIGUE", home=home, away=away, prediction="1",
        p1=p1, px=px, p2=p2, home_score=hs, away_score=as_,
        outcome=None, favorite=None, fav_margin=None,
        surprise=None, upset=None, prediction_hit=None,
    )


class TestResidual(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def _load(self, rows):
        db.upsert_matches(self.conn, rows)

    def test_a_team_matching_its_odds_has_no_residual(self):
        """Favori a 60 % qui gagne 6 fois sur 10 : residu nul, pas de signal."""
        rows = []
        for i in range(10):
            won = i < 6
            rows.append(_match(i, 60, 25, 15, 1 if won else 0, 0 if won else 1,
                               home="Regulier", away=f"Adv{i}"))
        self._load(rows)

        t = residual.team_residuals(self.conn, "favorite", min_matches=5)[0]
        self.assertEqual(t.team, "Regulier")
        self.assertAlmostEqual(t.per_match, 0.0, delta=1.0)
        self.assertFalse(t.significant)

    def test_the_confounding_trap_is_neutralised(self):
        """Le piege central : un favori TOUJOURS serre, qui tient exactement sa cote.

        Son taux de surprise brut est eleve (60 %) - un classement naif le
        designerait comme fragile. Son residu doit pourtant etre nul : il fait
        exactement ce que ses pourcentages annoncaient.
        """
        rows = []
        for i in range(10):
            won = i < 4          # gagne 4 fois sur 10, soit 40 % - comme annonce
            rows.append(_match(i, 40, 32, 28, 1 if won else 0, 0 if won else 1,
                               home="ToujoursSerre", away=f"Adv{i}"))
        self._load(rows)

        t = residual.team_residuals(self.conn, "favorite", min_matches=5)[0]
        raw = t.observed / t.n
        self.assertAlmostEqual(raw, 0.60, delta=0.01)     # taux brut alarmant
        self.assertAlmostEqual(t.per_match, 0.0, delta=1.0)  # residu nul
        self.assertFalse(t.significant)

    def test_a_genuinely_fragile_team_is_flagged(self):
        """Favori a 80 % qui perd systematiquement : residu massif."""
        rows = [_match(i, 80, 12, 8, 0, 1, home="Fragile", away=f"Adv{i}")
                for i in range(12)]
        self._load(rows)

        t = residual.team_residuals(self.conn, "favorite", min_matches=5)[0]
        self.assertGreater(t.per_match, 50)
        self.assertTrue(t.significant)

    def test_pure_noise_shows_no_overdispersion(self):
        """Le garde-fou : sur du bruit, le module ne doit annoncer aucun signal."""
        rng = random.Random(7)
        rows, i = [], 0
        for team in range(40):
            for _ in range(10):
                p1 = rng.choice((45, 55, 60, 70))
                won = rng.random() < p1 / 100
                rows.append(_match(i, p1, 100 - p1 - 20, 20,
                                   1 if won else 0, 0 if won else 1,
                                   home=f"T{team}", away=f"Adv{i}"))
                i += 1
        self._load(rows)

        o = residual.overdispersion(self.conn, "favorite", min_matches=5)
        self.assertEqual(o["teams"], 40)
        self.assertAlmostEqual(o["sd"], 1.0, delta=0.35)
        self.assertIn("hasard", o["verdict"])

    def test_a_planted_signal_is_detected(self):
        """Symetrique : la moitie des equipes sur-performe, l'autre sous-performe."""
        rng = random.Random(11)
        rows, i = [], 0
        for team in range(40):
            bias = 0.25 if team % 2 else -0.25
            for _ in range(12):
                p1 = 55
                won = rng.random() < (p1 / 100 + bias)
                rows.append(_match(i, p1, 25, 20, 1 if won else 0, 0 if won else 1,
                                   home=f"T{team}", away=f"Adv{i}"))
                i += 1
        self._load(rows)

        o = residual.overdispersion(self.conn, "favorite", min_matches=5)
        self.assertGreater(o["sd"], 1.5)
        self.assertIn("surdispersion", o["verdict"])

    def test_underdog_role(self):
        """Outsider a 20 % qui gagne la moitie du temps : signal positif."""
        rows = []
        for i in range(12):
            won = i < 6
            rows.append(_match(i, 70, 10, 20, 0 if won else 1, 1 if won else 0,
                               home=f"Adv{i}", away="Audacieux"))
        self._load(rows)

        t = residual.team_residuals(self.conn, "underdog", min_matches=5)[0]
        self.assertEqual(t.team, "Audacieux")
        self.assertGreater(t.per_match, 20)
        self.assertTrue(t.significant)

    def test_min_matches_filters(self):
        self._load([_match(i, 60, 25, 15, 1, 0, home="Rare", away=f"A{i}") for i in range(3)])
        self.assertEqual(residual.team_residuals(self.conn, "favorite", min_matches=5), [])
        self.assertEqual(len(residual.team_residuals(self.conn, "favorite", min_matches=3)), 1)


if __name__ == "__main__":
    unittest.main()
