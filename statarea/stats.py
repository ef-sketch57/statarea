"""Indices de surprise par equipe.

Deux indicateurs distincts, jamais melanges :

  FIABILITE      - quand l'equipe est FAVORITE, a quelle frequence se fait-elle
                   surprendre ? (surprise = le favori n'a pas gagne, nul compris)
  IMPREVISIBILITE - quand l'equipe est OUTSIDER, a quelle frequence cree-t-elle
                   la surprise ? (upset = l'outsider gagne ; le nul ne compte pas)

Seuls les matchs joues (outcome non NULL) et ayant un favori identifie
(favorite non NULL, donc hors matchs ou X est en tete) entrent dans le calcul.
Les autres restent en base, ils sont juste exclus ici.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import db

BASE_FILTER = "outcome IS NOT NULL AND favorite IS NOT NULL"


@dataclass
class TeamStats:
    team: str
    fav_matches: int
    fav_surprised: int
    dog_matches: int
    dog_upsets: int

    @property
    def matches(self) -> int:
        return self.fav_matches + self.dog_matches

    @property
    def surprised_rate(self) -> Optional[float]:
        """Taux de fois ou l'equipe, favorite, n'a pas gagne."""
        return self.fav_surprised / self.fav_matches if self.fav_matches else None

    @property
    def reliability(self) -> Optional[float]:
        """1 - taux de surprise subie. Plus c'est haut, plus le favori tient."""
        rate = self.surprised_rate
        return None if rate is None else 1.0 - rate

    @property
    def upset_rate(self) -> Optional[float]:
        """Taux de fois ou l'equipe, outsider, a gagne."""
        return self.dog_upsets / self.dog_matches if self.dog_matches else None


def _where(min_margin: int, since: Optional[str], until: Optional[str]) -> tuple[str, list]:
    clauses = [BASE_FILTER]
    params: list = []
    if min_margin:
        clauses.append("fav_margin >= ?")
        params.append(min_margin)
    if since:
        clauses.append("match_date >= ?")
        params.append(since)
    if until:
        clauses.append("match_date <= ?")
        params.append(until)
    return " AND ".join(clauses), params


def team_stats(
    conn: sqlite3.Connection,
    min_margin: int = 0,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list[TeamStats]:
    """Agrege les compteurs par equipe, domicile et exterieur confondus."""
    where, params = _where(min_margin, since, until)

    sql = f"""
    WITH roles AS (
        -- l'equipe a domicile
        SELECT {db.team_sql("home")} AS team,
               favorite = 'home' AS is_fav,
               CASE WHEN favorite = 'home' THEN surprise END AS surprised,
               CASE WHEN favorite = 'away' THEN upset     END AS created
        FROM matches WHERE {where}
        UNION ALL
        -- l'equipe a l'exterieur
        SELECT {db.team_sql("away")} AS team,
               favorite = 'away' AS is_fav,
               CASE WHEN favorite = 'away' THEN surprise END AS surprised,
               CASE WHEN favorite = 'home' THEN upset     END AS created
        FROM matches WHERE {where}
    )
    SELECT team,
           SUM(is_fav)                      AS fav_matches,
           COALESCE(SUM(surprised), 0)      AS fav_surprised,
           SUM(NOT is_fav)                  AS dog_matches,
           COALESCE(SUM(created), 0)        AS dog_upsets
    FROM roles
    GROUP BY team
    """
    rows = conn.execute(sql, params + params).fetchall()
    return [
        TeamStats(
            team=r["team"],
            fav_matches=r["fav_matches"] or 0,
            fav_surprised=r["fav_surprised"] or 0,
            dog_matches=r["dog_matches"] or 0,
            dog_upsets=r["dog_upsets"] or 0,
        )
        for r in rows
    ]


def most_reliable(stats: list[TeamStats], min_fav: int = 5) -> list[TeamStats]:
    """Favoris les plus fiables : ils confirment quand ils sont attendus."""
    pool = [s for s in stats if s.fav_matches >= min_fav]
    return sorted(pool, key=lambda s: (-s.reliability, -s.fav_matches, s.team))


def least_reliable(stats: list[TeamStats], min_fav: int = 5) -> list[TeamStats]:
    """Favoris les moins fiables : les equipes a eviter de suivre."""
    return list(reversed(most_reliable(stats, min_fav)))


def most_unpredictable(stats: list[TeamStats], min_dog: int = 5) -> list[TeamStats]:
    """Outsiders les plus imprevisibles : ils creent la surprise."""
    pool = [s for s in stats if s.dog_matches >= min_dog]
    return sorted(pool, key=lambda s: (-s.upset_rate, -s.dog_matches, s.team))


def stable_teams(stats: list[TeamStats], min_fav: int = 5, min_dog: int = 5,
                 max_surprised: float = 0.30, max_upset: float = 0.15) -> list[TeamStats]:
    """Equipes 'stables' dans les deux roles : celles dont on peut suivre la prediction."""
    out = [
        s for s in stats
        if s.fav_matches >= min_fav and s.dog_matches >= min_dog
        and s.surprised_rate <= max_surprised and s.upset_rate <= max_upset
    ]
    return sorted(out, key=lambda s: (s.surprised_rate + s.upset_rate, -s.matches))


def global_summary(
    conn: sqlite3.Connection,
    min_margin: int = 0,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> dict:
    """Taux de reference sur l'echantillon : sans ca, les taux par equipe ne se lisent pas."""
    where, params = _where(min_margin, since, until)
    row = conn.execute(
        f"""SELECT COUNT(*)                       AS n,
                   COALESCE(SUM(surprise), 0)     AS surprises,
                   COALESCE(SUM(upset), 0)        AS upsets,
                   AVG(fav_margin)                AS avg_margin
            FROM matches WHERE {where}""",
        params,
    ).fetchone()

    hit = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(prediction_hit), 0) AS hits "
        "FROM matches WHERE prediction_hit IS NOT NULL"
    ).fetchone()

    n = row["n"] or 0
    return {
        "matches": n,
        "surprises": row["surprises"],
        "surprise_rate": row["surprises"] / n if n else None,
        "upsets": row["upsets"],
        "upset_rate": row["upsets"] / n if n else None,
        "avg_margin": row["avg_margin"],
        "prediction_n": hit["n"] or 0,
        "prediction_hits": hit["hits"],
        "prediction_hit_rate": (hit["hits"] / hit["n"]) if hit["n"] else None,
    }
