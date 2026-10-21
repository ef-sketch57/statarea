"""Stockage SQLite des matchs.

Regle d'or de l'upsert : une valeur deja connue n'est JAMAIS ecrasee par un
NULL. Le score arrive apres la prediction, donc la meme date est re-scrapee
plusieurs fois ; une re-collecte avant le coup d'envoi ne doit pas effacer un
score deja enregistre.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .parser import Match

DEFAULT_DB = Path("data/statarea.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id        TEXT PRIMARY KEY,   -- date|home|away
    sa_id           TEXT,               -- identifiant interne statarea
    match_date      TEXT,
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
    home_country    TEXT,               -- pays de l'equipe (homonymes)
    away_country    TEXT,
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_home ON matches(home);
CREATE INDEX IF NOT EXISTS idx_matches_away ON matches(away);
CREATE INDEX IF NOT EXISTS idx_matches_outcome ON matches(outcome);

-- Un releve de cotes par passage : les cotes bougent, et ce mouvement est
-- lui-meme une information. On garde donc l'historique plutot que la derniere
-- valeur, exactement comme pour les instantanes HTML.
CREATE TABLE IF NOT EXISTS odds (
    match_id    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,      -- UTC, ISO 8601
    o1          REAL, ox    REAL, o2    REAL,   -- temps plein 1 X 2
    oht1        REAL, ohtx  REAL, oht2  REAL,   -- mi-temps
    over15      REAL, under15 REAL,
    over25      REAL, under25 REAL,
    over35      REAL, under35 REAL,
    bts         REAL, ots     REAL,             -- les deux marquent / un seul
    PRIMARY KEY (match_id, fetched_at)
);
CREATE INDEX IF NOT EXISTS idx_odds_match ON odds(match_id);

-- Instantanes HTML deja rejoues dans cette base. Permet a une base conservee
-- entre deux executions (cache) de ne traiter que les nouveaux.
CREATE TABLE IF NOT EXISTS snapshots_done (
    path        TEXT PRIMARY KEY,   -- relatif a data/raw
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

ODDS_COLS = ("o1", "ox", "o2", "oht1", "ohtx", "oht2",
             "over15", "under15", "over25", "under25", "over35", "under35",
             "bts", "ots")

# La prediction et les pourcentages sont figes a leur PREMIERE valeur connue.
# C'est le coeur de la methode : on veut noter le pronostic tel qu'il etait
# publie AVANT le match. Si statarea revisait ses pourcentages une fois le
# resultat connu, les reprendre rendrait toute l'analyse circulaire.
FROZEN = ("prediction", "p1", "px", "p2", "match_date", "kickoff")

# Les faits qui se completent au fil des re-scrapes : le score arrive apres.
REFRESHED = ("sa_id", "country", "competition", "home_score", "away_score",
             "home_country", "away_country")

# Colonnes entierement recalculees depuis les precedentes, jamais ecrites
# telles quelles : c'est ce qui garantit qu'une ligne reste coherente quel que
# soit l'ordre des collectes.
DERIVED = ("outcome", "favorite", "fav_margin", "surprise", "upset", "prediction_hit")

INSERT_COLS = ("match_id", "home", "away") + FROZEN + REFRESHED

UPSERT = f"""
INSERT INTO matches ({", ".join(INSERT_COLS)})
VALUES ({", ".join(":" + c for c in INSERT_COLS)})
ON CONFLICT(match_id) DO UPDATE SET
    {", ".join(f"{c} = COALESCE(matches.{c}, excluded.{c})" for c in FROZEN)},
    {", ".join(f"{c} = COALESCE(excluded.{c}, matches.{c})" for c in REFRESHED)},
    updated_at = datetime('now')
"""

# Regles metier, exprimees une seule fois, en SQL.
#
#   favorite   : argmax STRICT de p1 / px / p2, restreint aux colonnes 1 et 2.
#                X en tete, ou egalite au sommet -> NULL (match exclu des indices).
#   fav_margin : pourcentage du favori moins le deuxieme, toutes colonnes confondues.
#   surprise   : le favori n'a pas gagne (nul compris).
#   upset      : l'outsider a gagne (le nul n'est pas un upset).
RECOMPUTE = """
UPDATE matches SET
    outcome = CASE
        WHEN home_score IS NULL OR away_score IS NULL THEN NULL
        WHEN home_score > away_score THEN '1'
        WHEN home_score < away_score THEN '2'
        ELSE 'X' END,
    favorite = CASE
        WHEN p1 IS NULL OR px IS NULL OR p2 IS NULL THEN NULL
        WHEN p1 > px AND p1 > p2 THEN 'home'
        WHEN p2 > px AND p2 > p1 THEN 'away'
        ELSE NULL END,
    fav_margin = CASE
        WHEN p1 IS NULL OR px IS NULL OR p2 IS NULL THEN NULL
        WHEN p1 > px AND p1 > p2 THEN p1 - MAX(px, p2)
        WHEN p2 > px AND p2 > p1 THEN p2 - MAX(p1, px)
        ELSE NULL END
"""

RECOMPUTE_FLAGS = """
UPDATE matches SET
    surprise = CASE
        WHEN favorite IS NULL OR outcome IS NULL THEN NULL
        WHEN favorite = 'home' THEN outcome <> '1'
        ELSE outcome <> '2' END,
    upset = CASE
        WHEN favorite IS NULL OR outcome IS NULL THEN NULL
        WHEN favorite = 'home' THEN outcome = '2'
        ELSE outcome = '1' END,
    prediction_hit = CASE
        WHEN prediction IS NULL OR outcome IS NULL THEN NULL
        ELSE INSTR(prediction, outcome) > 0 END
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Colonnes ajoutees apres coup : une base existante les recoit vides, la
    # prochaine reconstruction depuis les archives les remplit.
    have = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
    for col in ("home_country", "away_country"):
        if col not in have:
            conn.execute(f"ALTER TABLE matches ADD COLUMN {col} TEXT")
    return conn


# Identite d'une equipe : son nom suivi de son pays quand on le connait.
def team_sql(side: str) -> str:
    return f"{side} || COALESCE(' (' || {side}_country || ')', '')"


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
        recompute(conn)

    new = sum(1 for i in ids if i not in known)
    return new, len(ids) - new


def recompute(conn: sqlite3.Connection) -> None:
    """Recalcule les colonnes derivees de toute la base.

    Appele apres chaque upsert, et disponible seul pour rejouer une regle
    modifiee sans retoucher aux donnees brutes ni au reseau.
    """
    conn.execute(RECOMPUTE)
    conn.execute(RECOMPUTE_FLAGS)


def upsert_odds(conn: sqlite3.Connection, records) -> int:
    """Enregistre des releves de cotes. Un meme (match, instant) n'est ecrit qu'une fois."""
    rows = []
    for rec in records:
        values = rec.get("odds") or {}
        row = {"match_id": rec["sa_id"], "fetched_at": rec["fetched_at"]}
        row.update({c: values.get(c) for c in ODDS_COLS})
        rows.append(row)
    if not rows:
        return 0

    cols = ("match_id", "fetched_at") + ODDS_COLS
    sql = (f"INSERT OR IGNORE INTO odds ({', '.join(cols)}) "
           f"VALUES ({', '.join(':' + c for c in cols)})")
    before = conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0]
    with conn:
        conn.executemany(sql, rows)
    return conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0] - before


CLOSING_ODDS_SQL = """
    SELECT o.* FROM odds o
    JOIN (
        SELECT o2.match_id, MAX(o2.fetched_at) AS fetched_at
        FROM odds o2 JOIN matches m2 ON m2.match_id = o2.match_id
        -- Strictement AVANT le coup d'envoi : une cote relevee pendant ou
        -- apres le match n'est plus une prevision, et l'utiliser serait
        -- regarder le resultat avant de parier.
        WHERE o2.fetched_at < m2.match_date || 'T' || COALESCE(m2.kickoff, '00:00') || ':00Z'
        GROUP BY o2.match_id
    ) last ON last.match_id = o.match_id AND last.fetched_at = o.fetched_at
"""


def closing_odds(conn: sqlite3.Connection) -> dict:
    """Dernier releve de cotes anterieur au coup d'envoi, par match.

    C'est la "ligne de cloture" : l'estimation du marche la plus informee dont
    on dispose sans jamais regarder apres le match.
    """
    return {r["match_id"]: r for r in conn.execute(CLOSING_ODDS_SQL)}


def latest_odds(conn: sqlite3.Connection, match_id: str) -> Optional[sqlite3.Row]:
    """Dernier releve connu pour un match."""
    return conn.execute(
        "SELECT * FROM odds WHERE match_id = ? ORDER BY fetched_at DESC LIMIT 1",
        (match_id,)).fetchone()


def done_snapshots(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT path FROM snapshots_done")}


def mark_snapshot(conn: sqlite3.Connection, key: str) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO snapshots_done (path) VALUES (?)", (key,))


def reset(conn: sqlite3.Connection) -> None:
    """Vide la base, pour une reconstruction complete depuis les archives."""
    with conn:
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM odds")
        conn.execute("DELETE FROM snapshots_done")


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
