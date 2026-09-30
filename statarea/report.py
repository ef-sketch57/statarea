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


def build_residual_report(conn: sqlite3.Connection, min_matches: int = 5,
                          top: int = 15, since: Optional[str] = None,
                          until: Optional[str] = None,
                          baseline: str = "statarea") -> str:
    """Indice residuel : l'ecart a ce que la reference annoncait."""
    from . import residual

    REFERENCE = {
        "statarea": "les pourcentages publies par statarea",
        "market": "la cote de cloture du marche (marge retiree)",
    }[baseline]

    titre = {"statarea": "ecart entre l'observe et ce que statarea annoncait",
             "market": "ecart entre l'observe et ce que le marche annoncait"}[baseline]
    lines = ["=" * 96, f"  INDICE RESIDUEL - {titre}", "=" * 96, ""]

    lines.append("  Le taux de surprise brut par equipe melange fragilite propre et difficulte")
    lines.append("  des matchs. L'indice residuel compare l'observe a une reference, match par")
    lines.append("  match, ce qui neutralise ce biais.")
    lines.append("")
    lines.append(f"  reference utilisee : {REFERENCE}")
    if baseline == "market":
        lines.append("  Reference bien plus exigeante : le marche integre blessures, compositions")
        lines.append("  et argent mise. Seuls les matchs ayant une cote relevee AVANT le coup")
        lines.append("  d'envoi sont retenus.")
    lines.append("")

    for role, titre, sens in (
        ("favorite", "EN TANT QUE FAVORITE", "decoit plus que sa cote ne l'annoncait"),
        ("underdog", "EN TANT QU'OUTSIDER", "cree la surprise plus souvent qu'annonce"),
    ):
        offset, n = residual.calibration_offset(conn, role, since, until, baseline)
        over = residual.overdispersion(conn, role, min_matches, since, until, baseline)

        lines.append("-" * 96)
        lines.append(f"  {titre}   ({n} matchs)")
        lines.append("-" * 96)
        lines.append(f"  biais global de la reference: {offset * 100:+.1f} points")

        if over["sd"] is None:
            lines.append(f"  test de surdispersion      : {over['verdict']} "
                         f"({over['teams']} equipe(s) au seuil de {min_matches} matchs)")
            lines.append("")
            lines.append(f"  Aucun classement publie : en dessous de {min_matches} matchs par equipe,")
            lines.append("  les ecarts ne sont que du bruit d'echantillonnage.")
            lines.append("")
            continue

        lines.append(f"  dispersion des z observee  : {over['sd']:.2f}  "
                     f"(attendue 1.00 +/- {2 * over['se']:.2f} si aucun signal)")
        lines.append(f"  verdict                    : {over['verdict']}")
        if not over.get("reliable"):
            lines.append("  ! Chiffre non interpretable : relancez avec --min 5 ou plus.")
        lines.append(f"  equipes au-dela de |z|>2   : {over['significant']} sur {over['teams']}")
        lines.append("")

        teams = residual.team_residuals(conn, role, min_matches, since, until, baseline)
        if not teams:
            lines.append("  (aucune equipe n'atteint le seuil)")
            lines.append("")
            continue

        lines.append(f"  {'equipe':28} {'n':>4} {'obs':>5} {'attendu':>8} "
                     f"{'ecart/match':>12} {'z':>7}")
        lines.append("  " + "-" * 70)
        for t in teams[:top]:
            flag = " *" if t.significant else ""
            lines.append(f"  {t.team[:28]:28} {t.n:>4} {t.observed:>5} {t.expected:>8.1f} "
                         f"{t.per_match:>+11.1f}% {t.z:>7.2f}{flag}")
        if len(teams) > top:
            lines.append(f"  ... et {len(teams) - top} autres. "
                         f"({sens})")
        lines.append("")

    lines.append("  * |z| > 2, soit environ 5 % de faux positifs AVANT correction pour tests")
    lines.append("    multiples. Avec des centaines d'equipes, quelques etoiles sont attendues")
    lines.append("    par pur hasard : ne regardez le classement que si la dispersion des z")
    lines.append("    depasse nettement 1.")
    lines.append("")
    return "\n".join(lines)


def build_value_report(conn: sqlite3.Connection, since: Optional[str] = None,
                       until: Optional[str] = None) -> str:
    """Rendement des strategies, cotes de cloture uniquement."""
    from . import value

    results = value.evaluate(conn, since, until)
    margin = value.book_margin(conn)
    covered = len(__import__("statarea.db", fromlist=["db"]).closing_odds(conn))

    lines = ["=" * 96,
             "  RENTABILITE - que rapporterait reellement le fait de suivre les pronostics",
             "=" * 96, ""]
    lines.append("  Seules les cotes relevees AVANT le coup d'envoi sont utilisees.")
    lines.append(f"  matchs disposant d'une cote de cloture : {covered}")
    if margin is not None:
        lines.append(f"  marge mediane du bookmaker             : {margin * 100:.1f} %")
        lines.append(f"  -> un parieur sans information perd environ {margin * 100:.0f} % a long terme.")
    lines.append("")

    playable = [r for r in results.values() if r.n]
    if not playable:
        lines.append("  Aucun match joue ne dispose encore d'une cote relevee avant son coup")
        lines.append("  d'envoi. Les cotes ne se collectant que vers l'avant, les premiers")
        lines.append("  resultats arriveront apres le prochain passage sur des matchs joues.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  {'strategie':46} {'n':>5} {'reussis':>8} {'rendement':>11} {'t':>7}")
    lines.append("  " + "-" * 82)
    for key in ("statarea_simple", "statarea_double", "corrige", "corrige_hors_echantillon",
                "contraires", "contraires_hors_echantillon", "favori", "domicile"):
        r = results[key]
        if not r.n:
            continue
        roi = f"{r.roi * 100:+.1f} %"
        t = f"{r.t:+.2f}" if r.t is not None else "  n/a"
        flag = " *" if r.significant else ""
        lines.append(f"  {r.label[:46]:46} {r.n:>5} {r.hits:>8} {roi:>11} {t:>7}{flag}")
    lines.append("")
    lines.append("  * |t| > 2. Un rendement positif NON significatif ne prouve rien : avec")
    lines.append("    quelques centaines de paris, un ecart de plusieurs points est courant")
    lines.append("    par pur hasard.")
    lines.append("")

    simple = results["statarea_simple"]
    favori = results["favori"]
    if simple.n and favori.n and simple.roi is not None and favori.roi is not None:
        delta = (simple.roi - favori.roi) * 100
        lines.append(f"  Ecart au temoin 'toujours le favori' : {delta:+.1f} points.")
        lines.append("  Une strategie qui ne bat pas ce temoin n'apporte aucune information.")
        lines.append("")
    lines.append("  Le pronostic corrige est une hypothese PRE-ENREGISTREE le 24/09 : seule la")
    lines.append("  ligne 'dont matchs joues depuis le 24/09' la teste. La ligne complete inclut")
    lines.append("  les matchs qui ont servi a la formuler, elle n'a pas valeur de preuve.")
    lines.append("")
    h2 = results["contraires_hors_echantillon"]
    lines.append("  H2 (pre-enregistree le 30/09) ne se juge que sur la ligne 'depuis le 01/10',")
    lines.append(f"  a partir de {value.H2_MIN_BETS} paris et si |t| > 2. "
                 f"Etat : {h2.n}/{value.H2_MIN_BETS} paris, "
                 + ("conclusion possible." if h2.n >= value.H2_MIN_BETS else "trop tot pour conclure."))
    lines.append("")
    return "\n".join(lines)


def build_divergence_report(conn: sqlite3.Connection) -> str:
    """Comparaison des deux estimations de probabilite : statarea et le marche."""
    from . import odds as _odds

    pairs = _odds.probability_pairs(conn)
    lines = ["=" * 96,
             "  STATAREA FACE AU MARCHE - deux estimations independantes",
             "=" * 96, ""]
    if not pairs:
        lines.append("  Aucun match ne dispose d'une cote relevee avant son coup d'envoi.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  {len(pairs) // 3} matchs, {len(pairs)} couples de probabilites.")
    lines.append("")
    lines.append("  ATTENTION : ceci mesure un DESACCORD, pas une erreur. Que le marche soit")
    lines.append("  plus juste que statarea est une presomption raisonnable, pas un resultat")
    lines.append("  mesure. Seuls les matchs joues trancheront.")
    lines.append("")

    for label, flag in (("TOUS LES MATCHS", None), ("clubs", False), ("selections nationales", True)):
        bands = _odds.divergence_bands(pairs, flag)
        if not bands:
            continue
        lines.append("-" * 96)
        lines.append(f"  {label}")
        lines.append("-" * 96)
        lines.append(f"  {'proba marche':16} {'n':>5} {'marche':>10} {'statarea':>10} {'ecart':>9}")
        for b in bands:
            lines.append(f"  {b['band'] + ' %':16} {b['n']:>5} {b['market']:>9.1f}% "
                         f"{b['statarea']:>9.1f}% {b['gap']:>+8.1f}")
        lines.append("")

    lines.append("  Un ecart positif en bas et negatif en haut signe une COMPRESSION : le")
    lines.append("  modele gonfle les issues improbables et sous-estime les favoris nets.")
    lines.append("")
    return "\n".join(lines)
