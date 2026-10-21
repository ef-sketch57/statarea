"""Historique de football-data.co.uk : resultats et cotes depuis 2005.

POURQUOI
--------
La question de depart - quelles equipes suivent fidelement leur probabilite,
lesquelles creent ou subissent la surprise - demande des centaines de matchs
par equipe. statarea ne sert qu'une fenetre de +/-3 jours : un mois de
collecte donne une mediane de 3 matchs par equipe, ce qui ne permet aucune
conclusion. football-data.co.uk publie, saison par saison, les resultats et
les cotes de 1X2 des principaux championnats depuis le milieu des annees 90.

On y prend la probabilite du MARCHE (cote de cloture quand elle existe), la
reference la plus exigeante : une propension par equipe qui survit a ce test
est une propension que les bookmakers n'integrent pas.

ARCHIVAGE
---------
Comme pour statarea, les fichiers bruts sont archives tels quels (gzip, sans
horodatage interne pour qu'un fichier inchange ne produise aucun diff) avant
toute lecture. Le telechargement tourne sur GitHub Actions
(`.github/workflows/historique.yml`) : le domaine n'est pas joignable depuis
toutes les machines.
"""

from __future__ import annotations

import csv
import gzip
import io
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

HISTORY_DIR = Path("data/history")
BASE_URL = "https://www.football-data.co.uk"

# Championnats "principaux" : un fichier par saison, mmz4281/{SSYY}/{code}.csv
LEAGUES = {
    "E0": "England", "E1": "England", "E2": "England", "E3": "England", "EC": "England",
    "SC0": "Scotland", "SC1": "Scotland", "SC2": "Scotland", "SC3": "Scotland",
    "D1": "Germany", "D2": "Germany",
    "I1": "Italy", "I2": "Italy",
    "SP1": "Spain", "SP2": "Spain",
    "F1": "France", "F2": "France",
    "N1": "Netherlands", "B1": "Belgium", "P1": "Portugal",
    "T1": "Turkey", "G1": "Greece",
}

# Championnats "supplementaires" : un seul fichier, toutes saisons, new/{code}.csv
EXTRA = {
    "ARG": "Argentina", "AUT": "Austria", "BRA": "Brazil", "CHN": "China",
    "DNK": "Denmark", "FIN": "Finland", "IRL": "Ireland", "JPN": "Japan",
    "MEX": "Mexico", "NOR": "Norway", "POL": "Poland", "ROU": "Romania",
    "RUS": "Russia", "SWE": "Sweden", "SWZ": "Switzerland", "USA": "USA",
}

FIRST_SEASON = 2005

# Ordre de preference des cotes : la cloture d'abord (c'est elle qui integre
# toute l'information disponible au coup d'envoi), Pinnacle avant la moyenne
# (marge la plus faible, la plus proche d'une probabilite).
ODDS_COLUMNS = [
    ("pinnacle_cloture", "PSCH", "PSCD", "PSCA"),
    ("moyenne_cloture", "AvgCH", "AvgCD", "AvgCA"),
    ("pinnacle", "PSH", "PSD", "PSA"),
    ("pinnacle", "PH", "PD", "PA"),
    ("moyenne", "AvgH", "AvgD", "AvgA"),
    ("moyenne", "BbAvH", "BbAvD", "BbAvA"),
    ("b365_cloture", "B365CH", "B365CD", "B365CA"),
    ("b365", "B365H", "B365D", "B365A"),
    ("bwin", "BWH", "BWD", "BWA"),
    ("william_hill", "WHH", "WHD", "WHA"),
]


@dataclass(frozen=True)
class HistMatch:
    country: str
    league: str
    season: int          # annee de debut de saison (2019 pour 2019/20)
    date: str            # AAAA-MM-JJ
    home: str
    away: str
    hg: int
    ag: int
    o1: float
    ox: float
    o2: float
    source: str          # colonne de cotes retenue (cf. ODDS_COLUMNS)

    @property
    def outcome(self) -> str:
        return "1" if self.hg > self.ag else ("2" if self.hg < self.ag else "X")


# --------------------------------------------------------------------------
# Telechargement

def season_code(start_year: int) -> str:
    """2019 -> '1920', le format des repertoires de football-data."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def current_season(today: Optional[datetime] = None) -> int:
    """Saison en cours : elle commence en juillet."""
    today = today or datetime.utcnow()
    return today.year if today.month >= 7 else today.year - 1


def targets(first: int = FIRST_SEASON, last: Optional[int] = None) -> list[tuple[str, Path]]:
    """(url, chemin local) de chaque fichier a recuperer."""
    last = current_season() if last is None else last
    out = []
    for year in range(first, last + 1):
        code = season_code(year)
        for league in LEAGUES:
            out.append((f"{BASE_URL}/mmz4281/{code}/{league}.csv",
                        Path("mmz4281") / code / f"{league}.csv.gz"))
    for league in EXTRA:
        out.append((f"{BASE_URL}/new/{league}.csv", Path("new") / f"{league}.csv.gz"))
    return out


def store(content: bytes, path: Path) -> bool:
    """Archive gzip deterministe. Renvoie False si le fichier est identique."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(content)
    data = buf.getvalue()
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def download(dest: Path | str = HISTORY_DIR, first: int = FIRST_SEASON,
             last: Optional[int] = None, delay: float = 1.0, session=None) -> dict:
    """Recupere tous les fichiers. Un 404 n'est pas une erreur : toutes les
    divisions n'existent pas toutes les saisons."""
    import requests

    dest = Path(dest)
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "statarea-research/1.0 (historique, 1 requete/s)")
    stats = {"written": 0, "unchanged": 0, "missing": 0, "failed": 0}
    for i, (url, rel) in enumerate(targets(first, last)):
        if i:
            time.sleep(delay)
        content, status = None, None
        for attempt in range(3):
            try:
                r = session.get(url, timeout=30)
            except requests.RequestException as exc:
                print(f"  erreur {url} : {exc}", file=sys.stderr)
                time.sleep(2 ** attempt * 2)
                continue
            status = r.status_code
            if status == 404:
                break
            if r.ok:
                content = r.content
                break
            time.sleep(2 ** attempt * 2)
        if content is None:
            stats["missing" if status == 404 else "failed"] += 1
            if status != 404:
                print(f"  echec {url} (statut {status})", file=sys.stderr)
            continue
        # Une page HTML a la place d'un CSV (redirection, page d'erreur) ne
        # doit pas entrer dans l'archive.
        if content.lstrip()[:1] == b"<":
            stats["failed"] += 1
            print(f"  reponse non CSV : {url}", file=sys.stderr)
            continue
        stats["written" if store(content, dest / rel) else "unchanged"] += 1
    return stats


# --------------------------------------------------------------------------
# Lecture

def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _date(value: str) -> Optional[str]:
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _odds(row: dict) -> Optional[tuple[float, float, float, str]]:
    for source, h, d, a in ODDS_COLUMNS:
        try:
            o = (float(row[h]), float(row[d]), float(row[a]))
        except (KeyError, TypeError, ValueError):
            continue
        if all(x > 1.0 for x in o):
            return (*o, source)
    return None


def _season_from_label(label: str, date: str) -> int:
    """'2012/2013' -> 2012 ; '2012' -> 2012 ; a defaut, deduite de la date."""
    label = (label or "").strip()
    if label[:4].isdigit():
        return int(label[:4])
    return current_season(datetime.strptime(date, "%Y-%m-%d"))


def parse_csv(raw: bytes, league: str, country: Optional[str] = None,
              season: Optional[int] = None) -> list[HistMatch]:
    """Lit un fichier football-data. Les lignes incompletes sont ignorees :
    un match sans score ou sans cote ne sert a rien ici."""
    text = _decode(raw)
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        home = row.get("HomeTeam") or row.get("Home") or row.get("HT")
        away = row.get("AwayTeam") or row.get("Away") or row.get("AT")
        hg = _int(row.get("FTHG", row.get("HG")))
        ag = _int(row.get("FTAG", row.get("AG")))
        date = _date(row.get("Date", ""))
        odds = _odds(row)
        if not (home and away and date and odds) or hg is None or ag is None:
            continue
        out.append(HistMatch(
            country=row.get("Country") or country or "",
            league=league,
            season=season if season is not None else _season_from_label(row.get("Season", ""), date),
            date=date, home=home, away=away, hg=hg, ag=ag,
            o1=odds[0], ox=odds[1], o2=odds[2], source=odds[3]))
    return out


def load(history_dir: Path | str = HISTORY_DIR) -> list[HistMatch]:
    """Tous les matchs archives, tries par date."""
    root = Path(history_dir)
    matches: list[HistMatch] = []
    for path in sorted(root.glob("mmz4281/*/*.csv.gz")):
        league = path.name[: -len(".csv.gz")]
        code = path.parent.name
        season = 2000 + int(code[:2]) if int(code[:2]) < 90 else 1900 + int(code[:2])
        matches.extend(parse_csv(gzip.decompress(path.read_bytes()), league,
                                 LEAGUES.get(league, league), season))
    for path in sorted(root.glob("new/*.csv.gz")):
        league = path.name[: -len(".csv.gz")]
        matches.extend(parse_csv(gzip.decompress(path.read_bytes()), league,
                                 EXTRA.get(league, league)))
    # Un meme match ne doit compter qu'une fois.
    unique = {(m.country, m.date, m.home, m.away): m for m in matches}
    return sorted(unique.values(), key=lambda m: (m.date, m.country, m.home))


def summary(matches: Iterable[HistMatch]) -> dict:
    matches = list(matches)
    by_source: dict[str, int] = {}
    for m in matches:
        by_source[m.source] = by_source.get(m.source, 0) + 1
    return {
        "matches": len(matches),
        "countries": len({m.country for m in matches}),
        "teams": len({(m.country, t) for m in matches for t in (m.home, m.away)}),
        "seasons": sorted({m.season for m in matches}),
        "first": matches[0].date if matches else None,
        "last": matches[-1].date if matches else None,
        "sources": by_source,
    }
