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

# Hypothese PRE-ENREGISTREE le 2026-09-24, avant d'avoir vu les matchs qui la
# testeront. Constat exploratoire sur 501 pronostics simples joues : quand le
# pronostic contredit les pourcentages de statarea lui-meme, il est juste dans
# 23,9 % des cas (46 matchs), contre 53,8 % quand il les suit (448 matchs).
# Regle : remplacer un pronostic simple qui contredit ses propres pourcentages
# par l'issue la plus probable selon ces pourcentages. Seuls les matchs joues a
# partir de cette date comptent pour la valider.
PREREGISTERED_SINCE = "2026-09-24"


def argmax_selection(p1, px, p2) -> Optional[str]:
    """L'issue au pourcentage strictement le plus haut, ou None en cas d'egalite."""
    values = {"1": p1, "X": px, "2": p2}
    if None in values.values():
        return None
    top = max(values.values())
    best = [k for k, v in values.items() if v == top]
    return best[0] if len(best) == 1 else None


# Hypothese H2, PRE-ENREGISTREE le 2026-09-30, avant les matchs qui la testeront.
# Constat qui l'a suggeree (donc sans valeur de preuve) : sur 24 pronostics
# contraires joues du 24 au 29/09, les suivre tels quels rapportait +28,8 %,
# alors que la regle H1 (les remplacer par l'issue favorite) perdait -26,7 %.
# Ces pronostics sont surtout des nuls, cotes autour de 3,3 : justes une fois
# sur trois, ils peuvent quand meme etre rentables.
# Regle : parier le pronostic contraire tel quel, a la cote de cloture.
# Test : uniquement les matchs dont la date UTC est posterieure ou egale a
# H2_SINCE - le 30/09 est exclu, une partie de ses matchs ayant deja ete vue.
# Critere fixe d'avance : conclusion seulement a partir de 200 paris, et
# seulement si |t| > 2. Aucune conclusion avant, quel que soit le chiffre.
H2_SINCE = "2026-10-01"
H2_MIN_BETS = 200


def is_contrary(prediction, p1, px, p2) -> bool:
    """Pronostic simple qui contredit le pourcentage strictement le plus haut."""
    if prediction not in SINGLES:
        return False
    best = argmax_selection(p1, px, p2)
    return best is not None and best != prediction


def corrected_prediction(prediction, p1, px, p2) -> Optional[str]:
    """Le pronostic, sauf s'il contredit les pourcentages : on suit alors ceux-ci."""
    if prediction not in SINGLES:
        return None
    best = argmax_selection(p1, px, p2)
    return best if best and best != prediction else prediction


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
        f"""SELECT match_id, match_date, prediction, outcome, favorite, p1, px, p2
            FROM matches WHERE {" AND ".join(clauses)}""", params).fetchall()
    book = db.closing_odds(conn)

    out = {
        "statarea_simple": Result("pronostic statarea (paris simples)"),
        "statarea_double": Result("pronostic statarea (doubles chances, cote reconstituee)"),
        "corrige": Result("pronostic corrige (pre-enregistre le 24/09)"),
        "corrige_hors_echantillon": Result("  dont matchs joues depuis le 24/09"),
        "contraires": Result("pronostics contraires tels quels (H2)"),
        "contraires_hors_echantillon": Result("  dont matchs joues depuis le 01/10"),
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

        sel = corrected_prediction(pred, m["p1"], m["px"], m["p2"])
        cote = odds_for(odds, sel) if sel else None
        if cote:
            ret = cote - 1 if wins(sel, outcome) else -1.0
            out["corrige"].returns.append(ret)
            if m["match_date"] >= PREREGISTERED_SINCE:
                out["corrige_hors_echantillon"].returns.append(ret)

        if is_contrary(pred, m["p1"], m["px"], m["p2"]):
            cote = odds_for(odds, pred)
            if cote:
                ret = cote - 1 if wins(pred, outcome) else -1.0
                out["contraires"].returns.append(ret)
                if m["match_date"] >= H2_SINCE:
                    out["contraires_hors_echantillon"].returns.append(ret)

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
