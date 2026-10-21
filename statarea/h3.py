"""Hypothese H3, PRE-ENREGISTREE le 2026-10-21, avant les matchs qui la testeront.

CONSTAT QUI L'A SUGGEREE
------------------------
Sur l'historique football-data (227 494 matchs, 38 championnats suivis par les
grands bookmakers), aucune propension par equipe, et un defaut par
championnat trop faible pour etre exploite depuis 2018. Reste une piste : les
competitions que football-data ne couvre pas, nombreuses chez statarea, aux
marches moins liquides. Aucun match de cette population n'a ete examine sous
cet angle avant ce commit.

HYPOTHESE
---------
Dans les competitions NON couvertes par football-data, le marche (cote de
cloture relevee sur statarea, marge retiree au prorata) se trompe davantage
que dans les competitions couvertes, dans l'un des deux sens :

  H3a : l'outsider y gagne plus souvent qu'annonce ;
  H3b : le favori y echoue (nul ou defaite) plus souvent qu'annonce.

La comparaison se fait ENTRE les deux groupes, meme bookmaker, meme periode :
le biais propre au bookmaker (favori-outsider, marge) s'annule.

CRITERE, FIXE D'AVANCE
----------------------
- matchs joues a partir du 22/10/2026 (date UTC), avec une cote de cloture et
  un favori designe par le marche ;
- aucune conclusion avant H3_MIN_OTHER matchs non couverts ET H3_MIN_COVERED
  matchs couverts, quel que soit le chiffre ;
- ecart observe - annonce, non couvert moins couvert, z > 2,5 (deux tests :
  seuil de Bonferroni) ;
- rentabilite : rapportee a titre d'information, elle ne se conclut qu'avec
  t > 2 sur le ROI lui-meme.

Population : toutes les competitions hors de la liste COVERED, sans aucune
autre exclusion (coupes, amicaux et feminines compris), pour ne laisser aucun
choix a faire apres coup.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from . import db
from .odds import implied

H3_SINCE = "2026-10-22"
H3_MIN_OTHER = 2000
H3_MIN_COVERED = 300
H3_Z = 2.5

# Les 38 championnats de l'historique, tels que statarea les nomme : nom exact.
COVERED = {
    "England": ("PREMIER LEAGUE", "CHAMPIONSHIP", "LEAGUE ONE", "LEAGUE TWO", "NATIONAL LEAGUE"),
    "Scotland": ("PREMIERSHIP", "CHAMPIONSHIP", "LEAGUE ONE", "LEAGUE TWO"),
    "Germany": ("BUNDESLIGA", "2. BUNDESLIGA"),
    "Italy": ("SERIE A", "SERIE B"),
    "Spain": ("LALIGA", "LALIGA2"),
    "France": ("LIGUE 1", "LIGUE 2"),
    "Netherlands": ("EREDIVISIE",),
    "Belgium": ("BELGIAN PRO LEAGUE",),
    "Portugal": ("LIGA PORTUGAL",),
    "Turkey": ("SUPER LIG",),
    "Greece": ("SUPER LEAGUE",),
    "Austria": ("BUNDESLIGA",),
    "Brazil": ("SERIE A",),
    "China": ("SUPER LEAGUE",),
    "Denmark": ("SUPERLIGAEN",),
    "Rep. Of Ireland": ("PREMIER DIVISION",),
    "Japan": ("J. LEAGUE",),
    "Norway": ("ELITESERIEN",),
    "Poland": ("EKSTRAKLASA",),
    "Romania": ("SUPERLIGA",),
    "Russia": ("PREMIER LEAGUE",),
    "Sweden": ("ALLSVENSKAN",),
    "Switzerland": ("SUPER LEAGUE",),
}

# Championnats que statarea decoupe en phases ou en conferences : le nom suivi
# d'un espace et d'une precision ("VEIKKAUSLIIGA CHAMPIONSHIP ROUND",
# "LIGA MX APERTURA") reste couvert, sauf competition feminine.
COVERED_PREFIX = {
    "Argentina": ("LIGA PROFESIONAL",),
    "Finland": ("VEIKKAUSLIIGA",),
    "Mexico": ("LIGA MX",),
    "USA": ("MAJOR LEAGUE SOCCER",),
}
WOMEN = ("FEMENIL", "FEMMINILE", "WOMEN", "KVINNER")


def is_covered(country: str | None, competition: str | None) -> bool:
    comp = (competition or "").upper().strip()
    if comp in COVERED.get(country or "", ()):
        return True
    return any((comp == name or comp.startswith(name + " ")) and not any(w in comp for w in WOMEN)
               for name in COVERED_PREFIX.get(country or "", ()))


@dataclass
class Group:
    n: int = 0
    obs: dict = field(default_factory=lambda: {"underdog": 0, "favorite": 0})
    exp: dict = field(default_factory=lambda: {"underdog": 0.0, "favorite": 0.0})
    var: dict = field(default_factory=lambda: {"underdog": 0.0, "favorite": 0.0})
    returns: dict = field(default_factory=lambda: {"underdog": [], "favorite": []})

    def residual(self, role: str) -> float:
        """Ecart observe - annonce, par match."""
        return (self.obs[role] - self.exp[role]) / self.n if self.n else 0.0

    def residual_var(self, role: str) -> float:
        return self.var[role] / self.n ** 2 if self.n else 0.0


def evaluate(conn: sqlite3.Connection, since: str = H3_SINCE) -> dict[str, Group]:
    book = db.closing_odds(conn)
    groups = {"couvert": Group(), "non_couvert": Group()}
    rows = conn.execute(
        "SELECT match_id, country, competition, outcome FROM matches "
        "WHERE outcome IS NOT NULL AND match_date >= ?", (since,))
    for m in rows:
        o = book.get(m["match_id"])
        if not o or not (o["o1"] and o["ox"] and o["o2"]):
            continue
        q1, qx, q2, _ = implied(o["o1"], o["ox"], o["o2"])
        if qx >= max(q1, q2) or q1 == q2:
            continue
        fav_home = q1 > q2
        q_fav, q_dog = (q1, q2) if fav_home else (q2, q1)
        o_fav, o_dog = (o["o1"], o["o2"]) if fav_home else (o["o2"], o["o1"])
        fav_won = m["outcome"] == ("1" if fav_home else "2")
        dog_won = m["outcome"] == ("2" if fav_home else "1")

        g = groups["couvert" if is_covered(m["country"], m["competition"]) else "non_couvert"]
        g.n += 1
        for role, p, hit, cote in (
            ("underdog", q_dog, dog_won, o_dog),
            # contre le favori : double chance nul-outsider reconstituee
            ("favorite", 1 - q_fav, not fav_won, o["ox"] * o_dog / (o["ox"] + o_dog)),
        ):
            g.obs[role] += int(hit)
            g.exp[role] += p
            g.var[role] += p * (1 - p)
            g.returns[role].append(cote - 1 if hit else -1.0)
    return groups


def _roi(returns: list[float]) -> tuple[float | None, float | None]:
    n = len(returns)
    if n < 2:
        return None, None
    mean = sum(returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
    return mean, (mean / (sd / math.sqrt(n)) if sd else None)


def build_report(conn: sqlite3.Connection, since: str = H3_SINCE) -> str:
    g = evaluate(conn, since)
    cov, oth = g["couvert"], g["non_couvert"]
    ready = oth.n >= H3_MIN_OTHER and cov.n >= H3_MIN_COVERED
    lines = ["=" * 96,
             "  H3 (pre-enregistree le 21/10) - le marche se trompe-t-il plus hors des grands championnats ?",
             "=" * 96, "",
             f"  matchs joues depuis le {since[8:10]}/{since[5:7]} : non couverts {oth.n}/{H3_MIN_OTHER}, "
             f"couverts {cov.n}/{H3_MIN_COVERED}",
             ""]
    lines.append(f"  {'':34} {'non couverts':>14} {'couverts':>10} {'difference':>11} {'z':>6}")
    for role, label in (("underdog", "H3a outsider gagne (obs - annonce)"),
                        ("favorite", "H3b favori ne gagne pas (idem)")):
        a, b = oth.residual(role), cov.residual(role)
        se = math.sqrt(oth.residual_var(role) + cov.residual_var(role))
        z = (a - b) / se if se else None
        lines.append(f"  {label:34} {a * 100:+13.1f}  {b * 100:+9.1f}  {(a - b) * 100:+10.1f} "
                     + (f"{z:+6.1f}" if z is not None else "   n/a"))
    lines.append("  (en points par match)")
    lines.append("")
    for role, label in (("underdog", "parier l'outsider"), ("favorite", "parier contre le favori")):
        for name, grp in (("non couverts", oth), ("couverts", cov)):
            roi, t = _roi(grp.returns[role])
            if roi is None:
                continue
            lines.append(f"  {label:24} {name:13} {len(grp.returns[role]):>6} paris  "
                         f"ROI {roi * 100:+6.1f}%  t {(t or 0):+5.1f}")
    lines.append("")
    lines.append(f"  Critere : z > {H3_Z:g} sur la difference, uniquement une fois les deux seuils "
                 "atteints. Etat : " + ("conclusion possible." if ready else "trop tot pour conclure."))
    lines.append("")
    return "\n".join(lines)
