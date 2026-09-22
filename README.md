# statarea — base de données de "surprises"

Collecte les prédictions de [statarea.com](https://www.statarea.com/predictions) et en
dérive, par équipe, deux indicateurs distincts :

- **fiabilité** — quand l'équipe est **favorite**, à quelle fréquence se fait-elle surprendre ?
- **imprévisibilité** — quand l'équipe est **outsider**, à quelle fréquence crée-t-elle la surprise ?

L'objectif est de ne suivre les prédictions statarea que sur les matchs où les deux
équipes sont stables, et d'identifier les équipes à éviter.

## Contrainte importante : statarea n'a pas d'historique

Le site ne sert qu'une **fenêtre glissante d'environ ±3 jours autour de la date du jour**.
Une date hors fenêtre ne renvoie pas d'erreur : elle renvoie **silencieusement la page du
jour**. Le parser vérifie donc systématiquement le `div#cdate` de la page servie et lève
une `ParseError` en cas de désaccord, plutôt que d'enregistrer les mauvais matchs sous la
mauvaise date.

Conséquence : **l'historique ne peut que se construire vers l'avant**, par une collecte
quotidienne. D'où l'archivage systématique du HTML brut dans `data/raw/YYYY-MM-DD.html.gz`
**avant** tout parsing : quand les règles évolueront, `reparse` rejoue tout l'historique
sans une seule requête réseau. Les archives sont versionnées dans le dépôt — c'est la seule
copie qui existe. La base SQLite, elle, n'est pas versionnée : elle se reconstruit
intégralement depuis les archives.

Le gzip ramène une journée de 1,7 Mo à ~80 Ko, soit environ 28 Mo par an.

Autre conséquence, moins évidente : statarea ne publie ses pronostics qu'environ un jour à
l'avance. Le re-scrape quotidien de toute la fenêtre ne sert donc pas qu'à récupérer les
scores, il récupère aussi **les matchs qui n'étaient pas encore listés la veille**. C'est
pourquoi la collecte porte sur `--window 3` et non sur la seule journée écoulée.

**Un jour non collecté est définitivement perdu.** Si le job échoue plusieurs jours de
suite, relancez-le : il rattrapera ce qui reste dans la fenêtre, et rien au-delà.

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

```bash
# une seule date
python -m statarea scrape --date 2026-09-19

# toute la fenêtre disponible (aujourd'hui ±3 jours) — la commande à mettre en cron
python -m statarea scrape --window 3

# une plage
python -m statarea scrape --from 2026-09-19 --to 2026-09-22 --delay 5

# re-parser les archives après un changement de règles : aucun réseau
python -m statarea reparse

# rejouer les seules règles dérivées sur la base existante
python -m statarea recompute

# inspection
python -m statarea dates
python -m statarea show --date 2026-09-19 --limit 20
python -m statarea show --team Lazio

# rapport
python -m statarea report --min-fav 5 --min-dog 5
python -m statarea report --min-fav 8 --min-margin 15   # favoris nets uniquement
```

Une pause de 3 s sépare deux pages par défaut (`--delay`). Les erreurs réseau sont
réessayées trois fois avec un back-off exponentiel.

### Collecte quotidienne automatique

`.github/workflows/collecte-quotidienne.yml` lance la collecte tous les jours à 04h30 UTC,
puis committe les nouvelles archives dans le dépôt. Le rapport est publié dans le résumé du
job. Déclenchement manuel possible via *Actions → Collecte quotidienne → Run workflow*.

Deux conditions pour qu'il tourne :

1. GitHub ne déclenche les workflows planifiés **que depuis la branche par défaut** — ce
   fichier doit y être fusionné ;
2. *Settings → Actions → General → Workflow permissions* doit être sur **Read and write**,
   sinon le commit des archives échoue.

À noter : GitHub désactive automatiquement les workflows planifiés d'un dépôt sans activité
pendant 60 jours. Les commits quotidiens du job suffisent à l'éviter.

Pour une machine qui tourne en permanence, l'équivalent en cron :

```cron
30 4 * * *  cd /chemin/vers/statarea && python -m statarea scrape --window 3 --delay 5
```

## Modèle de données

Table `matches`, clé `match_id = date|domicile|extérieur`.

| colonne | description |
|---|---|
| `match_date`, `kickoff` | date et heure du coup d'envoi |
| `country`, `competition` | pays et compétition |
| `home`, `away` | équipes |
| `prediction` | `1`, `X`, `2`, `1X`, `X2`, `12` |
| `p1`, `px`, `p2` | pourcentages, colonnes 1 / X / 2 de gauche à droite |
| `home_score`, `away_score` | score final, `NULL` tant que le match n'est pas joué |
| `outcome` | `1` / `X` / `2`, calculé depuis le score |
| `favorite` | `home`, `away`, ou `NULL` |
| `fav_margin` | pourcentage du favori moins le deuxième pourcentage |
| `surprise` | le favori n'a pas gagné (nul compris) |
| `upset` | l'outsider a gagné (le nul n'est pas un upset) |
| `prediction_hit` | la prédiction statarea était bonne |

### Règle du favori

Le favori est la colonne au pourcentage **strictement** le plus élevé parmi 1 / X / 2 :

- maximum en colonne **1** → favori = équipe à domicile ;
- maximum en colonne **2** → favori = équipe à l'extérieur ;
- maximum en colonne **X** → **aucun favori** : `favorite`, `surprise` et `upset` sont
  `NULL`. La ligne reste stockée complète, mais elle est exclue des indices de surprise ;
- **égalité au sommet** (34/34/32, 40/20/40) → aucun favori non plus : désigner une des
  deux équipes serait arbitraire.

`fav_margin` sépare un favori net d'un match équilibré sans re-collecter quoi que ce soit :

| exemple | favori | marge |
|---|---|---|
| 49 / 28 / 23 | extérieur | 21 — favori net |
| 38 / 33 / 29 | domicile | 5 — quasi équilibré |

Rien n'est filtré à la collecte : **on stocke tout, on filtre à l'analyse**
(`report --min-margin`).

### `surprise` et `upset` sont volontairement distincts

Un match nul est une surprise subie par le favori, mais ce n'est pas un upset : l'outsider
n'a pas gagné. Les deux compteurs alimentent deux classements différents.

### Règles d'upsert

La même date est re-scrapée une dizaine de fois : trois régimes coexistent.

**Les faits qui se complètent** (`home_score`, `away_score`, `kickoff`, `country`…) suivent
`COALESCE(excluded.col, matches.col)` : une nouvelle valeur remplit un trou ou corrige
l'ancienne, mais un `NULL` **n'écrase jamais** une valeur connue. Le score arrive après la
prédiction : une collecte faite avant le coup d'envoi ne doit pas effacer un score déjà
enregistré.

**Le pronostic est figé à sa première valeur connue** (`prediction`, `p1`, `px`, `p2`),
via `COALESCE(matches.col, excluded.col)` — la règle inverse. C'est le cœur de la méthode :
on veut noter le pronostic tel qu'il était publié **avant** le match. Si statarea révisait
ses pourcentages une fois le résultat connu, les reprendre rendrait toute l'analyse
circulaire — on mesurerait la capacité du site à décrire le passé, pas à prédire l'avenir.

**Les colonnes dérivées** (`outcome`, `favorite`, `fav_margin`, `surprise`, `upset`,
`prediction_hit`) ne sont jamais écrites telles quelles : elles sont **recalculées en SQL**
après chaque upsert, depuis les colonnes précédentes. Une ligne reste donc cohérente quel
que soit l'ordre des collectes, et `recompute` permet de rejouer une règle modifiée sur
tout l'historique sans retoucher aux données brutes.

Un test vérifie que les règles SQL et leurs équivalents Python du parser donnent exactement
les mêmes résultats sur 24 combinaisons de pourcentages et de scores.

## Structure HTML parsée

Page rendue côté serveur, pas d'AJAX :

```
div.competition[id]
  div.header   img[alt="<Pays> country flag"]  +  div.name "ITALY - SERIE A"
  div.body
    div.match[id]
      div.teams  div.ownheader "2026-09-19 12:00"
                 div.hostteam  .goals + .name
                 div.guestteam .goals + .htres "HT 2:0" + .name
      div.tip    div.value "1X"
      div.coefrow  div.ownheader > div.coefbox   en-têtes : 1, X, 2, H1, HX, H2, 1.5, 2.5, 3.5, BTS, OTS
                   div.coefbox > div.value       valeurs, dans le même ordre
```

Deux pièges, tous deux gérés :

- le score est un `<div class='goals'>-</div>` avant le match et un
  `<a class='goals'>2</a>` une fois joué ;
- l'ordre des colonnes est vérifié à chaque parsing contre les en-têtes réels ; si
  statarea le change un jour, le parsing s'arrête au lieu d'inverser silencieusement
  `p1` et `p2`.

## Tests

```bash
python -m unittest discover -s tests
```
