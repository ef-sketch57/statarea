"""Indice residuel par equipe : l'ecart a ce que statarea annoncait.

POURQUOI PAS LE TAUX BRUT
-------------------------
Un taux de surprise brut par equipe melange deux choses : la fragilite propre
de l'equipe, et la difficulte de ses matchs. Une equipe toujours favorite a
40/32/28 sera surprise bien plus souvent qu'une equipe favorite a 65/22/13,
sans qu'aucune fragilite n'entre en jeu. Un classement construit sur le taux
brut redecouvrirait donc surtout "les equipes qui jouent des matchs serres".

L'indice residuel corrige ca : on compare l'observe a ce que les pourcentages
de statarea annoncaient, match par match.

    attendu(surprise)  = 1 - p_favori / 100     (le favori ne gagne pas)
    attendu(upset)     = p_outsider / 100       (l'outsider gagne)

La calibration de statarea ayant ete verifiee comme bonne, ces attendus sont
une reference honnete et ne demandent aucun ajustement sur les donnees - donc
aucun risque de surapprentissage, et rien a re-entrainer.

    residu = observe - attendu

Un residu positif au role de favorite = l'equipe deçoit plus que sa cote ne le
laissait prevoir. Positif au role d'outsider = elle cree la surprise plus
souvent qu'annonce.

SIGNIFICATIVITE
---------------
Chaque match est un tirage de Bernoulli de probabilite propre. La somme suit
une loi de Poisson-binomiale, de variance sum p_i(1-p_i). Le z-score compare
donc le residu a cet ecart-type, ce qui tient compte du fait qu'un match a
50/50 est bien plus bruite qu'un match a 90/10.

Les z sont centres sur le biais global de l'echantillon : on classe les
equipes les unes par rapport aux autres, pas par rapport a la calibration
absolue de statarea, qui est mesuree separement.

LE TEST QUI COMPTE VRAIMENT
---------------------------
Si l'hypothese de depart est fausse - si aucune equipe n'a de propension
propre a la surprise - les z se distribuent comme une normale centree reduite,
d'ecart-type 1. Une surdispersion nette (ecart-type > 1) est la signature d'un
signal reel. `overdispersion()` mesure exactement ca : c'est le seul resultat
a regarder avant de croire au moindre classement.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

Role = Literal["favorite", "underdog"]
Baseline = Literal["statarea", "market"]

# En dessous, un classement n'a aucun sens : le bruit domine.
MIN_MATCHES = 5

# Seuil en dessous duquel le test de surdispersion lui-meme n'est pas fiable :
# l'approximation normale de la loi de Poisson-binomiale demande quelques
# tirages par equipe pour tenir.
RELIABLE_MIN = 5


@dataclass
class TeamResidual:
    team: str
    role: Role
    n: int
    observed: int
    expected: float
    variance: float
    offset: float          # biais de reference retire, par match (hors cette equipe)

    @property
    def residual(self) -> float:
        """Ecart a l'attendu, corrige du biais de reference."""
        return self.observed - self.expected - self.offset * self.n

    @property
    def per_match(self) -> float:
        """Residu moyen par match, en points de pourcentage."""
        return self.residual / self.n * 100 if self.n else 0.0

    @property
    def z(self) -> Optional[float]:
        """Ecarts-types entre l'observe et l'attendu."""
        return self.residual / math.sqrt(self.variance) if self.variance > 0 else None

    @property
    def significant(self) -> bool:
        """|z| > 2 : environ 5 % de faux positifs, AVANT correction multiple."""
        z = self.z
        return z is not None and abs(z) > 2


def _db_team(side: str) -> str:
    from .db import team_sql
    return team_sql(side)


def _expectation_rows(conn: sqlite3.Connection, role: Role, baseline: Baseline,
                      since: Optional[str], until: Optional[str]) -> list[dict]:
    """Un enregistrement par (equipe, match) : probabilite attendue et issue.

    Deux references possibles :

    - "statarea" : la probabilite annoncee par le site. Mesure l'ecart a ce que
      statarea prevoyait.
    - "market" : la probabilite implicite de la cote de CLOTURE, marge du
      bookmaker retiree au prorata. Reference bien plus exigeante - le marche
      integre les blessures, les compositions et l'argent mise - et la seule
      qui mene a une conclusion sur la rentabilite.

    Avec "market", seuls les matchs disposant d'une cote relevee AVANT le coup
    d'envoi sont retenus.
    """
    from .odds import implied

    clauses = ["favorite IS NOT NULL", "outcome IS NOT NULL",
               "p1 IS NOT NULL", "px IS NOT NULL", "p2 IS NOT NULL"]
    params: list = []
    if since:
        clauses.append("match_date >= ?")
        params.append(since)
    if until:
        clauses.append("match_date <= ?")
        params.append(until)

    matches = conn.execute(
        f"""SELECT match_id, {_db_team("home")} AS home, {_db_team("away")} AS away,
                   p1, px, p2, favorite, surprise, upset
            FROM matches WHERE {" AND ".join(clauses)}""", params).fetchall()

    book = {}
    if baseline == "market":
        from . import db as _db
        book = _db.closing_odds(conn)

    rows: list[dict] = []
    for m in matches:
        if baseline == "market":
            o = book.get(m["match_id"])
            if not o or not (o["o1"] and o["ox"] and o["o2"]):
                continue
            q1, _qx, q2, _marge = implied(o["o1"], o["ox"], o["o2"])
        else:
            total = m["p1"] + m["px"] + m["p2"]
            q1, q2 = m["p1"] / total, m["p2"] / total

        fav_home = m["favorite"] == "home"
        if role == "favorite":
            team = m["home"] if fav_home else m["away"]
            p = 1 - (q1 if fav_home else q2)     # le favori ne gagne pas
            hit = m["surprise"]
        else:
            team = m["away"] if fav_home else m["home"]
            p = q2 if fav_home else q1           # l'outsider gagne
            hit = m["upset"]

        if hit is None or p is None:
            continue
        rows.append({"team": team, "p": p, "hit": hit})
    return rows


def _rows(conn: sqlite3.Connection, role: Role,
          since: Optional[str] = None, until: Optional[str] = None,
          baseline: Baseline = "statarea") -> list[dict]:
    return _expectation_rows(conn, role, baseline, since, until)


def calibration_offset(conn: sqlite3.Connection, role: Role = "favorite",
                       since: Optional[str] = None, until: Optional[str] = None,
                       baseline: Baseline = "statarea") -> tuple[float, int]:
    """Biais global de la reference sur l'echantillon : (observe - attendu) par match."""
    rows = _rows(conn, role, since, until, baseline)
    if not rows:
        return 0.0, 0
    return sum(r["hit"] - r["p"] for r in rows) / len(rows), len(rows)


def team_residuals(conn: sqlite3.Connection, role: Role = "favorite",
                   min_matches: int = MIN_MATCHES,
                   since: Optional[str] = None,
                   until: Optional[str] = None,
                   baseline: Baseline = "statarea") -> list[TeamResidual]:
    rows = _rows(conn, role, since, until, baseline)

    agg: dict[str, dict[str, float]] = {}
    for r in rows:
        a = agg.setdefault(r["team"], {"n": 0, "observed": 0, "expected": 0.0, "variance": 0.0})
        a["n"] += 1
        a["observed"] += r["hit"]
        a["expected"] += r["p"]
        a["variance"] += r["p"] * (1 - r["p"])

    total_n = sum(a["n"] for a in agg.values())
    total_obs = sum(a["observed"] for a in agg.values())
    total_exp = sum(a["expected"] for a in agg.values())

    out = []
    for team, a in agg.items():
        if a["n"] < min_matches:
            continue
        # Biais de reference calcule SANS cette equipe : sinon une equipe qui
        # pese lourd dans l'echantillon annule une partie de son propre signal.
        rest_n = total_n - a["n"]
        offset = ((total_obs - a["observed"]) - (total_exp - a["expected"])) / rest_n if rest_n else 0.0
        out.append(TeamResidual(team=team, role=role, n=int(a["n"]),
                                observed=int(a["observed"]), expected=a["expected"],
                                variance=a["variance"], offset=offset))
    return sorted(out, key=lambda t: -(t.z or 0))


def overdispersion(conn: sqlite3.Connection, role: Role = "favorite",
                   min_matches: int = MIN_MATCHES,
                   since: Optional[str] = None,
                   until: Optional[str] = None,
                   baseline: Baseline = "statarea") -> dict:
    """Le seul test qui dise si l'hypothese tient.

    Sous l'hypothese nulle - aucune equipe n'a de propension propre - les z se
    distribuent en normale centree reduite. sd nettement > 1 = signal reel.
    """
    teams = team_residuals(conn, role, min_matches, since, until, baseline)
    zs = [t.z for t in teams if t.z is not None]
    if len(zs) < 2:
        return {"teams": len(zs), "sd": None, "mean": None, "reliable": False,
                "significant": 0, "verdict": "echantillon insuffisant"}

    mean = sum(zs) / len(zs)
    sd = math.sqrt(sum((z - mean) ** 2 for z in zs) / (len(zs) - 1))
    # Erreur type de l'ecart-type d'un echantillon normal.
    se = 1 / math.sqrt(2 * (len(zs) - 1))

    # En dessous de ~5 matchs, l'approximation normale du z est mauvaise et
    # gonfle mecaniquement la dispersion : le test ne veut alors rien dire.
    if min_matches < RELIABLE_MIN:
        verdict = (f"non interpretable : a moins de {RELIABLE_MIN} matchs par equipe, "
                   "l'approximation normale gonfle la dispersion")
        return {"teams": len(zs), "sd": sd, "mean": mean, "se": se,
                "expected_sd": 1.0, "verdict": verdict, "reliable": False,
                "significant": sum(1 for t in teams if t.significant)}

    if sd > 1 + 2 * se:
        verdict = "surdispersion : un signal par equipe est plausible"
    elif sd < 1 - 2 * se:
        verdict = "sous-dispersion (inattendu, verifier les donnees)"
    else:
        verdict = "compatible avec le hasard : aucun signal par equipe detectable"
    return {"teams": len(zs), "sd": sd, "mean": mean, "se": se,
            "expected_sd": 1.0, "verdict": verdict, "reliable": True,
            "significant": sum(1 for t in teams if t.significant)}
