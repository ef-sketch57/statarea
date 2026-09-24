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
quotidienne. D'où l'archivage systématique du HTML brut dans
`data/raw/YYYY-MM-DD/YYYYmmddTHHMMSSZ.html.gz` **avant** tout parsing : quand les règles évolueront, `reparse` rejoue tout l'historique
sans une seule requête réseau. Les archives sont versionnées dans le dépôt — c'est la seule
copie qui existe. La base SQLite, elle, n'est pas versionnée : elle se reconstruit
intégralement depuis les archives.

Le gzip ramène une journée de 1,7 Mo à ~80 Ko.

### Un fichier par téléchargement, jamais écrasé

**statarea remanie ses pages passées en continu.** Mesuré sur la page du 19 septembre, à
14 minutes d'intervalle : **18 matchs disparus, 7 apparus**. Une archive écrasée est donc
une perte définitive — et comme la fenêtre ne remonte pas au-delà de 3 jours, elle est
irrécupérable.

Chaque téléchargement écrit donc son propre instantané, horodaté en UTC. `reparse` les
rejoue du plus ancien au plus récent : l'upsert accumule les matchs que statarea a retirés
entre deux collectes, et le pronostic retenu reste bien le plus ancien connu. Un instantané
identique au précédent n'est pas réécrit, ce qui évite sept copies d'une date figée.

Sur les 7 dates collectées, l'union des instantanés donne **834 matchs, contre 815 si on ne
gardait que le dernier état de chaque page**.

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

### Déclencheur externe (cron-job.org)

**La planification GitHub fonctionne, mais avec des retards de plusieurs heures.**
Mesuré sur les premières exécutions planifiées : le créneau de 13:25 s'est déclenché à
17:52 (4 h 27 de retard), celui de 21:25 à 23:49 (2 h 24). GitHub ne garantit pas
l'heure d'exécution des tâches planifiées, et les dépôts peu actifs sont servis en
dernier.

Pour la collecte des matchs, ce retard est sans conséquence : la fenêtre de 3 jours
l'absorbe. Pour les cotes, il compte : un passage prévu à 13:25 qui part à 17:52 laisse
passer sans cote d'avant-match les matchs qui commencent entre les deux. Un déclencheur
externe comme [cron-job.org](https://cron-job.org), qui appelle l'API GitHub à l'heure
dite, supprime ce trou.

**1. Jeton GitHub** — *Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token*

- *Repository access* : **Only select repositories** → `ef-sketch57/statarea`
- *Repository permissions* : **Actions → Read and write** (rien d'autre)
- *Expiration* : la plus longue possible, et **notez la date** : à l'expiration la
  collecte s'arrête sans rien casser de visible.

**2. Tester le jeton** avant de le confier à cron-job.org :

```bash
curl -i -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer VOTRE_JETON" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/ef-sketch57/statarea/actions/workflows/collecte-quotidienne.yml/dispatches \
  -d '{"ref":"main"}'
```

Réponse attendue : `HTTP/2 204`, et une exécution « Collecte quotidienne » apparaît dans
l'onglet *Actions*. Un `401` signale un jeton invalide, un `403` une permission manquante,
un `404` un dépôt non sélectionné dans le jeton.

**3. Job cron-job.org** — *Create cronjob*

| champ | valeur |
|---|---|
| URL | `https://api.github.com/repos/ef-sketch57/statarea/actions/workflows/collecte-quotidienne.yml/dispatches` |
| Planification | personnalisée, heures **5, 13, 21**, minute **25**, fuseau **UTC** |
| *Advanced → Request method* | `POST` |
| *Advanced → Headers* | `Accept: application/vnd.github+json`<br>`Authorization: Bearer VOTRE_JETON`<br>`X-GitHub-Api-Version: 2022-11-28`<br>`Content-Type: application/json` |
| *Advanced → Request body* | `{"ref":"main"}` |
| *Notifications* | **activer l'alerte en cas d'échec** |

L'alerte d'échec est le point important : c'est elle qui transforme une panne silencieuse
(jeton expiré, dépôt renommé) en e-mail.

**Consommation.** Une collecte dure environ 7 minutes, dont 5 pour les cotes. Trois par
jour font ~630 minutes par mois, sous le quota gratuit de 2 000 minutes des dépôts privés.
Si cron-job.org est mis en place, retirer le bloc `schedule:` du workflow : sinon les
deux sources déclenchent chacune trois collectes par jour, et la consommation double.

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

## Les cotes

`python -m statarea odds --delay 1.5`

Les cotes ne sont pas dans la page de liste : statarea les sert a la demande, match par
match, par son endpoint interne
(`POST /actions/controller/` avec `object={"action":"getMatchBetInformation","matchid":...}`).

**Pourquoi elles changent tout.** Un taux de reussite ne dit rien sur la rentabilite. Si
statarea annonce 53 % sur un `1` et que le marche le cote a 1.88 (53 % implicites),
suivre ce pronostic a une esperance nulle avant marge et negative apres. Sans les cotes
on ne peut repondre qu'a « statarea se trompe-t-il ? » ; avec elles, on peut repondre a
« peut-on en tirer quelque chose ? ».

Elles donnent aussi une **seconde estimation de probabilite, independante** de celle de
statarea. L'ecart entre les deux est un signal en soi.

Le HTML brut des cotes serait trop lourd a versionner (6 Ko par match et par passage).
Le parsing etant trivial, on archive directement les valeurs en JSONL gzippe, un fichier
par jour dans `data/odds/`, append-only et jamais reecrit — meme principe que les
instantanes HTML. Un releve identique au precedent n'est pas reecrit ; un releve qui a
bouge est conserve en plus, parce que le mouvement des cotes est lui-meme une information.

## Le fuseau horaire est fige en UTC

statarea deduit le fuseau de l'adresse IP et affiche les heures dedans. Une collecte
lancee depuis une autre machine voit donc les memes matchs a des heures differentes, et
ceux qui encadrent minuit **changent de date**.

Mesure sur nos propres archives avant correction : quatre fuseaux distincts selon la
machine (GMT-4, GMT-7, GMT 0, GMT+1), et **5,9 % des matchs enregistres sous deux dates**,
donc comptes deux fois dans toutes les statistiques.

Deux corrections :

1. La session de collecte fige le fuseau en **GMT 0** (`setCustomTimezone`, garde par le
   cookie `statareacustomtimezone`).
2. La cle d'un match est desormais **`sa_id`**, l'identifiant interne de statarea, seul
   vraiment stable. `date|domicile|exterieur` reste en repli pour les rares lignes sans
   `sa_id`. `match_date` et `kickoff` sont figes a leur premiere valeur connue.

Les archives anterieures a la correction restent dans des fuseaux melanges : l'heure de
quelques matchs y est imprecise de quelques heures. Les doublons, eux, ont disparu.

## L'indice residuel

`python -m statarea residual --min 5`

**Pourquoi pas le taux brut.** Un taux de surprise brut par equipe melange deux choses :
la fragilite propre de l'equipe, et la difficulte de ses matchs. Une equipe toujours
favorite a 40/32/28 sera surprise bien plus souvent qu'une equipe favorite a 65/22/13,
sans qu'aucune fragilite n'entre en jeu. Un classement bati sur le taux brut
redecouvrirait surtout « les equipes qui jouent des matchs serres ».

L'indice residuel compare l'observe a ce que les pourcentages annoncaient, match par
match :

```
attendu(surprise) = 1 - p_favori / 100
attendu(upset)    = p_outsider / 100
residu            = observe - attendu
```

La calibration de statarea ayant ete mesuree comme bonne (ecarts dans le bruit sur
761 matchs), ces attendus sont une reference honnete : **rien n'est ajuste sur les
donnees**, donc aucun risque de surapprentissage et rien a re-entrainer.

**Significativite.** Chaque match est un tirage de Bernoulli de probabilite propre ; la
somme suit une loi de Poisson-binomiale de variance `sum p(1-p)`. Le z-score compare le
residu a cet ecart-type, ce qui tient compte du fait qu'un match a 50/50 est bien plus
bruite qu'un match a 90/10. Le biais de reference retire est calcule **en excluant
l'equipe evaluee**, sinon une equipe qui pese lourd annule une partie de son propre signal.

**Deux references possibles.** `--baseline statarea` compare l'observe aux pourcentages
du site. `--baseline market` le compare a la cote de cloture, marge retiree : une
reference bien plus exigeante, puisque le marche integre blessures, compositions et
argent mise. C'est celle qui mene a une conclusion sur la rentabilite.

**Le seul resultat a regarder d'abord.** Si aucune equipe n'a de propension propre, les
z se distribuent en normale centree reduite, d'ecart-type 1. Une surdispersion nette est
la signature d'un signal reel. Tant que la dispersion ne depasse pas 1, **le classement
ne vaut rien**, quelles que soient les etoiles affichees. En dessous de 5 matchs par
equipe, le test lui-meme n'est pas fiable et le rapport le signale.

Deux tests verrouillent la machinerie : sur du bruit pur elle ne doit annoncer aucun
signal, et sur un signal plante elle doit le voir. Un troisieme verifie que le piege de
confusion est bien neutralise — une equipe toujours favorite a 40 % qui gagne 40 % du
temps a un taux brut alarmant de 60 % de surprises et un residu nul.

## statarea face au marche

`python -m statarea divergence`

Les cotes donnent une seconde estimation de probabilite, independante de celle de
statarea. Comparer les deux, par tranche de probabilite de marche, revele un motif net :

| proba marche | marche | statarea | ecart |
|---|---|---|---|
| 0-10 % | 7,4 % | 13,2 % | **+5,8** |
| 10-20 % | 16,4 % | 19,7 % | +3,3 |
| 20-30 % | 25,8 % | 27,4 % | +1,6 |
| 30-40 % | 34,3 % | 32,9 % | -1,4 |
| 50-65 % | 56,8 % | 54,2 % | -2,7 |
| 65-101 % | 73,4 % | 62,1 % | **-11,3** |

Gradient monotone : **statarea comprime ses probabilites vers le milieu**. Il gonfle les
issues improbables et sous-estime les favoris nets — comportement typique d'une
estimation ramenee vers une moyenne faute d'historique suffisant.

L'effet est bien plus marque sur les selections nationales (+6,7 en bas, -10,6 en haut)
que sur les clubs (+1,8 / -3,3), ce qui appuie la meme explication.

**Ceci mesure un desaccord, pas une erreur.** Que le marche soit plus juste que statarea
est une presomption raisonnable, pas un resultat mesure. Seuls les matchs joues
trancheront.

## La rentabilite

`python -m statarea value`

Justesse et rentabilite sont deux choses sans rapport. Un pronostic juste a 53 % cote
1.88 (53 % implicites) a une esperance nulle avant marge, negative apres. La question
n'est donc jamais « statarea a-t-il raison ? » mais « a-t-il plus souvent raison que la
cote ne le suppose ? ».

**Regle absolue** : seules les cotes relevees **strictement avant le coup d'envoi** sont
utilisees. Une cote posterieure reviendrait a parier en connaissant le resultat, et
produirait des rendements imaginaires — un bug invisible dans les resultats. Un test le
verrouille.

**Doubles chances.** statarea produit aussi des `1X`, `X2` et `12`, alors que l'endpoint
ne sert que les cotes 1, X et 2. La cote est donc reconstituee par
`cote(A ou B) = (cote_A x cote_B) / (cote_A + cote_B)`, la combinaison exacte des
probabilites implicites. Elle reste une reconstitution, et les resultats la distinguent
des paris simples, qui eux sont exacts.

**Les temoins.** Un rendement ne se lit jamais seul : le rapport compare toujours au fait
de parier systematiquement le favori et systematiquement le domicile. Une strategie qui
ne bat pas ces temoins n'apporte rien. Avec une marge mediane mesuree a **10,2 %**, le
rendement de reference d'un parieur sans information est d'environ -10 %.

## Hypothèses pré-enregistrées

Une règle trouvée en regardant les données s'ajuste toujours à ces données. Elle ne vaut
quelque chose que testée sur des matchs joués **après** sa formulation. La date du commit
qui l'introduit en fait foi.

**H1 — 24/09/2026 — le pronostic qui contredit ses propres pourcentages.** Constat
exploratoire sur 501 pronostics simples joués : quand statarea pronostique contre son
propre pourcentage le plus haut (le plus souvent un `X` alors que le `1` est en tête), il
est juste dans **23,9 %** des cas (46 matchs), contre **53,8 %** quand il le suit
(448 matchs). Règle : remplacer un tel pronostic par l'issue la plus probable selon les
pourcentages. Test : la ligne « dont matchs joués depuis le 24/09 » de
`python -m statarea value`, uniquement.

## Le site

`python -m statarea site` genere un site statique dans `dist/` : les matchs a venir,
avec le pronostic statarea, les pourcentages 1/X/2 et la **marge du favori**.

C'est la marge qui justifie le site : la prediction seule ne distingue pas un `1` a
63/25/12 (marge 38, favori net) d'un `1` a 38/33/29 (marge 5, pile ou face). Les deux
s'affichent `1` chez statarea.

Le site affiche aussi le gradient mesure par tranche de marge, et **separe les taux de
reussite simples et doubles chances** : un pronostic `1X` couvre deux issues sur trois,
son taux eleve releve de l'arithmetique et non de la performance. Les agreger donnait un
chiffre flatteur et vide de sens.

Le site est une simple vue de la base, sans etat propre : il se reconstruit entierement
depuis `data/raw/`.

### Couleurs de la barre 1/X/2

La barre n'est pas categorielle mais **divergente** : domicile ← nul → exterieur. Donc
deux teintes opposees et un midpoint gris neutre, jamais une troisieme teinte.

| mode | domicile | nul | exterieur |
|---|---|---|---|
| clair | `#2a78d6` | `#898781` | `#e34948` |
| sombre | `#3987e5` | `#5f5e5a` | `#e66767` |

Palette passee au validateur : separation daltonienne ΔE 8,7 (clair) et 11,7 (sombre),
vision normale 17,8 et 21,8. Le gris sombre est sous 3:1 de contraste, ce qui impose
d'ecrire les valeurs en clair — les trois pourcentages sont donc toujours affiches et la
couleur ne porte jamais seule l'information.

### Deploiement

Le depot contient `netlify.toml` et `vercel.json`. Dans les deux cas, l'hebergeur
construit **depuis le depot** : chaque commit du job de collecte declenche une
reconstruction, et aucun jeton de deploiement n'est stocke cote GitHub.

Sur Netlify : *Add new site → Import an existing project*, choisir le depot. La commande
de build et le repertoire publie sont deja dans `netlify.toml`, il n'y a rien a saisir.

Une precision qui compte : la liste des matchs a venir est calculee **a la date de
construction**. Un jour sans nouvelle archive ne produit pas de commit, donc pas de
reconstruction, et le site afficherait des matchs deja joues. Pour l'eviter, creez un
*build hook* chez l'hebergeur (Netlify : *Site settings → Build & deploy → Build hooks*)
et collez son URL dans un secret GitHub nomme `DEPLOY_BUILD_HOOK` (*Settings → Secrets and
variables → Actions*). Le job le declenche alors chaque jour. Sans ce secret, l'etape est
simplement ignoree.

## Tests

```bash
python -m unittest discover -s tests
```
