"""Telechargement des pages de predictions et archivage du HTML brut.

statarea ne garde pas l'historique en ligne (fenetre glissante de quelques
jours autour d'aujourd'hui). Le HTML brut est donc archive dans
data/raw/YYYY-MM-DD.html AVANT tout parsing : on peut re-parser tout
l'historique sans re-telecharger quand les regles evoluent.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import requests

from .parser import Match, ParseError, parse_page

BASE_URL = "https://www.statarea.com/predictions/date/{date}/competition"
CONTROLLER_URL = "https://www.statarea.com/actions/controller/"
RAW_DIR = Path("data/raw")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
DEFAULT_DELAY = 3.0


class FetchError(RuntimeError):
    pass


def day_dir(day: str, raw_dir: Path | str = RAW_DIR) -> Path:
    return Path(raw_dir) / day


def snapshot_path(day: str, when: Optional[datetime] = None,
                  raw_dir: Path | str = RAW_DIR) -> Path:
    """Chemin d'un instantane : data/raw/YYYY-MM-DD/YYYYmmddTHHMMSSZ.html.gz.

    Un fichier par telechargement, jamais ecrase. statarea remanie ses pages
    passees en continu : entre deux collectes d'une meme date, des matchs
    disparaissent et d'autres apparaissent. Ecraser l'archive de la veille
    detruirait donc definitivement les matchs que la nouvelle version ne
    contient plus.
    """
    when = when or datetime.now(timezone.utc)
    return day_dir(day, raw_dir) / f"{when.strftime('%Y%m%dT%H%M%SZ')}.html.gz"


def snapshots(day: str, raw_dir: Path | str = RAW_DIR) -> list[Path]:
    """Instantanes d'une date, du plus ancien au plus recent.

    L'ancien format plat (data/raw/YYYY-MM-DD.html[.gz]) est encore lu et
    considere comme le plus ancien.
    """
    found = []
    for legacy in (Path(raw_dir) / f"{day}.html.gz", Path(raw_dir) / f"{day}.html"):
        if legacy.exists():
            found.append(legacy)
    found += sorted(day_dir(day, raw_dir).glob("*.html.gz"))
    return found


def archived_days(raw_dir: Path | str = RAW_DIR) -> list[str]:
    root = Path(raw_dir)
    days = {d.name for d in root.glob("[0-9]" * 4 + "-*") if d.is_dir()}
    days |= {f.name.split(".")[0] for f in root.glob("*.html*")}
    return sorted(days)


def read_archive(path: Path) -> str:
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    return path.read_text(encoding="utf-8")


def _digest(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def daterange(start: str, end: str) -> Iterator[str]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        d0, d1 = d1, d0
    while d0 <= d1:
        yield d0.isoformat()
        d0 += timedelta(days=1)


def make_session() -> requests.Session:
    """Session HTTP avec le fuseau horaire FIGE EN UTC.

    statarea deduit le fuseau de l'adresse IP et affiche les heures dedans.
    Une collecte lancee depuis une autre machine voit donc les memes matchs a
    des heures differentes, et ceux qui encadrent minuit changent de DATE.
    Mesure sur nos propres archives : quatre fuseaux distincts (GMT-4, GMT-7,
    GMT 0, GMT+1) selon la machine, et 5,9 % des matchs enregistres sous deux
    dates, donc comptes deux fois dans les statistiques.

    Le reglage est garde en session par le cookie statareacustomtimezone.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    pin_timezone(session)
    return session


def pin_timezone(session: requests.Session, timezone: str = "GMT 0") -> bool:
    """Fige le fuseau de la session. Retourne False si statarea a refuse."""
    try:
        r = session.post(
            CONTROLLER_URL,
            data={"object": json.dumps({"action": "setCustomTimezone", "timezone": timezone})},
            headers={"X-Requested-With": "XMLHttpRequest"}, timeout=30,
        )
        return r.ok and "success" in r.text.lower()
    except requests.RequestException:
        return False


def fetch_day(
    day: str,
    session: Optional[requests.Session] = None,
    timeout: float = 30.0,
    retries: int = 3,
) -> str:
    """Telecharge la page d'une date. Leve FetchError apres epuisement des essais."""
    session = session or make_session()
    url = BASE_URL.format(date=day)
    last: Optional[Exception] = None

    for attempt in range(retries):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:  # reseau, 5xx, timeout
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    raise FetchError(f"{day}: echec du telechargement ({last})")


def archive(day: str, html: str, raw_dir: Path | str = RAW_DIR,
            when: Optional[datetime] = None) -> Optional[Path]:
    """Ecrit un nouvel instantane. Retourne None si la page n'a pas bouge.

    Dedoublonner sur le contenu evite d'accumuler des copies identiques : une
    date figee est collectee sept fois sans rien ajouter.
    """
    existing = snapshots(day, raw_dir)
    if existing and _digest(read_archive(existing[-1])) == _digest(html):
        return None

    path = snapshot_path(day, when, raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(html.encode("utf-8"), compresslevel=9))
    return path


def collect_day(
    day: str,
    session: Optional[requests.Session] = None,
    raw_dir: Path | str = RAW_DIR,
    use_cache: bool = False,
) -> list[Match]:
    """Archive puis parse une date. `use_cache` relit l'archive si elle existe."""
    cached = snapshots(day, raw_dir) if use_cache else None
    if cached:
        html = read_archive(cached[-1])
    else:
        html = fetch_day(day, session=session)
        archive(day, html, raw_dir)
    return parse_page(html, expected_date=day)


def reparse_archive(raw_dir: Path | str = RAW_DIR) -> Iterator[tuple[str, list[Match]]]:
    """Re-parse toutes les archives locales, sans aucune requete reseau.

    Les instantanes d'une meme date sont rejoues du plus ancien au plus
    recent : l'upsert accumule alors les matchs que statarea a retires entre
    deux collectes, et le pronostic retenu est bien le plus ancien connu.
    """
    for day in archived_days(raw_dir):
        for path in snapshots(day, raw_dir):
            try:
                yield day, parse_page(read_archive(path), expected_date=day)
            except ParseError as exc:
                yield day, []
                print(f"  ! {day} ({path.name}): {exc}")


def sleep_between(delay: float = DEFAULT_DELAY) -> None:
    if delay > 0:
        time.sleep(delay)
