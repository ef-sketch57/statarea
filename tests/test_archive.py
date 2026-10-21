"""Tests de l'archivage par instantanes.

statarea remanie ses pages passees en continu : entre deux collectes d'une
meme date, des matchs disparaissent et d'autres apparaissent. Une archive
ecrasee est donc une perte definitive.
"""

import gzip
import tempfile
import zlib
import unittest
from datetime import datetime, timezone
from pathlib import Path

from statarea import db, scraper

PAGE = """
<div id='cdate'>2026-09-19</div>
<div class='competition'>
  <div class='header'><img alt='Italy country flag'/><div class='name'>ITALY - SERIE A</div></div>
  <div class='body'>{matches}</div>
</div>
"""

MATCH = """
<div class='match' id='{id}'>
  <div class='date'>12:00</div>
  <div class='tip'><div class='value'><div class='type4'>1</div></div></div>
  <div class='teams'>
    <div class='ownheader'>2026-09-19 12:00</div>
    <div class='hostteam'>{hg}<div class='name'>{home}</div></div>
    <div class='guestteam'>{ag}<div class='name'>{away}</div></div>
  </div>
  <div class='coefrow'>
    <div class='ownheader'>
      <div class='coefbox'>1</div><div class='coefbox'>X</div><div class='coefbox'>2</div>
    </div>
    <div class='coefbox'><div class='value'>52</div></div>
    <div class='coefbox'><div class='value'>28</div></div>
    <div class='coefbox'><div class='value'>20</div></div>
  </div>
</div>
"""


def page(*teams, scores=False):
    """Construit une page de test.

    L'identifiant vient de la paire d'equipes, pas de la position : les sa_id
    de statarea sont propres a chaque match et le restent quand la page change
    d'ordre ou qu'un match disparait.
    """
    goals = "<a class='goals'>2</a>", "<a class='goals'>0</a>"
    blank = "<div class='goals'>-</div>", "<div class='goals'>-</div>"
    hg, ag = goals if scores else blank
    body = "".join(
        MATCH.format(id=zlib.crc32(f"{h}|{a}".encode()), home=h, away=a, hg=hg, ag=ag)
        for h, a in teams
    )
    return PAGE.format(matches=body)


class TestSnapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _at(self, hour):
        return datetime(2026, 9, 19, hour, tzinfo=timezone.utc)

    def test_a_second_fetch_never_overwrites_the_first(self):
        a = scraper.archive("2026-09-19", page(("Roma", "Inter")), self.raw, self._at(10))
        b = scraper.archive("2026-09-19", page(("Roma", "Inter"), ("Lazio", "Napoli")),
                            self.raw, self._at(11))
        self.assertIsNotNone(b)
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists(), "le premier instantane a ete ecrase")
        self.assertEqual(len(scraper.snapshots("2026-09-19", self.raw)), 2)

    def test_identical_content_is_not_stored_twice(self):
        html = page(("Roma", "Inter"))
        scraper.archive("2026-09-19", html, self.raw, self._at(10))
        self.assertIsNone(scraper.archive("2026-09-19", html, self.raw, self._at(11)))
        self.assertEqual(len(scraper.snapshots("2026-09-19", self.raw)), 1)

    def test_snapshots_are_chronological(self):
        for h in (12, 10, 11):
            scraper.archive("2026-09-19", page(*[("A", f"B{h}{i}") for i in range(h)]),
                            self.raw, self._at(h))
        names = [p.name for p in scraper.snapshots("2026-09-19", self.raw)]
        self.assertEqual(names, sorted(names))

    def test_legacy_flat_archive_is_still_read_and_comes_first(self):
        legacy = self.raw / "2026-09-19.html.gz"
        legacy.write_bytes(gzip.compress(page(("Roma", "Inter")).encode()))
        scraper.archive("2026-09-19", page(("Lazio", "Napoli")), self.raw, self._at(11))

        found = scraper.snapshots("2026-09-19", self.raw)
        self.assertEqual(found[0], legacy)
        self.assertEqual(len(found), 2)
        self.assertEqual(scraper.archived_days(self.raw), ["2026-09-19"])

    def test_reparse_recovers_matches_statarea_removed(self):
        """Le coeur du correctif : l'union des instantanes, pas le dernier."""
        scraper.archive("2026-09-19", page(("Roma", "Inter"), ("Lazio", "Napoli")),
                        self.raw, self._at(10))
        # Plus tard, statarea a retire Lazio-Napoli et ajoute Milan-Juve.
        scraper.archive("2026-09-19", page(("Roma", "Inter"), ("Milan", "Juve"), scores=True),
                        self.raw, self._at(11))

        conn = db.connect(":memory:")
        for _, matches in scraper.reparse_archive(self.raw):
            db.upsert_matches(conn, matches)

        teams = {r[0] for r in conn.execute("SELECT home FROM matches")}
        self.assertEqual(teams, {"Roma", "Lazio", "Milan"})

        # Et le score du dernier instantane a bien complete la premiere ligne.
        row = conn.execute("SELECT * FROM matches WHERE home = 'Roma'").fetchone()
        self.assertEqual(row["outcome"], "1")
        self.assertEqual(row["favorite"], "home")


if __name__ == "__main__":
    unittest.main()


class TestIncrementalReparse(unittest.TestCase):
    """La base mise en cache et completee doit etre IDENTIQUE a une base
    reconstruite de zero : sinon le cache fausserait silencieusement l'analyse."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.raw, self.odds = root / "raw", root / "odds"
        self.raw.mkdir(); self.odds.mkdir()
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def _at(self, day, hour):
        return datetime(2026, 9, day, hour, tzinfo=timezone.utc)

    def _reparse(self, dbfile, incremental):
        from statarea.cli import main
        args = ["reparse", "--db", str(dbfile), "--raw-dir", str(self.raw),
                "--odds-dir", str(self.odds)]
        if incremental:
            args.append("--incremental")
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            main(args)
        return out.getvalue()

    def _dump(self, dbfile):
        conn = db.connect(dbfile)
        return [tuple(r) for r in conn.execute(
            """SELECT match_id, match_date, kickoff, home, away, prediction, p1, px, p2,
                      home_score, away_score, outcome, favorite, surprise, upset
               FROM matches ORDER BY match_id""")]

    def test_incremental_equals_full_rebuild(self):
        cache = self.root / "cache.db"
        scraper.archive("2026-09-19", page(("Roma", "Inter"), ("Lazio", "Napoli")), self.raw, self._at(19, 10))
        self._reparse(cache, incremental=True)          # premiere execution : complete

        # Execution suivante : nouveaux instantanes, dont un score et un match retire.
        scraper.archive("2026-09-19", page(("Roma", "Inter"), ("Milan", "Juve"), scores=True),
                        self.raw, self._at(19, 14))
        p20 = page(("Atalanta", "Torino")).replace("2026-09-19", "2026-09-20")
        scraper.archive("2026-09-20", p20, self.raw, self._at(19, 14))
        out = self._reparse(cache, incremental=True)
        self.assertIn("incrementale : 2 instantane", out)

        full = self.root / "full.db"
        self._reparse(full, incremental=False)
        self.assertEqual(self._dump(cache), self._dump(full))
        self.assertEqual(len(self._dump(full)), 4)

    def test_nothing_new_replays_nothing(self):
        cache = self.root / "cache.db"
        scraper.archive("2026-09-19", page(("Roma", "Inter")), self.raw, self._at(19, 10))
        self._reparse(cache, incremental=True)
        self.assertIn("incrementale : 0 instantane", self._reparse(cache, incremental=True))

    def test_an_older_snapshot_forces_a_full_rebuild(self):
        """Un instantane plus ancien que ceux deja traites changerait l'ordre de
        rejeu, donc les valeurs figees : on reconstruit tout."""
        cache = self.root / "cache.db"
        scraper.archive("2026-09-19", page(("Roma", "Inter")), self.raw, self._at(19, 14))
        self._reparse(cache, incremental=True)
        scraper.archive("2026-09-19", page(("Roma", "Inter"), ("Lazio", "Napoli")), self.raw, self._at(19, 10))
        out = self._reparse(cache, incremental=True)
        self.assertIn("complete", out)
        full = self.root / "full.db"
        self._reparse(full, incremental=False)
        self.assertEqual(self._dump(cache), self._dump(full))
