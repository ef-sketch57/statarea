"""Rentabilite : que rapporterait reellement le fait de suivre les pronostics.

La justesse et la rentabilite sont deux choses sans rapport. Un pronostic juste
a 53 % cote 1.88 (53 % implicites) a une esperance nulle avant marge du
bookmaker, negative apres. La question n'est donc jamais "statarea a-t-il
raison ?" mais "a-t-il plus souvent raison que la cote ne le suppose ?".

REGLE ABSOLUE
-------------
Seules les cotes relevees STRICTEMENT AVANT le coup d'envoi sont utilisees
(cf. db.closing_odds). Utiliser une cote posterieure reviendrait a parier en
connaissant le resultat, et produirait des rendements imaginaires.

DOUBLES CHANCES
---------------
statarea produit aussi des pronostics 1X, X2 et 12, alors que l'endpoint ne
sert que les cotes 1, X et 2. La cote double chance est donc RECONSTITUEE :

    cote(A ou B) = (cote_A * cote_B) / (cote_A + cote_B)

C'est la combinaison exacte des probabilites implicites, marge du bookmaker
comprise. Elle reste une reconstitution : un bookmaker affiche parfois une
double chance legerement differente. Les resultats la distinguent donc des
paris simples, qui eux sont exacts.

LES TEMOINS
-----------
Un rendement ne se lit pas seul. Le rapport compare toujours le fait de suivre
statarea a deux strategies passives : parier systematiquement le favori, et
parier systematiquement le domicile. Une strategie qui ne bat pas ces temoins
n'apporte rien. Avec une marge mediane de ~10 %, le rendement de reference
d'un parieur sans information est d'environ -10 %.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from . import db

SINGLES = {"1", "X", "2"}
DOUBLES = {"1X", "X2", "12"}


def double_chance_odds(odds: dict[str, float], prediction: str) -> Optional[float]:
    """Cote reconstituee d'une double chance, depuis les cotes 1 X 2."""
    pair = {"1X": ("o1", "ox"), "X2": ("ox", "o2"), "12": ("o1", "o2")}[prediction]
    a, b = odds.get(pair[0]), odds.get(pair[1])
    if not a or not b:
        return None
    return (a * b) / (a + b)


def odds_for(odds: dict[str, float], selection: str) -> Optional[float]:
    """Cote d'une selection, simple ou double chance."""
    if selection in SINGLES:
        return odds.get({"1": "o1", "X": "ox", "2": "o2"}[selection])
    if selection in DOUBLES:
        return double_chance_odds(odds, selection)
    return None


def wins(selection: str, outcome: str) -> bool:
    return outcome in selection


@dataclass
class Result:
    label: str
    returns: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.returns)

    @property
    def hits(self) -> int:
        return sum(1 for r in self.returns if r > 0)

    @property
    def profit(self) -> float:
        return sum(self.returns)

    @property
    def roi(self) -> Optional[float]:
        """Rendement par unite misee."""
        return self.profit / self.n if self.n else None

    @property
    def se(self) -> Optional[float]:
        """Erreur type du rendement : sans elle, un ROI ne veut rien dire."""
        if self.n < 2:
            return None
        mean = self.profit / self.n
        var = sum((r - mean) ** 2 for r in self.returns) / (self.n - 1)
        return math.sqrt(var / self.n)

    @property
    def t(self) -> Optional[float]:
        """Combien d'erreurs types separent le rendement de zero."""
        se, roi = self.se, self.roi
        return roi / se if se and se > 0 else None

    @property
    def significant(self) -> bool:
        t = self.t
        return t is not None and abs(t) > 2


def evaluate(conn: sqlite3.Connection, since: Optional[str] = None,
             until: Optional[str] = None,
             include_doubles: bool = True) -> dict[str, Result]:
    """Rendement de chaque strategie sur les matchs joues ayant une cote de cloture."""
    clauses = ["outcome IS NOT NULL"]
    params: list = []
    if since:
        clauses.append("match_date >= ?")
        params.append(since)
    if until:
        clauses.append("match_date <= ?")
        params.append(until)

    matches = conn.execute(
        f"""SELECT match_id, prediction, outcome, favorite
            FROM matches WHERE {" AND ".join(clauses)}""", params).fetchall()
    book = db.closing_odds(conn)

    out = {
        "statarea_simple": Result("pronostic statarea (paris simples)"),
        "statarea_double": Result("pronostic statarea (doubles chances, cote reconstituee)"),
        "favori": Result("temoin : toujours le favori"),
        "domicile": Result("temoin : toujours le domicile"),
    }

    for m in matches:
        o = book.get(m["match_id"])
        if not o:
            continue
        odds = {k: o[k] for k in ("o1", "ox", "o2") if o[k]}
        if len(odds) < 3:
            continue

        pred, outcome = m["prediction"], m["outcome"]
        if pred in SINGLES:
            cote = odds_for(odds, pred)
            if cote:
                out["statarea_simple"].returns.append(cote - 1 if wins(pred, outcome) else -1.0)
        elif include_doubles and pred in DOUBLES:
            cote = odds_for(odds, pred)
            if cote:
                out["statarea_double"].returns.append(cote - 1 if wins(pred, outcome) else -1.0)

        if m["favorite"]:
            sel = "1" if m["favorite"] == "home" else "2"
            cote = odds_for(odds, sel)
            if cote:
                out["favori"].returns.append(cote - 1 if wins(sel, outcome) else -1.0)

        out["domicile"].returns.append(odds["o1"] - 1 if outcome == "1" else -1.0)

    return out


def book_margin(conn: sqlite3.Connection) -> Optional[float]:
    """Marge mediane du bookmaker sur les cotes de cloture connues."""
    from .odds import implied

    margins = []
    for o in db.closing_odds(conn).values():
        if o["o1"] and o["ox"] and o["o2"]:
            margins.append(implied(o["o1"], o["ox"], o["o2"])[3])
    if not margins:
        return None
    margins.sort()
    mid = len(margins) // 2
    return margins[mid] if len(margins) % 2 else (margins[mid - 1] + margins[mid]) / 2
