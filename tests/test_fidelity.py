import random
import unittest

from statarea import fidelity
from statarea.history import HistMatch


def simulate(fragile=0.0, seasons=10, teams=40, per_season=30, seed=1):
    """Marche honnete ; les equipes 0-7 de chaque pays, favorites, perdent
    `fragile` de probabilite de victoire au profit du nul, chaque saison."""
    rnd = random.Random(seed)
    out = []
    for country in ("A", "B"):
        strength = [rnd.gauss(0, 0.8) for _ in range(teams)]
        for season in range(2010, 2010 + seasons):
            for k in range(teams * per_season // 2):
                h, a = rnd.sample(range(teams), 2)
                d = strength[h] - strength[a] + 0.3
                p1 = 0.72 / (1 + 2.718 ** (-d)) + 0.02
                px = 0.26
                p2 = 1 - p1 - px
                true = [p1, px, p2]
                fav = 0 if p1 > p2 else 2
                if fragile and (h if fav == 0 else a) < 8:
                    true[fav] -= fragile
                    true[1] += fragile
                u = rnd.random()
                res = "1" if u < true[0] else ("X" if u < true[0] + true[1] else "2")
                hg, ag = {"1": (1, 0), "X": (0, 0), "2": (0, 1)}[res]
                o = [1 / (q * 1.04) for q in (p1, px, p2)]
                out.append(HistMatch(country, "L", season, f"{season}-08-{k % 28 + 1:02d}",
                                     f"T{h}", f"T{a}", hg, ag, o[0], o[1], o[2], "test"))
    return out


class TestFidelity(unittest.TestCase):
    def test_rows_roles(self):
        m = HistMatch("A", "L", 2020, "2020-08-01", "H", "V", 0, 0, 1.5, 4.0, 6.0, "t")
        fav, dog = fidelity.rows_of([m])
        self.assertEqual((fav.team, fav.role, fav.hit), (("A", "H"), "favorite", 1))
        self.assertEqual((dog.team, dog.role, dog.hit), (("A", "V"), "underdog", 0))
        self.assertAlmostEqual(fav.o_event, 4.0 * 6.0 / 10.0)
        # Nul en tete du marche : aucun favori, aucune ligne.
        self.assertEqual(fidelity.rows_of([HistMatch("A", "L", 2020, "d", "H", "V", 0, 0,
                                                     3.5, 2.5, 3.5, "t")]), [])

    def test_null_world_shows_no_signal(self):
        rows = fidelity.rows_of(simulate())
        cal = fidelity.fit_calibration(rows)
        d = fidelity.dispersion(fidelity.team_scores(rows, cal, "favorite", 50).values())
        self.assertLess(abs(d["sd"] - 1), 3 * d["se"])
        p = fidelity.persistence(rows, cal, "favorite")
        self.assertLess(abs(p["r"]), 3 * p["se"])

    def test_fragile_teams_are_found_and_persist(self):
        rows = fidelity.rows_of(simulate(fragile=0.15))
        cal = fidelity.fit_calibration(rows)
        scores = fidelity.team_scores(rows, cal, "favorite", 50)
        d = fidelity.dispersion(scores.values())
        self.assertGreater(d["sd"], 1 + 2 * d["se"])
        top = sorted(scores.values(), key=lambda t: -t.z)[:8]
        self.assertGreaterEqual(sum(int(t.team[1][1:]) < 8 for t in top), 6)
        self.assertTrue(fidelity.persistence(rows, cal, "favorite")["significant"])
        bt = fidelity.backtest(rows, 2014)["contre les favoris fragiles"]
        self.assertGreater(bt["strategy"].roi, bt["witness"].roi)

    def test_backtest_uses_only_past(self):
        # Une seule saison : rien d'anterieur, donc aucun pari.
        rows = fidelity.rows_of(simulate(seasons=1))
        bt = fidelity.backtest(rows, 2010)
        self.assertTrue(all(r["strategy"].n == 0 for r in bt.values()))


if __name__ == "__main__":
    unittest.main()


class TestLeagues(unittest.TestCase):
    def test_biased_league_is_found_and_bet(self):
        # Pays B : 16 % des victoires du favori deviennent des nuls, toutes
        # les saisons. Pays A : marche honnete.
        ms = simulate(seed=3)
        rnd = random.Random(5)
        biased = []
        for m in ms:
            if m.country == "B" and rnd.random() < 0.16:
                fav_home = m.o1 < m.o2
                fav_won = m.hg > m.ag if fav_home else m.ag > m.hg
                if fav_won:
                    m = HistMatch(m.country, m.league, m.season, m.date, m.home, m.away,
                                  0, 0, m.o1, m.ox, m.o2, m.source)
            biased.append(m)
        rows = fidelity.rows_of(biased)
        cal = fidelity.fit_calibration(rows, by_country=False)
        sc = fidelity.league_scores(rows, cal, "favorite")
        self.assertGreater(sc[("B", "L")].z, 3)
        self.assertLess(sc[("A", "L")].z, sc[("B", "L")].z)
        self.assertTrue(fidelity.league_persistence(rows, cal, "favorite")["r"] > 0)
        bt = fidelity.league_backtest(rows, 2014)[("favorite", +1)]
        self.assertGreater(bt["strategy"].n, 0)
        self.assertGreater(bt["strategy"].roi, bt["witness"].roi)
