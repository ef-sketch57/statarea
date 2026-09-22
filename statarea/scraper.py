"""Telechargement des pages de predictions et archivage du HTML brut.

statarea ne garde pas l'historique en ligne (fenetre glissante de quelques
jours autour d'aujourd'hui). Le HTML brut est donc archive dans
data/raw/YYYY-MM-DD.html AVANT tout parsing : on peut re-parser tout
l'historique sans re-telecharger quand les regles evoluent.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Optional

import requests

from .parser import Match, ParseError, parse_page

BASE_URL = "https://www.statarea.com/predictions/date/{date}/competition"
RAW_DIR = Path("data/raw")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
DEFAULT_DELAY = 3.0


class FetchError(RuntimeError):
    pass


def raw_path(day: str, raw_dir: Path | str = RAW_DIR) -> Path:
    return Path(raw_dir) / f"{day}.html"


def daterange(start: str, end: str) -> Iterator[str]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        d0, d1 = d1, d0
    while d0 <= d1:
        yield d0.isoformat()
        d0 += timedelta(days=1)


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


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


def archive(day: str, html: str, raw_dir: Path | str = RAW_DIR) -> Path:
    path = raw_path(day, raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def collect_day(
    day: str,
    session: Optional[requests.Session] = None,
    raw_dir: Path | str = RAW_DIR,
    use_cache: bool = False,
) -> list[Match]:
    """Archive puis parse une date. `use_cache` relit l'archive si elle existe."""
    path = raw_path(day, raw_dir)
    if use_cache and path.exists():
        html = path.read_text(encoding="utf-8")
    else:
        html = fetch_day(day, session=session)
        archive(day, html, raw_dir)
    return parse_page(html, expected_date=day)


def reparse_archive(raw_dir: Path | str = RAW_DIR) -> Iterator[tuple[str, list[Match]]]:
    """Re-parse toutes les archives locales, sans aucune requete reseau."""
    for path in sorted(Path(raw_dir).glob("*.html")):
        day = path.stem
        try:
            yield day, parse_page(path.read_text(encoding="utf-8"), expected_date=day)
        except ParseError as exc:
            yield day, []
            print(f"  ! {day}: {exc}")


def sleep_between(delay: float = DEFAULT_DELAY) -> None:
    if delay > 0:
        time.sleep(delay)
