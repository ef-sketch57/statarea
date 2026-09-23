"""Collecte des cotes de bookmaker.

Les cotes ne sont pas dans la page de liste : elles sont servies a la demande,
match par match, par l'endpoint interne de statarea.

    POST https://www.statarea.com/actions/controller/
    object={"action":"getMatchBetInformation","matchid":"<sa_id>"}

POURQUOI ELLES CHANGENT TOUT
----------------------------
Un taux de reussite ne dit rien sur la rentabilite. Si statarea annonce 53 %
sur un `1` et que le marche le cote a 1.88 (53 % implicites), suivre ce
pronostic a une esperance nulle avant marge, negative apres. Sans les cotes on
ne peut repondre qu'a "statarea se trompe-t-il ?" ; avec elles, on peut
repondre a "peut-on en tirer quelque chose ?".

Les cotes donnent aussi une SECONDE estimation de probabilite, independante de
celle de statarea. L'ecart entre les deux est un signal en soi.

ARCHIVAGE
---------
Le HTML brut des cotes serait trop lourd a versionner (6 Ko par match et par
passage). Comme le parsing est trivial - une etiquette, une valeur - on archive
directement les valeurs, en JSONL gzippe, un fichier par jour :
data/odds/YYYY-MM-DD.jsonl.gz. Append-only, jamais reecrit, comme les
instantanes HTML. Un relever identique au precedent n'est pas reecrit.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from .scraper import CONTROLLER_URL, make_session

ODDS_DIR = Path("data/odds")

# Etiquettes servies par statarea -> noms de colonnes.
MARKETS = {
    "1": "o1", "X": "ox", "2": "o2",
    "HT1": "oht1", "HTX": "ohtx", "HT2": "oht2",
    "Over 1.5 goals": "over15", "Under 1.5 goals": "under15",
    "Over 2.5 goals": "over25", "Under 2.5 goals": "under25",
    "Over 3.5 goals": "over35", "Under 3.5 goals": "under35",
    "Both team score": "bts", "Only one team score": "ots",
}

# Cotes de reference : sans elles, la ligne n'a pas d'interet.
CORE = ("o1", "ox", "o2")


def parse_odds(html: str) -> dict[str, float]:
    """Extrait les cotes d'une reponse de l'endpoint."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, float] = {}
    for item in soup.select("div.betitem"):
        label = item.find("div", class_="label")
        value = item.find("div", class_="value")
        if label is None or value is None:
            continue
        column = MARKETS.get(label.get_text(strip=True))
        if column is None:
            continue
        try:
            out[column] = float(value.get_text(strip=True))
        except ValueError:
            continue
    return out


def fetch_odds(sa_id: str, session: Optional[requests.Session] = None,
               timeout: float = 30.0) -> dict[str, float]:
    """Retourne les cotes d'un match. Dictionnaire vide si aucune n'est publiee."""
    session = session or make_session()
    r = session.post(
        CONTROLLER_URL,
        data={"object": json.dumps({"action": "getMatchBetInformation", "matchid": str(sa_id)})},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=timeout,
    )
    r.raise_for_status()
    return parse_odds(r.text)


def implied(o1: float, ox: float, o2: float) -> tuple[float, float, float, float]:
    """Probabilites implicites normalisees, et la marge du bookmaker.

    La somme des inverses depasse 1 : c'est la marge (l'overround). On la
    retire au prorata pour obtenir des probabilites comparables a celles de
    statarea.
    """
    raw = (1 / o1, 1 / ox, 1 / o2)
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total, total - 1


# --------------------------------------------------------------------------
# Archivage JSONL, append-only

def odds_path(day: str, odds_dir: Path | str = ODDS_DIR) -> Path:
    return Path(odds_dir) / f"{day}.jsonl.gz"


def read_day(day: str, odds_dir: Path | str = ODDS_DIR) -> list[dict]:
    path = odds_path(day, odds_dir)
    if not path.exists():
        return []
    text = gzip.decompress(path.read_bytes()).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def append_day(day: str, records: list[dict], odds_dir: Path | str = ODDS_DIR) -> int:
    """Ajoute des releves, en ignorant ceux identiques au dernier connu."""
    if not records:
        return 0
    existing = read_day(day, odds_dir)
    last: dict[str, dict] = {}
    for rec in existing:
        last[rec["sa_id"]] = rec

    fresh = []
    for rec in records:
        previous = last.get(rec["sa_id"])
        if previous and previous.get("odds") == rec.get("odds"):
            continue
        fresh.append(rec)
    if not fresh:
        return 0

    path = odds_path(day, odds_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = existing + fresh
    payload = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in lines) + "\n"
    path.write_bytes(gzip.compress(payload.encode("utf-8"), compresslevel=9))
    return len(fresh)


def archived_days(odds_dir: Path | str = ODDS_DIR) -> list[str]:
    return sorted(p.name.split(".")[0] for p in Path(odds_dir).glob("*.jsonl.gz"))


def iter_archive(odds_dir: Path | str = ODDS_DIR) -> Iterator[tuple[str, list[dict]]]:
    for day in archived_days(odds_dir):
        yield day, read_day(day, odds_dir)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Collecte

DEFAULT_DELAY = 1.0


def pending_matches(conn, today: Optional[str] = None, ahead: int = 3,
                    behind: int = 1) -> list:
    """Matchs qui meritent un releve : pas encore joues, dans la fenetre utile.

    Inutile d'interroger un match deja termine (sa cote ne bougera plus) ni un
    match trop lointain (statarea n'a souvent pas encore de cotes).
    """
    from datetime import date, timedelta
    ref = date.fromisoformat(today) if today else date.today()
    return conn.execute(
        """SELECT match_id, sa_id, match_date, kickoff, home, away
           FROM matches
           WHERE sa_id IS NOT NULL AND outcome IS NULL
             AND match_date >= ? AND match_date <= ?
           ORDER BY match_date, kickoff""",
        ((ref - timedelta(days=behind)).isoformat(),
         (ref + timedelta(days=ahead)).isoformat()),
    ).fetchall()


def collect(conn, session=None, today: Optional[str] = None, ahead: int = 3,
            behind: int = 1, delay: float = DEFAULT_DELAY,
            odds_dir: Path | str = ODDS_DIR, limit: Optional[int] = None,
            progress=None) -> dict:
    """Releve les cotes des matchs a venir, archive puis enregistre."""
    import time

    session = session or make_session()
    rows = pending_matches(conn, today, ahead, behind)
    if limit:
        rows = rows[:limit]

    by_day: dict[str, list[dict]] = {}
    stamp = now()
    fetched = empty = failed = 0

    for i, row in enumerate(rows):
        try:
            values = fetch_odds(row["sa_id"], session=session)
        except requests.RequestException:
            failed += 1
        else:
            if values.get("o1") and values.get("ox") and values.get("o2"):
                by_day.setdefault(row["match_date"], []).append(
                    {"sa_id": row["sa_id"], "fetched_at": stamp, "odds": values})
                fetched += 1
            else:
                empty += 1
        if progress:
            progress(i + 1, len(rows), row)
        if delay and i < len(rows) - 1:
            time.sleep(delay)

    written = sum(append_day(day, recs, odds_dir) for day, recs in by_day.items())
    stored = 0
    for day, recs in by_day.items():
        stored += __import__("statarea.db", fromlist=["db"]).upsert_odds(conn, recs)

    return {"matchs": len(rows), "cotes": fetched, "sans_cote": empty,
            "echecs": failed, "archives": written, "enregistres": stored}
