# CONTEXTE : aboeka-bot
# Historique des phases et état courant du projet
# Dernière mise à jour : 2026-06-17

---

## Genèse : pourquoi ce bot existe

**aboeka-bot** est le successeur de **BotGSTAR** (archivé le 2026-06-17).

BotGSTAR était un monolithe Python (discord.ext.commands) qui gérait à la fois :
- Le pipeline Arsenal (veille vidéo politique sur 6 plateformes : TikTok, Insta, YouTube, X, Reddit, Threads)
- Les cours ISTIC (publication corrections, transcriptions CM, forums Discord)
- La veille RSS tech et politique (digests quotidiens)

Le pipeline Arsenal a été externalisé vers **aboeka.fr** (service Python séparé, hébergé). aboeka-bot est le pont Discord ↔ API aboeka.fr, plus léger et maintenable que le pipeline intégré.

---

## Phase 0 : création initiale (avant 2026-06-17)

- Bot léger : `discord.Client` + écoute `🔗・liens` (channel `1498918445763268658`)
- Détecte liens TikTok / Instagram / YouTube / X / Reddit
- Appelle `POST /api/bot/generate` → poll job → publie fiche dans forums Discord
- Fichier `publisher.py` : routing vers forums thématiques, anti-doublon via `data/published_threads.json`
- Réception (commentaires) postée par `_reception_poller` (toutes les 30 s)
- Failover dual-bot : `BOT_INSTANCE=local` (prioritaire, délai 0 s) vs `server` (secours, délai 4 s), arbitrage via `/api/bot/claim`
- Stocké dans `BotGSTAR/aboeka-bot/` (sous-dossier du repo BotGSTAR)

---

## Phase 1 : extraction + tray watchdog (2026-06-17)

**Contexte :** Arsenal Pipeline migré vers aboeka.fr → les cogs `arsenal_pipeline` et `arsenal_publisher` retirés de BotGSTAR. aboeka-bot mérite son propre repo et son propre cycle de vie.

**Ce qui a été fait :**

1. **Cogs Arsenal retirés de BotGSTAR** (`BotGSTAR/bot.py`) : seuls `cours_pipeline`, `veille_rss`, `veille_rss_politique` restaient.

2. **Tray watchdog créé** (`bot_tray.py` + `start_tray.vbs`), même architecture que BotGSTAR :
   - Spawne `python -u bot.py` en subprocess sans console
   - Auto-restart 10 s après crash
   - Icône tray colorée (vert/orange/rouge/bleu) avec lettre "G"
   - Menu clic droit : logs en direct, pause/reprise, redémarrer, startup Windows, quitter
   - `start_tray.vbs` : lanceur silencieux (`pythonw.exe`)

3. **Dossier déplacé** hors de BotGSTAR :
   - Avant : `Documents/BotGSTAR/aboeka-bot/`
   - Après : `Documents/aboeka-bot/` (même niveau que BotGSTAR)

4. **Repo GitHub indépendant** : `github.com/Gstarmix/aboeka-bot` (privé)
   - Commit initial : `f41644a`

5. **Startup Windows** : `AboekaBot_Tray.vbs` dans `%APPDATA%\...\Startup\` (durci OneDrive : attend hydratation avant de lancer)

---

## Phase 2 : bot unifié, BotGSTAR archivé (2026-06-17)

**Contexte :** Les cogs `cours_pipeline` + `veille_rss` + `veille_rss_politique` qui restaient dans BotGSTAR doivent aussi vivre dans aboeka-bot. BotGSTAR devient une archive pure.

**Ce qui a été fait :**

1. **`bot.py` converti** : `discord.Client` → `commands.Bot` (sous-classe `AboekaBot`)
   - `AboekaBot.setup_hook()` charge les 3 extensions
   - `on_message` : `await client.process_commands(message)` ajouté en tête (les commandes `!cours` / `!veille` fonctionnent dans tous les salons)
   - `intents.members = True` et `intents.messages = True` ajoutés

2. **Extensions copiées depuis BotGSTAR** :
   - `extensions/cours_pipeline.py` : pipeline COURS ISTIC (commandes `!cours`, watcher corrections, forums correction/perso)
   - `extensions/veille_rss.py` : veille RSS tech (39 sources, 4 catégories, digest 8h00)
   - `extensions/veille_rss_politique.py` : veille RSS politique (40 sources, 7 catégories Option C)

3. **`datas/` créé et peuplé** :
   - `rss_sources.yaml`, `rss_keywords.yaml` : sources + scoring RSS tech
   - `rss_sources_politique.yaml`, `rss_keywords_politique.yaml` : idem politique
   - `discord_published.json` : tracking publications corrections forums
   - `discord_perso_published.json` : tracking publications forum perso
   - `embed_spacer.png` : ressource embed
   - `rss_state.json` / `rss_state_politique.json` : exclus du git (states runtime, changent à chaque cycle)

4. **BotGSTAR définitivement arrêté** :
   - Tray BotGSTAR (PID 18196) + bot.py BotGSTAR (PID 32012) tués
   - `BotGSTAR_Tray.vbs` retiré du dossier Startup Windows
   - BotGSTAR = archive git figée

5. **Commit** : `b12df0e`, pushé sur `master`

---

## État courant (2026-06-17)

### Ce qui tourne
| Processus | Rôle |
|---|---|
| `aboeka-bot/bot_tray.py` (pythonw) | Watchdog tray : auto-restart, logs en direct |
| `aboeka-bot/bot.py` (python) | Bot unifié : liens + cours + RSS |

### Structure du repo
```
aboeka-bot/
├── bot.py                    # Entry point : AboekaBot(commands.Bot)
├── publisher.py              # Routing forums Discord + anti-doublon
├── bot_tray.py               # Tray watchdog (auto-restart, startup Windows)
├── start_tray.vbs            # Lanceur silencieux pythonw
├── requirements.txt
├── .env.example
├── CLAUDE.md                 # Instructions Claude Code → voir aussi ce fichier
├── CONTEXTE.md               # Ce fichier
├── extensions/
│   ├── cours_pipeline.py     # Cog COURS (commandes !cours, watcher, forums)
│   ├── veille_rss.py         # Cog RSS tech (commandes !veille)
│   └── veille_rss_politique.py # Cog RSS politique (commandes !vp)
├── datas/
│   ├── rss_sources.yaml      # Sources RSS tech (éditable + via !veille sources)
│   ├── rss_keywords.yaml     # Scoring mots-clés RSS tech
│   ├── rss_sources_politique.yaml
│   ├── rss_keywords_politique.yaml
│   ├── discord_published.json     # Tracking corrections publiées (forum public)
│   ├── discord_perso_published.json # Tracking forum perso
│   └── embed_spacer.png
└── data/
    ├── published_threads.json     # Anti-doublon fiches aboeka (publisher.py)
    └── reception_posted.json      # Anti-doublon réception commentaires
```

### IDs Discord clés
| Élément | ID |
|---|---|
| Guild ISTIC L1 G2 | `1466806132998672466` |
| `🔗・liens` (drops Arsenal) | `1498918445763268658` |
| `📋・bot-general` (logs bot.py) | `1518182717139976344` |
| `📋・veille-politique` | `1518182706549100776` |
| `📋・veille-tech` | `1518182709850013836` |
| `📋・cours` | `1518182713117380739` |
| `📋・logs` (ANCIEN, retire, a supprimer) | ~~`1493760267300110466`~~ |
| Rôle Admin | `1493905604241129592` |

### API aboeka.fr
- `ABOEKA_API_BASE` : `https://aboeka.fr` (prod) ou `http://127.0.0.1:3000` (local)
- `/api/bot/claim` : verrou dual-bot
- `/api/bot/generate` : lancement pipeline + poll job
- `/api/bot/reception/{dossier}` : réception commentaires (polling 30 s)

---

## Phase 3 : crash loop + .env fix + repo renommé (2026-06-18)

**Contexte :** Bot planté en boucle au démarrage (token Discord invalide). Après régénération du token, deux autres bugs bloquaient la génération de fiches.

**Ce qui a été fait :**

1. **Crash loop sur `LoginFailure` corrigé** :
   - `bot.py` : `client.run()` enveloppé dans un try/except `LoginFailure` → `sys.exit(2)` avec log d'instruction claire
   - `bot_tray.py` : `FATAL_EXIT_CODES = frozenset({2})` + état `BotState.FATAL` (icône violette)
     → toast "erreur fatale : corrige .env puis clique Redémarrer"
     → **plus d'auto-restart infini** sur ce code

2. **`.env` Windows corrigé** :
   - `ABOEKA_API_BASE` : `http://127.0.0.1:3000` → `https://aboeka.fr` (**rien n'écoute sur 3000 côté Windows**)
   - `BOT_INSTANCE` : `server` → `local` (instance primaire, pas de délai)
   - `BOT_CLAIM_DELAY_S` : `4` → `0`

3. **Repo renommé** `aboeka-bot` → `AboekaBot` sur GitHub. Remote git mis à jour :
   ```
   git remote set-url origin https://github.com/Gstarmix/AboekaBot.git
   ```

4. **BOTS_FONCTIONNEMENT.md** dans le repo `Aboeka` (`_contexte/`) mis à jour pour refléter le renommage et les corrections.

**État post-session :**
- Bot Windows tourne correctement, claim + génération atteignent `https://aboeka.fr`
- 502 sporadique sur `/api/bot/generate` en cours d'investigation côté serveur (possiblement intermittent)
- Le bot serveur (`/app/aboeka/aboeka-bot/`) reste en secours avec `BOT_INSTANCE=server` + `BOT_CLAIM_DELAY_S=4`

---

## Phase 4 : simplification, bot serveur seul pour les liens (2026-06-18)

**Contexte :** Le bot Windows ne fait rien de différent du bot serveur pour les liens TikTok/YouTube/etc. La transcription se passe toujours côté serveur. Le dual-bot n'apportait que de la redondance, pas de performance. Décision : bot serveur gère les liens en autonome, bot Windows garde uniquement `!cours` et `!veille`.

**Ce qui a été fait :**

1. **`bot.py`** : ajout de `PROCESS_LINKS` (env var). Si `false`, `on_message` ignore les liens (mais `process_commands` reste actif pour les cogs)
2. **`.env` Windows** : `PROCESS_LINKS=false`
3. **Conséquence** : plus de double traitement, plus de doublon possible, plus de compétition au claim

**Architecture résultante :**
- Bot serveur (`/app/aboeka/AboekaBot/`) : gère TOUT (liens + `!cours` + `!veille`) quand Windows éteint
- Bot Windows : `!cours` + `!veille` uniquement (PROCESS_LINKS=false)

---

## Ce qui reste à faire / pistes futures

- [ ] **Audit `#liens`** : surveiller les logs du bot serveur après simplification (à faire dans le tunnel)
- [ ] **`CLAUDE.md`** à enrichir au fil des sessions
- [ ] **Supprimer les cogs Arsenal de BotGSTAR/extensions/** si BotGSTAR est archivé proprement
- [ ] **Vérifier `!cours` et `!veille`** en production après la migration
- [ ] **Dépendances** : vérifier que `requirements.txt` liste tout (`anthropic`, `ruamel.yaml`, `feedparser`, `aiohttp`, `PyYAML`, `Pillow`…)
- [ ] **`cours_pipeline.py`** : renommer `BOTGSTAR_ROOT` → `BOT_ROOT` (cosmétique)
- [ ] Décider si `BotGSTAR/` reste sur OneDrive ou est archivé ailleurs