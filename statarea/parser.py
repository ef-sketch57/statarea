"""Parsing d'une page /predictions/date/YYYY-MM-DD/competition de statarea.com.

Structure HTML observee (rendue cote serveur, pas d'AJAX) :

    div.competition[id]
      div.header
        img[alt="<Pays> country flag"]     -> pays
        div.name  "ITALY - SERIE A"        -> pays + competition
      div.body
        div.match[id]                      -> id interne statarea
          div.date            "12:00"      -> heure de coup d'envoi (fuseau de la page)
          div.teams
            div.ownheader     "2026-09-19 12:00"
            div.hostteam  .goals (a si joue, div "-" sinon) + .name
            div.guestteam .goals + .htres ("HT 2:0") + .name
          div.tip div.value   "1" | "X" | "2" | "1X" | "X2" | "12"
          div.coefrow
            div.ownheader > div.coefbox    en-tetes : 1, X, 2, H1, HX, H2, 1.5, 2.5, 3.5, BTS, OTS
            div.coefbox > div.value        valeurs, dans le MEME ordre

Les trois premieres valeurs de coefrow sont donc, de gauche a droite, 1 / X / 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional

from bs4 import BeautifulSoup

# En-tetes attendus des trois premieres colonnes de pourcentages.
EXPECTED_HEAD = ("1", "X", "2")

PREDICTIONS = {"1", "X", "2", "1X", "X2", "12"}


class ParseError(RuntimeError):
    """La page ne correspond plus a la structure attendue."""


@dataclass
class Match:
    match_id: str
    sa_id: Optional[str]
    match_date: str
    kickoff: Optional[str]
    country: Optional[str]
    competition: Optional[str]
    home: str
    away: str
    prediction: Optional[str]
    p1: Optional[int]
    px: Optional[int]
    p2: Optional[int]
    home_score: Optional[int]
    away_score: Optional[int]
    outcome: Optional[str]
    favorite: Optional[str]
    fav_margin: Optional[int]
    surprise: Optional[int]
    upset: Optional[int]
    prediction_hit: Optional[int]

    def as_dict(self) -> dict:
        return asdict(self)


def _text(node) -> Optional[str]:
    return node.get_text(strip=True) if node is not None else None


def _int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    return int(value) if re.fullmatch(r"-?\d+", value) else None


def page_date(soup: BeautifulSoup) -> Optional[str]:
    """Date reellement servie par la page (div#cdate).

    statarea sert silencieusement la page du jour quand on demande une date
    hors de sa fenetre : il faut donc toujours verifier ce que la page contient.
    """
    node = soup.find(id="cdate")
    text = _text(node)
    return text if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _split_country_competition(header) -> tuple[Optional[str], Optional[str]]:
    flag = header.find("img") if header else None
    country = None
    if flag is not None:
        country = re.sub(r"\s*country flag\s*$", "", flag.get("alt", "")).strip() or None

    full = _text(header.find("div", class_="name")) if header else None
    competition = full
    if full and " - " in full:
        head, tail = full.split(" - ", 1)
        competition = tail.strip()
        if not country:
            country = head.strip()
    return country, competition


def _percentages(match) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Retourne (p1, px, p2) apres avoir verifie l'ordre des colonnes 1 / X / 2."""
    coefrow = match.find("div", class_="coefrow")
    if coefrow is None:
        return None, None, None

    head = coefrow.find("div", class_="ownheader")
    if head is not None:
        labels = tuple(_text(c) for c in head.select("div.coefbox")[:3])
        if labels != EXPECTED_HEAD:
            raise ParseError(
                f"ordre des colonnes inattendu : {labels!r}, attendu {EXPECTED_HEAD!r}"
            )

    values = [_int(_text(v)) for v in coefrow.select("div.coefbox > div.value")]
    values += [None] * (3 - len(values))
    return values[0], values[1], values[2]


def _score(side) -> Optional[int]:
    """Buts d'une equipe. `-` (match non joue) -> None.

    Le noeud est un <div class='goals'>-</div> avant le match et un
    <a class='goals'>2</a> une fois le match joue.
    """
    node = side.find(class_="goals")
    return _int(_text(node))


def compute_outcome(home_score: Optional[int], away_score: Optional[int]) -> Optional[str]:
    if home_score is None or away_score is None:
        return None
    if home_score > away_score:
        return "1"
    if home_score < away_score:
        return "2"
    return "X"


def compute_favorite(
    p1: Optional[int], px: Optional[int], p2: Optional[int]
) -> tuple[Optional[str], Optional[int]]:
    """Favori = colonne 1 ou 2 ayant le pourcentage strictement le plus eleve.

    - max en colonne 1 -> 'home'
    - max en colonne 2 -> 'away'
    - max en colonne X -> pas de favori (None) : le match est stocke mais exclu
      des indices de surprise.
    - egalite au sommet (34/34/32, 40/20/40...) -> pas de favori non plus :
      aucune des deux equipes n'est designee, la designer serait arbitraire.

    fav_margin = pourcentage du favori moins le deuxieme pourcentage (toutes
    colonnes confondues, X inclus).
    """
    if p1 is None or px is None or p2 is None:
        return None, None

    values = [p1, px, p2]
    top = max(values)
    if values.count(top) > 1:
        return None, None
    if top == px:
        return None, None

    second = sorted(values, reverse=True)[1]
    return ("home" if top == p1 else "away"), top - second


def compute_flags(
    favorite: Optional[str], outcome: Optional[str]
) -> tuple[Optional[int], Optional[int]]:
    """(surprise, upset).

    surprise : le favori n'a pas gagne (nul compris).
    upset    : l'outsider a gagne (le nul n'est pas un upset).
    """
    if favorite is None or outcome is None:
        return None, None
    if favorite == "home":
        return int(outcome != "1"), int(outcome == "2")
    return int(outcome != "2"), int(outcome == "1")


def compute_prediction_hit(prediction: Optional[str], outcome: Optional[str]) -> Optional[int]:
    if not prediction or outcome is None or prediction not in PREDICTIONS:
        return None
    return int(outcome in prediction)


def build_match_id(match_date: str, home: str, away: str,
                   sa_id: Optional[str] = None) -> str:
    """Identifiant stable d'un match.

    sa_id est l'identifiant interne de statarea : c'est le seul vraiment
    stable. La cle date|domicile|exterieur ne l'est pas, parce que statarea
    affiche les heures dans le fuseau deduit de l'IP : un match a 21:00 GMT
    devient 02:00 le lendemain vu depuis GMT+5, et se retrouve enregistre sous
    deux dates. Mesure sur nos archives avant correction : 5,9 % des matchs
    en double, comptes deux fois dans les statistiques.

    On garde la cle composee en repli, pour les rares lignes sans sa_id.
    """
    return sa_id if sa_id else f"{match_date}|{home}|{away}"


def parse_page(html: str, expected_date: Optional[str] = None) -> list[Match]:
    """Parse une page de predictions et retourne les matchs enrichis.

    `expected_date` : si fourni, on verifie que la page servie correspond bien
    a cette date (statarea retombe sur le jour courant pour une date hors
    fenetre, sans erreur HTTP).
    """
    soup = BeautifulSoup(html, "html.parser")

    served = page_date(soup)
    if expected_date and served and served != expected_date:
        raise ParseError(
            f"statarea a servi la page du {served} alors que {expected_date} etait demande "
            "(date hors de la fenetre disponible)"
        )
    default_date = served or expected_date

    matches: list[Match] = []
    for comp in soup.select("div.competition"):
        country, competition = _split_country_competition(comp.find("div", class_="header"))

        for node in comp.select("div.match"):
            teams = node.find("div", class_="teams")
            if teams is None:
                continue
            host = teams.find("div", class_="hostteam")
            guest = teams.find("div", class_="guestteam")
            if host is None or guest is None:
                continue

            home = _text(host.find("div", class_="name"))
            away = _text(guest.find("div", class_="name"))
            if not home or not away:
                continue

            stamp = _text(teams.find("div", class_="ownheader")) or ""
            m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", stamp)
            match_date = m.group(1) if m else default_date
            kickoff = m.group(2) if m else _text(node.find("div", class_="date"))
            if not match_date:
                continue

            tip = node.find("div", class_="tip")
            prediction = _text(tip.find("div", class_="value")) if tip else None
            if prediction not in PREDICTIONS:
                prediction = None

            p1, px, p2 = _percentages(node)
            home_score, away_score = _score(host), _score(guest)

            outcome = compute_outcome(home_score, away_score)
            favorite, fav_margin = compute_favorite(p1, px, p2)
            surprise, upset = compute_flags(favorite, outcome)

            matches.append(
                Match(
                    match_id=build_match_id(match_date, home, away, node.get("id")),
                    sa_id=node.get("id"),
                    match_date=match_date,
                    kickoff=kickoff,
                    country=country,
                    competition=competition,
                    home=home,
                    away=away,
                    prediction=prediction,
                    p1=p1,
                    px=px,
                    p2=p2,
                    home_score=home_score,
                    away_score=away_score,
                    outcome=outcome,
                    favorite=favorite,
                    fav_margin=fav_margin,
                    surprise=surprise,
                    upset=upset,
                    prediction_hit=compute_prediction_hit(prediction, outcome),
                )
            )

    return matches
