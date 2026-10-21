import gzip
import tempfile
import unittest
from pathlib import Path

from statarea import history

MMZ = (b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A,PSH,PSD,PSA,PSCH,PSCD,PSCA\r\n"
       b"E0,16/08/2024,20:00,Man United,Fulham,1,0,H,1.60,4.20,5.25,1.62,4.30,5.40,1.65,4.20,5.10\r\n"
       b"E0,17/08/2024,12:30,Ipswich,Liverpool,0,2,A,7.00,5.00,1.44,,,,,,\r\n"
       b"E0,18/08/2024,16:30,Chelsea,Man City,,,,2.0,3.0,4.0,,,,,,\r\n"
       b",,,,,,,,,,,,,,,,\r\n")

NEW = ("Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA\n"
       "Brazil,Serie A,2012,19/05/12,20:00,Grêmio,Santos,1,1,D,2.10,3.30,3.60,2.0,3.2,3.5\n"
       "Norway,Eliteserien,2019/2020,01/03/2020,,Molde,Brann,2,1,H,,,,1.5,4.0,6.0\n").encode("cp1252")


class TestParse(unittest.TestCase):
    def test_mmz(self):
        ms = history.parse_csv(MMZ, "E0", "England", 2024)
        self.assertEqual(len(ms), 2)                 # sans score / ligne vide ignores
        m = ms[0]
        self.assertEqual((m.date, m.home, m.away, m.hg, m.ag), ("2024-08-16", "Man United", "Fulham", 1, 0))
        self.assertEqual((m.o1, m.ox, m.o2, m.source), (1.65, 4.20, 5.10, "pinnacle_cloture"))
        self.assertEqual(ms[1].source, "b365")       # repli quand la cloture manque
        self.assertEqual(ms[1].outcome, "2")

    def test_new_files(self):
        ms = history.parse_csv(NEW, "BRA", "Brazil")
        self.assertEqual(ms[0].home, "Grêmio")   # cp1252 decode
        self.assertEqual((ms[0].date, ms[0].season, ms[0].outcome), ("2012-05-19", 2012, "X"))
        self.assertEqual((ms[1].country, ms[1].season, ms[1].source), ("Norway", 2019, "moyenne_cloture"))


class TestArchive(unittest.TestCase):
    def test_store_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "E0.csv.gz"
            self.assertTrue(history.store(MMZ, path))
            self.assertFalse(history.store(MMZ, path))
            self.assertEqual(gzip.decompress(path.read_bytes()), MMZ)

    def test_load_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            history.store(MMZ, Path(tmp) / "mmz4281" / "2425" / "E0.csv.gz")
            history.store(NEW, Path(tmp) / "new" / "BRA.csv.gz")
            ms = history.load(tmp)
            self.assertEqual(len(ms), 4)
            self.assertEqual({m.season for m in ms if m.league == "E0"}, {2024})

    def test_targets(self):
        t = history.targets(2005, 2006)
        self.assertIn(("https://www.football-data.co.uk/mmz4281/0506/E0.csv",
                       Path("mmz4281/0506/E0.csv.gz")), t)
        self.assertEqual(len(t), 2 * len(history.LEAGUES) + len(history.EXTRA))
        self.assertEqual(history.season_code(2099), "9900")


if __name__ == "__main__":
    unittest.main()
