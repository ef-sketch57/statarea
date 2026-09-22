"""Stockage SQLite des matchs.

Regle d'or de l'upsert : une valeur deja connue n'est JAMAIS ecrasee par un
NULL. Le score arrive apres la prediction, donc la meme date est re-scrapee
plusieurs fois ; une re-collecte avant le coup d'envoi ne doit pas effacer un
score deja enregistre.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .parser import Match

DEFAULT_DB = Path("data/statarea.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id        TEXT PRIMARY KEY,   -- date|home|away
    sa_id           TEXT,               -- identifiant interne statarea
    match_date      TEXT NOT NULL,
    kickoff         TEXT,
    country         TEXT,
    competition     TEXT,
    home            TEXT NOT NULL,
    away            TEXT NOT NULL,
    prediction      TEXT,               -- 1 X 2 1X X2 12
    p1              INTEGER,
    px              INTEGER,
    p2              INTEGER,
    home_score      INTEGER,
    away_score      INTEGER,
    outcome         TEXT,               -- 1 X 2, calcule depuis le score
    favorite        TEXT,               -- home / away / NULL si X est en tete
    fav_margin      INTEGER,            -- favori moins deuxieme pourcentage
    surprise        INTEGER,            -- le favori n'a pas gagne
    upset           INTEGER,            -- l'outsider a gagne
    prediction_hit  INTEGER,            -- la prediction statarea etait bonne
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_home ON matches(home);
CREATE INDEX IF NOT EXISTS idx_matches_away ON matches(away);
CREATE INDEX IF NOT EXISTS idx_matches_outcome ON matches(outcome);
"""

# Colonnes fusionnees par COALESCE : le nouveau remplace l'ancien seulement
# s'il n'est pas NULL.
MERGED = (
    "sa_id", "kickoff", "country", "competition", "prediction",
    "p1", "px", "p2", "home_score", "away_score", "outcome",
    "favorite", "fav_margin", "surprise", "upset", "prediction_hit",
)

INSERT_COLS = ("match_id", "match_date", "home", "away") + MERGED

UPSERT = f"""
INSERT INTO matches ({", ".join(INSERT_COLS)})
VALUES ({", ".join(":" + c for c in INSERT_COLS)})
ON CONFLICT(match_id) DO UPDATE SET
    {", ".join(f"{c} = COALESCE(excluded.{c}, matches.{c})" for c in MERGED)},
    updated_at = datetime('now')
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_matches(conn: sqlite3.Connection, matches: Iterable[Match]) -> tuple[int, int]:
    """Insere ou complete les matchs. Retourne (nouveaux, mis a jour)."""
    rows = [m.as_dict() if isinstance(m, Match) else dict(m) for m in matches]
    if not rows:
        return 0, 0

    ids = [r["match_id"] for r in rows]
    known = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        placeholders = ",".join("?" * len(chunk))
        known.update(
            r[0] for r in conn.execute(
                f"SELECT match_id FROM matches WHERE match_id IN ({placeholders})", chunk
            )
        )

    payload = [{c: r.get(c) for c in INSERT_COLS} for r in rows]
    with conn:
        conn.executemany(UPSERT, payload)

    new = sum(1 for i in ids if i not in known)
    return new, len(ids) - new


def count(conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> int:
    sql = "SELECT COUNT(*) FROM matches" + (f" WHERE {where}" if where else "")
    return conn.execute(sql, params).fetchone()[0]


def dates(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """(date, nb matchs, nb avec score) par date, du plus recent au plus ancien."""
    return [
        (r["match_date"], r["n"], r["played"])
        for r in conn.execute(
            "SELECT match_date, COUNT(*) AS n, "
            "SUM(outcome IS NOT NULL) AS played "
            "FROM matches GROUP BY match_date ORDER BY match_date DESC"
        )
    ]
