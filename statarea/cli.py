"""Interface en ligne de commande.

    python -m statarea scrape --date 2026-09-19          # une seule date
    python -m statarea scrape --from 2026-09-19 --to 2026-09-22
    python -m statarea scrape --window 3                 # aujourd'hui +/- 3 jours
    python -m statarea reparse                           # re-parse les archives, sans reseau
    python -m statarea recompute                         # rejoue les regles sur la base
    python -m statarea show --date 2026-09-19 --limit 20 # inspection
    python -m statarea odds --delay 1                    # releve les cotes a venir
    python -m statarea value                             # rentabilite mesuree
    python -m statarea divergence                        # statarea face au marche
    python -m statarea residual --baseline market        # indice contre le marche
    python -m statarea residual --min 5                  # indice residuel par equipe
    python -m statarea site                              # genere le site dans dist/
    python -m statarea report --min-fav 5 --min-dog 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from . import db, odds, report, scraper, site
from .parser import ParseError, parse_page
from .scraper import FetchError


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(db.DEFAULT_DB), help="chemin de la base SQLite")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="statarea", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrape", help="telecharge, archive et enregistre une plage de dates")
    _add_common(p)
    p.add_argument("--date", help="une seule date YYYY-MM-DD")
    p.add_argument("--from", dest="start", help="debut de plage YYYY-MM-DD")
    p.add_argument("--to", dest="end", help="fin de plage YYYY-MM-DD")
    p.add_argument("--window", type=int, help="aujourd'hui +/- N jours")
    p.add_argument("--raw-dir", default=str(scraper.RAW_DIR))
    p.add_argument("--delay", type=float, default=scraper.DEFAULT_DELAY,
                   help="pause en secondes entre deux pages (defaut: 3)")
    p.add_argument("--use-cache", action="store_true",
                   help="relit l'archive locale si elle existe au lieu de retelecharger")
    p.add_argument("--dry-run", action="store_true",
                   help="archive et parse mais n'ecrit pas en base")

    p = sub.add_parser("reparse", help="re-parse les archives locales, sans aucune requete")
    _add_common(p)
    p.add_argument("--raw-dir", default=str(scraper.RAW_DIR))
    p.add_argument("--incremental", action="store_true",
                   help="ne rejouer que les instantanes absents de la base")
    p.add_argument("--odds-dir", default=str(odds.ODDS_DIR))

    p = sub.add_parser("recompute", help="rejoue les regles derivees sur la base existante")
    _add_common(p)

    p = sub.add_parser("show", help="affiche les matchs enregistres")
    _add_common(p)
    p.add_argument("--date")
    p.add_argument("--team")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("dates", help="couverture de la base, par date")
    _add_common(p)

    p = sub.add_parser("divergence", help="statarea face au marche")
    _add_common(p)

    p = sub.add_parser("value", help="rentabilite : ce que rapporteraient les pronostics")
    _add_common(p)
    p.add_argument("--since")
    p.add_argument("--until")

    p = sub.add_parser("odds", help="releve les cotes des matchs a venir")
    _add_common(p)
    p.add_argument("--delay", type=float, default=1.0,
                   help="pause entre deux matchs en secondes (defaut: 1)")
    p.add_argument("--ahead", type=int, default=3, help="jours a venir couverts")
    p.add_argument("--behind", type=int, default=1, help="jours passes couverts")
    p.add_argument("--limit", type=int, help="s'arreter apres N matchs")
    p.add_argument("--today", help="date de reference YYYY-MM-DD")

    p = sub.add_parser("residual", help="indice residuel : ecart a ce qu'annoncait statarea")
    _add_common(p)
    p.add_argument("--min", type=int, default=5, dest="min_matches",
                   help="minimum de matchs par equipe et par role (defaut: 5)")
    p.add_argument("--baseline", choices=("statarea", "market"), default="statarea",
                   help="reference de l'attendu : les pourcentages statarea, "
                        "ou la cote de cloture du marche (defaut: statarea)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--since")
    p.add_argument("--until")

    p = sub.add_parser("site", help="genere le site statique dans dist/")
    _add_common(p)
    p.add_argument("--out", default="dist", help="repertoire de sortie (defaut: dist)")
    p.add_argument("--today", help="date de reference YYYY-MM-DD (defaut: aujourd'hui)")

    p = sub.add_parser("report", help="classements de fiabilite et d'imprevisibilite")
    _add_common(p)
    p.add_argument("--min-fav", type=int, default=5,
                   help="minimum de matchs en tant que favorite (defaut: 5)")
    p.add_argument("--min-dog", type=int, default=5,
                   help="minimum de matchs en tant qu'outsider (defaut: 5)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-margin", type=int, default=0,
                   help="ne garder que les matchs dont la marge du favori atteint ce seuil")
    p.add_argument("--since")
    p.add_argument("--until")

    return parser


def _days(args) -> list[str]:
    if args.date:
        return [args.date]
    if args.window is not None:
        today = date.today()
        return [(today + timedelta(days=d)).isoformat()
                for d in range(-args.window, args.window + 1)]
    if args.start and args.end:
        return list(scraper.daterange(args.start, args.end))
    if args.start:
        return list(scraper.daterange(args.start, date.today().isoformat()))
    return [date.today().isoformat()]


def cmd_scrape(args) -> int:
    days = _days(args)
    session = scraper.make_session()
    conn = db.connect(args.db)
    total_new = total_upd = 0
    failures = 0

    print(f"Collecte de {len(days)} date(s), pause de {args.delay}s entre les pages.\n")
    for i, day in enumerate(days):
        try:
            matches = scraper.collect_day(day, session=session, raw_dir=args.raw_dir,
                                          use_cache=args.use_cache)
        except (FetchError, ParseError) as exc:
            print(f"  {day}  ! {exc}")
            failures += 1
        else:
            played = sum(1 for m in matches if m.outcome)
            if args.dry_run:
                print(f"  {day}  {len(matches):4} matchs ({played} joues)  [dry-run]")
            else:
                new, upd = db.upsert_matches(conn, matches)
                total_new += new
                total_upd += upd
                print(f"  {day}  {len(matches):4} matchs ({played} joues)  "
                      f"+{new} nouveaux, {upd} completes")
        if i < len(days) - 1:
            scraper.sleep_between(args.delay)

    print(f"\nTermine : +{total_new} nouveaux, {total_upd} completes, {failures} echec(s).")
    print(f"HTML brut archive dans {args.raw_dir}/")
    return 1 if failures == len(days) else 0


def plan_reparse(conn, raw_dir, incremental: bool) -> tuple[list, bool]:
    """Instantanes a rejouer, et s'il faut repartir d'une base vide.

    En mode incremental, seuls les instantanes absents de snapshots_done sont
    rejoues. C'est equivalent a une reconstruction complete a une condition :
    qu'aucun nouvel instantane ne soit ANTERIEUR a un instantane deja traite de
    la meme date, sinon l'ordre de rejeu - donc les valeurs figees a la premiere
    observation - differerait. Dans ce cas, on reconstruit tout.
    """
    every = list(scraper.iter_snapshots(raw_dir))
    if not incremental:
        return every, True

    done = db.done_snapshots(conn)
    if not done:
        return every, True

    latest_done: dict[str, str] = {}
    for day, path in every:
        key = scraper.snapshot_key(path, raw_dir)
        if key in done:
            latest_done[day] = max(latest_done.get(day, ""), path.name)

    todo = []
    for day, path in every:
        key = scraper.snapshot_key(path, raw_dir)
        if key in done:
            continue
        if path.name < latest_done.get(day, ""):
            print(f"  instantane anterieur a un instantane deja traite ({key}) : "
                  "reconstruction complete.")
            return every, True
        todo.append((day, path))
    return todo, False


def cmd_reparse(args) -> int:
    conn = db.connect(args.db)
    todo, full = plan_reparse(conn, args.raw_dir, args.incremental)
    if full:
        db.reset(conn)
    mode = "complete" if full else "incrementale"
    print(f"Reconstruction {mode} : {len(todo)} instantane(s) a rejouer.")

    total_new = total_upd = 0
    for day, path in todo:
        try:
            matches = parse_page(scraper.read_archive(path), expected_date=day)
        except ParseError as exc:
            print(f"  ! {day} ({path.name}): {exc}")
            matches = []
        if matches:
            new, upd = db.upsert_matches(conn, matches)
            total_new += new
            total_upd += upd
            print(f"  {day}  {len(matches):4} matchs  +{new} nouveaux, {upd} completes")
        db.mark_snapshot(conn, scraper.snapshot_key(path, args.raw_dir))
    print(f"\nRe-parse termine : +{total_new} nouveaux, {total_upd} completes.")

    # Les cotes aussi : sans elles, une base reconstruite (job GitHub, build du
    # site) ne connait que les cotes du passage en cours, jamais les cotes
    # d'avant-match des matchs joues depuis. Rechargement idempotent et rapide.
    loaded = 0
    for day, records in odds.iter_archive(args.odds_dir):
        loaded += db.upsert_odds(conn, records)
    print(f"Cotes rechargees depuis data/odds/ : {loaded} releve(s).")
    return 0


def cmd_recompute(args) -> int:
    conn = db.connect(args.db)
    with conn:
        db.recompute(conn)
    print(f"Regles rejouees sur {db.count(conn)} match(s).")
    return 0


def cmd_show(args) -> int:
    conn = db.connect(args.db)
    where, params = [], []
    if args.date:
        where.append("match_date = ?")
        params.append(args.date)
    if args.team:
        where.append("(home LIKE ? OR away LIKE ?)")
        params += [f"%{args.team}%", f"%{args.team}%"]
    sql = "SELECT * FROM matches"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY match_date, kickoff LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("Aucun match ne correspond.")
        return 0

    head = (f"{'date':10} {'h':5} {'pays':12} {'domicile':22} {'exterieur':22} "
            f"{'tip':4} {'1':>3} {'X':>3} {'2':>3} {'score':6} {'res':3} {'fav':5} "
            f"{'mrg':>3} {'sur':>3} {'ups':>3} {'hit':>3}")
    print(head)
    print("-" * len(head))
    for r in rows:
        score = (f"{r['home_score']}-{r['away_score']}"
                 if r["home_score"] is not None else "-")
        def f(v): return "-" if v is None else str(v)
        print(f"{r['match_date']:10} {(r['kickoff'] or ''):5} {(r['country'] or '')[:12]:12} "
              f"{r['home'][:22]:22} {r['away'][:22]:22} {(r['prediction'] or '-'):4} "
              f"{f(r['p1']):>3} {f(r['px']):>3} {f(r['p2']):>3} {score:6} "
              f"{f(r['outcome']):3} {(r['favorite'] or '-')[:5]:5} {f(r['fav_margin']):>3} "
              f"{f(r['surprise']):>3} {f(r['upset']):>3} {f(r['prediction_hit']):>3}")
    print(f"\n{len(rows)} ligne(s). fav: equipe favorite | mrg: marge | "
          f"sur: surprise subie | ups: upset | hit: prediction juste")
    return 0


def cmd_dates(args) -> int:
    conn = db.connect(args.db)
    rows = db.dates(conn)
    if not rows:
        print("Base vide.")
        return 0
    print(f"{'date':12} {'matchs':>8} {'joues':>8}")
    print("-" * 30)
    for day, n, played in rows:
        print(f"{day:12} {n:>8} {played or 0:>8}")
    print("-" * 30)
    print(f"{'total':12} {sum(r[1] for r in rows):>8} {sum(r[2] or 0 for r in rows):>8}")
    return 0


def cmd_divergence(args) -> int:
    conn = db.connect(args.db)
    print(report.build_divergence_report(conn))
    return 0


def cmd_value(args) -> int:
    conn = db.connect(args.db)
    print(report.build_value_report(conn, since=args.since, until=args.until))
    return 0


def cmd_odds(args) -> int:
    conn = db.connect(args.db)

    def progress(i, total, row):
        if i % 25 == 0 or i == total:
            print(f"  {i}/{total}...", flush=True)

    r = odds.collect(conn, today=args.today, ahead=args.ahead, behind=args.behind,
                     delay=args.delay, limit=args.limit, progress=progress)
    print(f"\n{r['matchs']} matchs interroges : {r['cotes']} avec cotes, "
          f"{r['sans_cote']} sans, {r['echecs']} echec(s).")
    print(f"{r['archives']} releve(s) archive(s) dans data/odds/, "
          f"{r['enregistres']} enregistre(s) en base.")
    return 0


def cmd_residual(args) -> int:
    conn = db.connect(args.db)
    print(report.build_residual_report(conn, min_matches=args.min_matches, top=args.top,
                                       since=args.since, until=args.until,
                                       baseline=args.baseline))
    return 0


def cmd_site(args) -> int:
    conn = db.connect(args.db)
    page = site.build(conn, args.out, args.today)
    upcoming = len(site.upcoming(conn, args.today or date.today().isoformat()))
    print(f"{page} genere ({upcoming} matchs a venir, {db.count(conn)} en base).")
    return 0


def cmd_report(args) -> int:
    conn = db.connect(args.db)
    print(report.build_report(conn, min_fav=args.min_fav, min_dog=args.min_dog,
                              top=args.top, min_margin=args.min_margin,
                              since=args.since, until=args.until))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {
        "scrape": cmd_scrape, "reparse": cmd_reparse, "recompute": cmd_recompute,
        "show": cmd_show, "dates": cmd_dates, "site": cmd_site,
        "report": cmd_report, "residual": cmd_residual, "odds": cmd_odds,
        "value": cmd_value, "divergence": cmd_divergence,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
