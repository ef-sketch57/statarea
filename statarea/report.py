"""Rapport lisible en ligne de commande."""

from __future__ import annotations

import sqlite3
from typing import Optional

from . import db, stats


def _pct(value: Optional[float]) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def _rule(width: int = 96) -> str:
    return "-" * width


def _table(title: str, rows: list[stats.TeamStats], role: str) -> list[str]:
    out = [title, _rule()]
    if not rows:
        out += ["  (aucune equipe n'atteint le seuil de matchs demande)", ""]
        return out

    if role == "fav":
        out.append(f"  {'#':>3}  {'equipe':30} {'matchs fav':>10} {'surprises':>10} "
                   f"{'subie':>7} {'fiabilite':>10}")
        out.append(_rule())
        for i, s in enumerate(rows, 1):
            out.append(f"  {i:>3}  {s.team[:30]:30} {s.fav_matches:>10} {s.fav_surprised:>10} "
                       f"{_pct(s.surprised_rate):>7} {_pct(s.reliability):>10}")
    else:
        out.append(f"  {'#':>3}  {'equipe':30} {'matchs out':>10} {'surprises':>10} {'taux':>7}")
        out.append(_rule())
        for i, s in enumerate(rows, 1):
            out.append(f"  {i:>3}  {s.team[:30]:30} {s.dog_matches:>10} {s.dog_upsets:>10} "
                       f"{_pct(s.upset_rate):>7}")
    out.append("")
    return out


def build_report(
    conn: sqlite3.Connection,
    min_fav: int = 5,
    min_dog: int = 5,
    top: int = 15,
    min_margin: int = 0,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> str:
    summary = stats.global_summary(conn, min_margin=min_margin, since=since, until=until)
    all_stats = stats.team_stats(conn, min_margin=min_margin, since=since, until=until)

    lines: list[str] = []
    lines.append("=" * 96)
    lines.append("  INDICES DE SURPRISE - statarea")
    lines.append("=" * 96)
    lines.append("")

    total = db.count(conn)
    played = db.count(conn, "outcome IS NOT NULL")
    no_fav = db.count(conn, "favorite IS NULL AND outcome IS NOT NULL")
    lines.append(f"  matchs en base              : {total}")
    lines.append(f"  matchs joues (score connu)  : {played}")
    lines.append(f"  joues sans favori (X en tete ou egalite, exclus) : {no_fav}")
    filters = []
    if min_margin:
        filters.append(f"marge favori >= {min_margin}")
    if since:
        filters.append(f"depuis {since}")
    if until:
        filters.append(f"jusqu'a {until}")
    lines.append(f"  filtres d'analyse           : {', '.join(filters) if filters else 'aucun'}")
    lines.append("")
    lines.append(f"  echantillon analyse         : {summary['matches']} matchs")
    lines.append(f"  taux de surprise global     : {_pct(summary['surprise_rate'])} "
                 f"({summary['surprises']} favoris n'ont pas gagne)")
    lines.append(f"  taux d'upset global         : {_pct(summary['upset_rate'])} "
                 f"({summary['upsets']} victoires d'outsider)")
    if summary["avg_margin"] is not None:
        lines.append(f"  marge moyenne du favori     : {summary['avg_margin']:.1f} points")
    lines.append(f"  predictions statarea justes : {_pct(summary['prediction_hit_rate'])} "
                 f"({summary['prediction_hits']}/{summary['prediction_n']})")
    lines.append("")
    lines.append("  Les taux par equipe se lisent par rapport a ces references globales.")
    lines.append("")

    lines += _table(
        f"  FAVORIS LES PLUS FIABLES  (min. {min_fav} matchs en tant que favorite)",
        stats.most_reliable(all_stats, min_fav)[:top], "fav")
    lines += _table(
        f"  FAVORIS LES MOINS FIABLES - a eviter  (min. {min_fav} matchs)",
        stats.least_reliable(all_stats, min_fav)[:top], "fav")
    lines += _table(
        f"  OUTSIDERS LES PLUS IMPREVISIBLES  (min. {min_dog} matchs en tant qu'outsider)",
        stats.most_unpredictable(all_stats, min_dog)[:top], "dog")

    stable = stats.stable_teams(all_stats, min_fav, min_dog)[:top]
    lines.append(f"  EQUIPES STABLES DANS LES DEUX ROLES  "
                 f"(surprise subie <= 30%, upset cree <= 15%)")
    lines.append(_rule())
    if stable:
        lines.append(f"  {'#':>3}  {'equipe':30} {'fav':>5} {'subie':>7} {'out':>5} {'cree':>7}")
        lines.append(_rule())
        for i, s in enumerate(stable, 1):
            lines.append(f"  {i:>3}  {s.team[:30]:30} {s.fav_matches:>5} "
                         f"{_pct(s.surprised_rate):>7} {s.dog_matches:>5} {_pct(s.upset_rate):>7}")
    else:
        lines.append("  (aucune equipe n'atteint les seuils : echantillon encore trop petit)")
    lines.append("")

    if summary["matches"] < 200:
        lines.append("  ! Echantillon faible : ces classements sont indicatifs, pas exploitables.")
        lines.append("    Continuez la collecte quotidienne avant d'en tirer des regles.")
        lines.append("")

    return "\n".join(lines)
