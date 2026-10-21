"""Fidelite a la probabilite, par equipe, sur l'historique football-data.

LA QUESTION
-----------
Quelles equipes SUIVENT leur probabilite, lesquelles s'en ecartent ? Deux
roles, jamais melanges, comme dans `residual` :

  favorite : la "surprise" est que le favori ne gagne pas (nul ou defaite).
             attendu = 1 - p(favori gagne)
  outsider : l'"upset" est que l'outsider gagne.
             attendu = p(outsider gagne)

La probabilite est celle du MARCHE (cote de cloture quand elle existe, marge
retiree au prorata). Une equipe dont les surprises depassent ce que le marche
annoncait est une equipe que le marche surestime.

LES PIEGES, ET CE QUI LES CORRIGE
---------------------------------
1. Le biais favori-outsider : le marche surcote legerement les outsiders. Sans
   correction, tous les favoris paraitraient "fiables". On recalibre donc les
   probabilites par tranche de 5 points, puis par pays.

2. Le hasard : sur 1 000 equipes, une cinquantaine depassent |z| > 2 sans
   aucune propension reelle. Un classement seul ne prouve rien. On mesure donc
   la DISPERSION des z (ecart-type 1 sous l'hypothese nulle) et l'ecart reel
   entre equipes qu'elle implique, en points par match.

3. La persistance : meme reelle, une propension passee n'a de valeur que si
   elle se prolonge. On correle le residu d'une saison avec celui de la
   suivante, equipe par equipe.

4. La rentabilite : le seul juge final. Chaque saison est jouee avec des
   classements calcules UNIQUEMENT sur les saisons precedentes, puis comparee
   au meme pari place sur tous les matchs (le temoin).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

from .history import HistMatch
from .odds import implied

Role = Literal["favorite", "underdog"]
ROLES: tuple[Role, ...] = ("favorite", "underdog")

BIN_WIDTH = 0.05
MIN_BIN = 200          # en dessous, la tranche n'est pas corrigee
MIN_COUNTRY = 300


@dataclass(frozen=True)
class Row:
    """Une equipe, dans un role, sur un match."""
    team: tuple[str, str]       # (pays, nom) : deux "Nacional" ne se confondent pas
    role: Role
    season: int
    date: str
    p: float                    # probabilite de l'evenement selon le marche
    hit: int                    # 1 si l'evenement a eu lieu
    o_event: float              # cote de l'evenement (double chance synthetique si besoin)
    o_against: float            # cote du contraire


def _dc(a: float, b: float) -> float:
    """Cote double chance equivalente a deux cotes simples : 1 / (1/a + 1/b)."""
    return a * b / (a + b)


def rows_of(matches: Iterable[HistMatch]) -> list[Row]:
    out: list[Row] = []
    for m in matches:
        q1, qx, q2, _marge = implied(m.o1, m.ox, m.o2)
        # Meme regle que pour statarea : argmax de 1/X/2, le nul en tete ou une
        # egalite stricte ne designent aucun favori.
        if qx >= max(q1, q2) or q1 == q2:
            continue
        fav_home = q1 > q2
        fav, dog = (m.home, m.away) if fav_home else (m.away, m.home)
        q_fav, q_dog = (q1, q2) if fav_home else (q2, q1)
        o_fav, o_dog = (m.o1, m.o2) if fav_home else (m.o2, m.o1)
        fav_won = m.outcome == ("1" if fav_home else "2")
        dog_won = m.outcome == ("2" if fav_home else "1")
        out.append(Row((m.country, fav), "favorite", m.season, m.date,
                       1 - q_fav, int(not fav_won), _dc(m.ox, o_dog), o_fav))
        out.append(Row((m.country, dog), "underdog", m.season, m.date,
                       q_dog, int(dog_won), o_dog, _dc(o_fav, m.ox)))
    return out


# --------------------------------------------------------------------------
# Calibration

def _bin(p: float) -> int:
    return min(int(p / BIN_WIDTH), int(1 / BIN_WIDTH) - 1)


@dataclass
class Calibration:
    by_bin: dict = field(default_factory=dict)        # (role, bin) -> correction
    by_country: dict = field(default_factory=dict)    # (role, pays) -> correction

    def __call__(self, r: Row) -> float:
        p = r.p + self.by_bin.get((r.role, _bin(r.p)), 0.0) \
                + self.by_country.get((r.role, r.team[0]), 0.0)
        return min(max(p, 0.005), 0.995)


def fit_calibration(rows: Iterable[Row]) -> Calibration:
    rows = list(rows)
    cal = Calibration()
    acc: dict = defaultdict(lambda: [0, 0.0])
    for r in rows:
        a = acc[(r.role, _bin(r.p))]
        a[0] += 1
        a[1] += r.hit - r.p
    cal.by_bin = {k: s / n for k, (n, s) in acc.items() if n >= MIN_BIN}

    acc = defaultdict(lambda: [0, 0.0])
    for r in rows:
        a = acc[(r.role, r.team[0])]
        a[0] += 1
        a[1] += r.hit - cal(r)
    cal.by_country = {k: s / n for k, (n, s) in acc.items() if n >= MIN_COUNTRY}
    return cal


# --------------------------------------------------------------------------
# Scores par equipe

@dataclass
class TeamScore:
    team: tuple[str, str]
    role: Role
    n: int = 0
    observed: int = 0
    expected: float = 0.0
    variance: float = 0.0

    @property
    def residual(self) -> float:
        return self.observed - self.expected

    @property
    def per_match(self) -> float:
        """Ecart par match, en points de pourcentage."""
        return self.residual / self.n * 100 if self.n else 0.0

    @property
    def z(self) -> Optional[float]:
        return self.residual / math.sqrt(self.variance) if self.variance > 0 else None

    @property
    def label(self) -> str:
        return f"{self.team[1]} ({self.team[0]})"


def team_scores(rows: Iterable[Row], cal: Calibration, role: Role,
                min_n: int = 1) -> dict[tuple[str, str], TeamScore]:
    out: dict[tuple[str, str], TeamScore] = {}
    for r in rows:
        if r.role != role:
            continue
        p = cal(r)
        s = out.get(r.team)
        if s is None:
            s = out[r.team] = TeamScore(r.team, role)
        s.n += 1
        s.observed += r.hit
        s.expected += p
        s.variance += p * (1 - p)
    return {k: v for k, v in out.items() if v.n >= min_n}


def dispersion(scores: Iterable[TeamScore]) -> dict:
    """Ecart-type des z (1 sous l'hypothese nulle) et ecart reel entre equipes.

    tau est l'ecart-type de la vraie propension par match, estime par la
    methode des moments : variance observee des residus par match, moins la
    part due au hasard.
    """
    scores = [s for s in scores if s.z is not None]
    k = len(scores)
    if k < 3:
        return {"teams": k, "sd": None, "se": None, "tau": None, "verdict": "echantillon insuffisant"}
    zs = [s.z for s in scores]
    mz = sum(zs) / k
    sd = math.sqrt(sum((z - mz) ** 2 for z in zs) / (k - 1))
    se = 1 / math.sqrt(2 * (k - 1))

    rs = [s.residual / s.n for s in scores]
    mr = sum(rs) / k
    noise = sum(s.variance / s.n ** 2 for s in scores) / k
    tau2 = sum((r - mr) ** 2 for r in rs) / (k - 1) - noise

    if sd > 1 + 2 * se:
        verdict = "surdispersion : les equipes different reellement"
    elif sd < 1 - 2 * se:
        verdict = "sous-dispersion (inattendu)"
    else:
        verdict = "compatible avec le hasard"
    return {"teams": k, "sd": sd, "se": se, "tau": math.sqrt(tau2) if tau2 > 0 else 0.0,
            "verdict": verdict, "beyond_2": sum(1 for z in zs if abs(z) > 2),
            "expected_beyond_2": 0.0455 * k}


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def persistence(rows: list[Row], cal: Calibration, role: Role, min_n: int = 10) -> dict:
    """Le residu d'une equipe sur une saison annonce-t-il celui de la suivante ?

    Correlation des z entre la saison t et la saison t+1, sur toutes les
    paires (equipe, t) ou l'equipe a au moins `min_n` matchs dans le role les
    deux saisons. 0 = aucune persistance : le passe ne dit rien.
    """
    by_season: dict[int, list[Row]] = defaultdict(list)
    for r in rows:
        if r.role == role:
            by_season[r.season].append(r)
    scores = {s: team_scores(rs, cal, role, min_n) for s, rs in by_season.items()}
    xs, ys = [], []
    for season, teams in scores.items():
        nxt = scores.get(season + 1)
        if not nxt:
            continue
        for team, a in teams.items():
            b = nxt.get(team)
            if b is not None and a.z is not None and b.z is not None:
                xs.append(a.z)
                ys.append(b.z)
    r = _pearson(xs, ys)
    se = 1 / math.sqrt(len(xs) - 3) if len(xs) > 3 else None
    return {"pairs": len(xs), "r": r, "se": se,
            "significant": r is not None and se is not None and abs(r) > 2 * se}


# --------------------------------------------------------------------------
# Rentabilite hors echantillon

@dataclass
class Ledger:
    n: int = 0
    total: float = 0.0
    total_sq: float = 0.0

    def add(self, profit: float) -> None:
        self.n += 1
        self.total += profit
        self.total_sq += profit * profit

    @property
    def roi(self) -> Optional[float]:
        return self.total / self.n if self.n else None

    @property
    def se(self) -> Optional[float]:
        if self.n < 2:
            return None
        mean = self.total / self.n
        var = (self.total_sq - self.n * mean * mean) / (self.n - 1)
        return math.sqrt(max(var, 0.0) / self.n)


# Les quatre strategies, chacune avec son temoin (le meme pari sur tous les
# matchs du role). (role, signe du z retenu, parier sur l'evenement ?)
STRATEGIES = {
    "contre les favoris fragiles": ("favorite", +1, True),     # nul ou outsider
    "avec les favoris fideles": ("favorite", -1, False),       # victoire du favori
    "avec les outsiders createurs": ("underdog", +1, True),    # victoire de l'outsider
    "contre les outsiders inoffensifs": ("underdog", -1, False),  # favori ou nul
}

# Parametres fixes AVANT le premier calcul : ni le seuil, ni la fenetre, ni le
# minimum ne sont ajustes sur les resultats.
BT_WINDOW = 3          # saisons precedentes servant au classement
BT_MIN_N = 20          # matchs minimum dans le role sur cette fenetre
BT_Z = 2.0             # seuil de selection


def backtest(rows: list[Row], first_season: int, last_season: Optional[int] = None,
             window: int = BT_WINDOW, min_n: int = BT_MIN_N, z_min: float = BT_Z) -> dict:
    """Chaque saison S est jouee avec des classements calcules sur S-window..S-1.

    La calibration est elle aussi ajustee sur les seules saisons anterieures :
    aucune information de la saison jouee n'entre dans la selection.
    """
    seasons = sorted({r.season for r in rows})
    last_season = seasons[-1] if last_season is None else last_season
    result = {name: {"strategy": Ledger(), "witness": Ledger(), "teams": 0} for name in STRATEGIES}
    by_season: dict[int, list[Row]] = defaultdict(list)
    for r in rows:
        by_season[r.season].append(r)

    for season in seasons:
        if season < first_season or season > last_season:
            continue
        past = [r for s in range(season - window, season) for r in by_season.get(s, [])]
        if not past:
            continue
        cal = fit_calibration([r for r in rows if r.season < season])
        scores = {role: team_scores(past, cal, role, min_n) for role in ROLES}
        for name, (role, sign, on_event) in STRATEGIES.items():
            chosen = {t for t, s in scores[role].items()
                      if s.z is not None and sign * s.z >= z_min}
            result[name]["teams"] += len(chosen)
            for r in by_season[season]:
                if r.role != role:
                    continue
                odds, won = (r.o_event, r.hit) if on_event else (r.o_against, 1 - r.hit)
                profit = odds - 1 if won else -1.0
                result[name]["witness"].add(profit)
                if r.team in chosen:
                    result[name]["strategy"].add(profit)
    return result
