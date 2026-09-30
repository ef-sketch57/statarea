"""Generation du site statique : les matchs a venir et leur pronostic.

Le site n'est qu'une vue de la base, regeneree a chaque collecte. Aucun etat
n'y vit : tout se reconstruit depuis data/raw/.

Choix de couleurs (methode dataviz, validateur execute) : la barre 1/X/2 n'est
pas categorielle mais DIVERGENTE - domicile <- nul -> exterieur. Donc deux
teintes opposees et un midpoint gris neutre, jamais une troisieme teinte.
  clair  #2a78d6 / #898781 / #e34948 - CVD dE 8.7, vision normale 17.8, contraste >= 3:1
  sombre #3987e5 / #5f5e5a / #e66767 - CVD dE 11.7, vision normale 21.8
Le gris sombre passe en avertissement de contraste (2.68:1) : la regle de
relief impose alors d'afficher les valeurs en clair, ce que fait la barre -
les trois pourcentages sont toujours ecrits, la couleur ne porte jamais seule
l'information.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from . import db, stats

DEFAULT_OUT = Path("dist")

# Seuils de lecture de fav_margin, en points de pourcentage.
TIERS = (
    (20, "favori net", "net"),
    (10, "favori modere", "modere"),
    (0, "tres serre", "serre"),
)

JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
        "aout", "septembre", "octobre", "novembre", "decembre")


def e(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def tier(margin: Optional[int]) -> tuple[str, str]:
    if margin is None:
        return "pas de favori", "aucun"
    for threshold, label, slug in TIERS:
        if margin >= threshold:
            return label, slug
    return "pas de favori", "aucun"


def short_date(iso: Optional[str]) -> str:
    if not iso:
        return "-"
    d = date.fromisoformat(iso)
    return f"{d.day:02d}/{d.month:02d}"


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def upcoming(conn: sqlite3.Connection, today: str, limit_days: int = 7,
             now: Optional[datetime] = None) -> list[sqlite3.Row]:
    """Matchs pas encore COMMENCES.

    L'absence de score ne suffit pas : statarea met une douzaine d'heures a
    publier les resultats, et un match deja joue restait affiche "a venir".
    """
    now = now or datetime.now(timezone.utc)
    return conn.execute(
        """SELECT * FROM matches
           WHERE match_date >= ? AND outcome IS NULL
             AND (kickoff IS NULL OR match_date || 'T' || kickoff > ?)
           ORDER BY match_date, kickoff, country, home""",
        (today, now.strftime("%Y-%m-%dT%H:%M")),
    ).fetchall()


def _bar(row: sqlite3.Row) -> str:
    """Barre divergente 1/X/2. Les trois valeurs sont toujours ecrites."""
    if row["p1"] is None:
        return '<div class="bar-missing">pourcentages indisponibles</div>'

    cells = []
    for key, code, side in (("p1", "1", "home"), ("px", "X", "draw"), ("p2", "2", "away")):
        value = row[key]
        cells.append(
            f'<div class="seg seg-{side}" style="flex-grow:{value}" '
            f'title="{code} : {value}%"></div>'
        )

    legend = "".join(
        f'<span class="key"><i class="chip chip-{side}"></i>'
        f'<b>{code}</b> <span class="num">{row[key]}</span></span>'
        for key, code, side in (("p1", "1", "home"), ("px", "X", "draw"), ("p2", "2", "away"))
    )
    return f'<div class="bar">{"".join(cells)}</div><div class="keys">{legend}</div>'


def _match_row(row: sqlite3.Row) -> str:
    label, slug = tier(row["fav_margin"])
    fav = row["favorite"]
    home = f'<b>{e(row["home"])}</b>' if fav == "home" else e(row["home"])
    away = f'<b>{e(row["away"])}</b>' if fav == "away" else e(row["away"])
    margin = f'<span class="margin">+{row["fav_margin"]}</span>' if row["fav_margin"] is not None else ""
    comp = " · ".join(x for x in (e(row["country"]), e(row["competition"])) if x)

    return f"""
      <article class="match">
        <div class="when"><time>{e(row["kickoff"] or "")}</time></div>
        <div class="who">
          <div class="teams">{home} <span class="v">-</span> {away}</div>
          <div class="comp">{comp}</div>
        </div>
        <div class="odds">{_bar(row)}</div>
        <div class="verdict">
          <span class="tip tip-{e(row["prediction"] or "na")}">{e(row["prediction"] or "-")}</span>
          <span class="tier tier-{slug}">{label} {margin}</span>
        </div>
      </article>"""


# Tranches de marge. Le gradient est net et c'est l'information la plus
# solide dont nous disposions aujourd'hui.
BANDS = ((0, 5, "0-4"), (5, 10, "5-9"), (10, 20, "10-19"),
         (20, 30, "20-29"), (30, 101, "30 et +"))


def margin_bands(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for lo, hi, label in BANDS:
        r = conn.execute(
            """SELECT COUNT(*) n, AVG(surprise = 0) win,
                      AVG(surprise = 1 AND upset = 0) draw, AVG(upset) ups
               FROM matches
               WHERE outcome IS NOT NULL AND favorite IS NOT NULL
                 AND fav_margin >= ? AND fav_margin < ?""", (lo, hi)).fetchone()
        if r["n"]:
            out.append({"label": label, "n": r["n"], "win": r["win"] * 100,
                        "draw": r["draw"] * 100, "ups": r["ups"] * 100})
    return out


def hit_rates(conn: sqlite3.Connection) -> dict:
    """Simples et doubles chances separes.

    Les agreger n'a aucun sens : un pronostic `1X` couvre deux issues sur trois,
    son taux eleve est de l'arithmetique, pas de la performance.
    """
    out = {}
    for key, preds in (("simple", ("1", "X", "2")), ("double", ("1X", "X2", "12"))):
        r = conn.execute(
            f"""SELECT COUNT(*) n, SUM(prediction_hit) h FROM matches
                WHERE prediction_hit IS NOT NULL
                  AND prediction IN ({",".join("?" * len(preds))})""", preds).fetchone()
        out[key] = {"n": r["n"] or 0, "rate": (r["h"] / r["n"]) if r["n"] else None}
    return out


def _summary(conn: sqlite3.Connection) -> dict:
    s = stats.global_summary(conn)
    s["total"] = db.count(conn)
    s["played"] = db.count(conn, "outcome IS NOT NULL")
    row = conn.execute(
        "SELECT MIN(match_date) a, MAX(match_date) b FROM matches WHERE outcome IS NOT NULL"
    ).fetchone()
    s["from"], s["to"] = short_date(row["a"]), short_date(row["b"])
    s["hits"] = hit_rates(conn)
    s["bands"] = margin_bands(conn)
    return s


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.0f} %"


def _bands_table(bands: list[dict]) -> str:
    if not bands:
        return ""
    rows = "".join(
        f'<tr><th scope="row">{b["label"]}</th><td class="n n-col">{b["n"]}</td>'
        f'<td class="viz"><div class="bar">'
        f'<div class="seg seg-home" style="flex-grow:{b["win"]:.1f}"></div>'
        f'<div class="seg seg-draw" style="flex-grow:{b["draw"]:.1f}"></div>'
        f'<div class="seg seg-away" style="flex-grow:{b["ups"]:.1f}"></div>'
        f'</div></td>'
        f'<td class="v">{b["win"]:.0f} %</td><td class="v">{b["draw"]:.0f} %</td>'
        f'<td class="v">{b["ups"]:.0f} %</td></tr>'
        for b in bands
    )
    return f"""
  <section class="bands">
    <h2>Ce que la marge annonce</h2>
    <table>
      <thead><tr><th scope="col">marge</th><th scope="col" class="n-col">matchs</th>
        <th scope="col"></th>
        <th scope="col"><i class="chip chip-home"></i><span class="lg"> favori gagne</span><span class="sm"> favori</span></th>
        <th scope="col"><i class="chip chip-draw"></i> nul</th>
        <th scope="col"><i class="chip chip-away"></i> upset</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <p class="cap">Le risque d'upset est multiplie par trois entre un favori net et un
    favori serre. C'est l'information la plus solide dont nous disposions aujourd'hui,
    et elle ne demande aucun modele.</p>
  </section>"""


def render(conn: sqlite3.Connection, today: Optional[str] = None) -> str:
    today = today or date.today().isoformat()
    rows = upcoming(conn, today)
    s = _summary(conn)
    built = datetime.now(timezone.utc).strftime("%d/%m/%Y a %H:%M UTC")

    days, current = [], None
    for row in rows:
        if row["match_date"] != current:
            current = row["match_date"]
            days.append((current, []))
        days[-1][1].append(row)

    sections = "".join(
        f'<section class="day"><h2>{long_date(day)}'
        f'<span class="count">{len(items)} match{"s" if len(items) > 1 else ""}</span></h2>'
        f'<div class="matches">{"".join(_match_row(r) for r in items)}</div></section>'
        for day, items in days
    )
    if not sections:
        sections = ('<section class="day"><p class="empty">Aucun match a venir en base. '
                    'Lancez une collecte : <code>python -m statarea scrape --window 3</code></p></section>')

    return TEMPLATE.format(
        built=built, sections=sections,
        total=s["total"], played=s["played"], span_from=s["from"], span_to=s["to"],
        hit_s=_pct(s["hits"]["simple"]["rate"]), hit_s_n=s["hits"]["simple"]["n"],
        hit_d=_pct(s["hits"]["double"]["rate"]), hit_d_n=s["hits"]["double"]["n"],
        surprise=_pct(s["surprise_rate"]), upset=_pct(s["upset_rate"]),
        upcoming=len(rows), bands=_bands_table(s["bands"]),
    )


def build(conn: sqlite3.Connection, out_dir: Path | str = DEFAULT_OUT,
          today: Optional[str] = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(render(conn, today), encoding="utf-8")
    return page


TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pronostics du jour</title>
<meta name="description" content="Les matchs a venir, leur pronostic et la marge du favori.">
<style>
  :root {{
    color-scheme: light;
    --plane:#f9f9f7; --surface:#fcfcfb;
    --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
    --rule:#e1e0d9; --ring:rgba(11,11,11,.10);
    --home:#2a78d6; --draw:#898781; --away:#e34948;
    --accent:#2a78d6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --plane:#0d0d0d; --surface:#1a1a19;
      --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
      --rule:#2c2c2a; --ring:rgba(255,255,255,.10);
      --home:#3987e5; --draw:#5f5e5a; --away:#e66767;
      --accent:#3987e5;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
    --rule:#2c2c2a; --ring:rgba(255,255,255,.10);
    --home:#3987e5; --draw:#5f5e5a; --away:#e66767;
    --accent:#3987e5;
  }}

  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--plane); color:var(--ink);
    font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
  }}
  .wrap {{ max-width:1000px; margin:0 auto; padding:32px 16px 64px; }}

  header h1 {{ margin:0 0 4px; font-size:1.6rem; letter-spacing:-.01em; }}
  .built {{ color:var(--muted); font-size:.85rem; margin:0 0 24px; }}

  .facts {{
    display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
    background:var(--rule); border:1px solid var(--rule); border-radius:10px;
    overflow:hidden; margin-bottom:20px;
  }}
  .fact {{ background:var(--surface); padding:12px 14px; }}
  .fact .v {{ display:block; font-size:1.45rem; font-weight:600; letter-spacing:-.02em; }}
  .fact .k {{ color:var(--ink-2); font-size:.78rem; }}

  .note {{
    border:1px solid var(--rule); border-left:3px solid var(--accent);
    background:var(--surface); border-radius:8px; padding:12px 14px;
    color:var(--ink-2); font-size:.88rem; margin-bottom:28px;
  }}
  .note b {{ color:var(--ink); }}

  .day {{ margin-bottom:28px; }}
  .day h2 {{
    font-size:.82rem; text-transform:uppercase; letter-spacing:.06em;
    color:var(--ink-2); font-weight:600; margin:0 0 8px;
    display:flex; align-items:baseline; gap:8px;
  }}
  .count {{ color:var(--muted); font-weight:400; letter-spacing:0; text-transform:none; }}
  .matches {{ border:1px solid var(--rule); border-radius:10px; overflow:hidden; background:var(--surface); }}

  .match {{
    display:grid; gap:4px 14px; padding:12px 14px; border-top:1px solid var(--rule);
    grid-template-columns:52px minmax(0,1fr) 190px 168px; align-items:center;
  }}
  .match:first-child {{ border-top:0; }}

  .when time {{ font-variant-numeric:tabular-nums; color:var(--ink-2); font-size:.9rem; }}
  .teams {{ line-height:1.35; }}
  .teams .v {{ color:var(--muted); padding:0 2px; }}
  .comp {{ color:var(--muted); font-size:.76rem; margin-top:1px; }}

  .bar {{ display:flex; gap:2px; height:8px; }}
  .seg {{ background:var(--draw); flex-basis:0; min-width:3px; }}
  .seg-home {{ background:var(--home); border-radius:4px 0 0 4px; }}
  .seg-away {{ background:var(--away); border-radius:0 4px 4px 0; }}
  .bar-missing {{ color:var(--muted); font-size:.78rem; }}

  .keys {{ display:flex; gap:12px; margin-top:6px; font-size:.78rem; color:var(--ink-2); }}
  .key {{ display:inline-flex; align-items:center; gap:4px; }}
  .chip {{ width:8px; height:8px; border-radius:2px; display:inline-block; }}
  .chip-home {{ background:var(--home); }}
  .chip-draw {{ background:var(--draw); }}
  .chip-away {{ background:var(--away); }}
  .num {{ font-variant-numeric:tabular-nums; }}

  .verdict {{ display:flex; align-items:baseline; gap:8px; white-space:nowrap; }}
  .tip {{
    font-weight:600; font-size:.85rem; min-width:30px; text-align:center;
    padding:3px 7px; border-radius:6px; border:1px solid var(--ring); background:var(--plane);
  }}
  .tier {{ font-size:.78rem; color:var(--ink-2); }}
  .tier-net {{ color:var(--ink); font-weight:600; }}
  .tier-aucun {{ color:var(--muted); font-style:italic; }}
  .margin {{ font-variant-numeric:tabular-nums; color:var(--muted); font-weight:400; }}

  .bands {{ margin-bottom:28px; }}
  .bands h2 {{
    font-size:.82rem; text-transform:uppercase; letter-spacing:.06em;
    color:var(--ink-2); font-weight:600; margin:0 0 8px;
  }}
  .bands table {{
    width:100%; border-collapse:collapse; background:var(--surface);
    border:1px solid var(--rule); border-radius:10px; overflow:hidden;
  }}
  .bands th, .bands td {{ padding:9px 12px; text-align:left; font-weight:400; }}
  .bands thead th {{
    font-size:.74rem; color:var(--ink-2); border-bottom:1px solid var(--rule);
    white-space:nowrap;
  }}
  .bands thead th .chip {{ margin-right:4px; vertical-align:baseline; }}
  .bands tbody tr {{ border-top:1px solid var(--rule); }}
  .bands tbody tr:first-child {{ border-top:0; }}
  .bands tbody th {{ font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .bands .n, .bands .v {{ font-variant-numeric:tabular-nums; color:var(--ink-2); }}
  .bands .v {{ text-align:right; white-space:nowrap; }}
  .bands thead th:nth-child(n+4) {{ text-align:right; }}
  .bands .sm {{ display:none; }}
  .bands .viz {{ width:34%; min-width:120px; }}
  .cap {{ color:var(--ink-2); font-size:.82rem; margin:10px 2px 0; }}

  .empty {{ padding:16px; color:var(--ink-2); }}
  code {{ background:var(--plane); padding:1px 5px; border-radius:4px; font-size:.85em; }}
  footer {{ margin-top:40px; color:var(--muted); font-size:.8rem; border-top:1px solid var(--rule); padding-top:14px; }}

  @media (max-width:760px) {{
    .match {{ grid-template-columns:52px minmax(0,1fr); }}
    .odds, .verdict {{ grid-column:1 / -1; }}
    .bands .viz, .bands .n-col {{ display:none; }}
    .bands th, .bands td {{ padding:8px 6px; font-size:.84rem; }}
    .bands .lg {{ display:none; }}
    .bands .sm {{ display:inline; }}
    .verdict {{ margin-top:2px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Pronostics a venir</h1>
    <p class="built">Genere le {built} &middot; {upcoming} matchs a venir</p>
  </header>

  <div class="facts">
    <div class="fact"><span class="v">{total}</span><span class="k">matchs en base</span></div>
    <div class="fact"><span class="v">{played}</span><span class="k">joues, du {span_from} au {span_to}</span></div>
    <div class="fact"><span class="v">{hit_s}</span><span class="k">pronostics simples justes ({hit_s_n})</span></div>
    <div class="fact"><span class="v">{hit_d}</span><span class="k">doubles chances justes ({hit_d_n})</span></div>
    <div class="fact"><span class="v">{surprise}</span><span class="k">favoris battus ou tenus en echec</span></div>
    <div class="fact"><span class="v">{upset}</span><span class="k">victoires d'outsider</span></div>
  </div>

  <p class="note">
    La <b>marge</b> est l'ecart entre le favori et le deuxieme pourcentage. C'est elle qui
    separe un favori net d'un match a pile ou face, ce que la seule prediction ne dit pas.
    Un match sans favori est un match ou le nul arrive en tete : il est affiche, mais exclu
    des statistiques de surprise.
    <br><br>
    Les deux taux de reussite sont volontairement <b>separes</b> : un pronostic double
    (<code>1X</code>, <code>X2</code>, <code>12</code>) couvre deux issues sur trois, son
    taux eleve releve de l'arithmetique et non de la performance. Les agreger donnerait un
    chiffre flatteur et vide de sens.
    <br><br>
    <b>Les indices de fiabilite par equipe ne sont pas encore la</b> : ils demandent plusieurs
    semaines de collecte. Les chiffres ci-dessus portent sur {played} matchs seulement et ne
    sont pas exploitables en l'etat.
  </p>

  {bands}

  {sections}

  <footer>
    Donnees collectees depuis statarea.com. Les pourcentages affiches sont ceux publies
    <b>avant</b> le match et ne sont jamais reecrits ensuite.
  </footer>
</div>
</body>
</html>
"""
