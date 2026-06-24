from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import re
import time as _time_mod
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import aiohttp
import discord
import feedparser
from discord.ext import commands, tasks
from ruamel.yaml import YAML
import webhook_log
ISTIC_GUILD_ID = 1466806132998672466
ADMIN_ROLE_ID = 1493905604241129592
LOG_CHANNEL_ID = int(os.environ.get("LOG_VEILLE_POLITIQUE_CHANNEL_ID", "0") or 0) or 1518182706549100776
LOG_WEBHOOK = os.environ.get("LOG_WEBHOOK_VEILLE_POLITIQUE")
if LOG_CHANNEL_ID == 1493760267300110466:
    LOG_CHANNEL_ID = 1518182706549100776
VEILLE_POL_CATEGORY_NAME = "📡 VEILLE"
VEILLE_POL_CHANNEL_NAMES: dict[str, str] = {
    "actu-chaude":          "🔥・actu-chaude",
    "arsenal-eco":          "💰・économie-veille",
    "arsenal-ecologie":     "🌱・écologie-veille",
    "arsenal-international":"🌍・international-veille",
    "arsenal-social":       "✊・social-veille",
    "arsenal-attaques":     "🎯・débats-politiques",
    "arsenal-medias":       "📺・médias-veille",
}
VALID_CATEGORIES = set(VEILLE_POL_CHANNEL_NAMES.keys())
VALID_PRIORITIES = {1, 2, 3}
VALID_LANGUAGES = {"fr", "en"}
DIGEST_MAX_ARTICLES = 10
DIGEST_HOUR = 8
DIGEST_MINUTE = 0
PARIS_TZ = ZoneInfo("Europe/Paris")
DIGEST_WINDOW_HOURS_BY_CAT: dict[str, int] = {
    "actu-chaude":            24,
    "arsenal-eco":            72,
    "arsenal-ecologie":       48,
    "arsenal-international":  24,
    "arsenal-social":         48,
    "arsenal-attaques":       48,
    "arsenal-medias":         72,
}
DIGEST_WINDOW_HOURS_DEFAULT = 48
SOURCE_ERROR_THRESHOLD = 5
HTTP_TIMEOUT_SECONDS = 15
PRUNE_DAYS = 30
KEYWORD_BOOST_POINTS = 500
EMBED_DESCRIPTION_MAX = 4096
EMBED_FIELD_NAME_MAX = 256
EMBED_FIELD_VALUE_MAX = 1024
EMBED_FIELDS_MAX = 25
ARTICLES_PER_EMBED = 5
DIGEST_MAX_EMBEDS_PER_CATEGORY = 2
MESSAGE_MAX_EMBEDS = 10
EMBED_SPACER_URL = "https://www.zupimages.net/up/26/17/j8a7.png"
SKIP_EMPTY_CATEGORIES = True
USER_AGENT = "BotGSTAR-VeillePolitique/1.0 (+gaylordaboeka@gmail.com)"
CATEGORY_TITLES: dict[str, str] = {
    "actu-chaude":           "🔥 Actu chaude",
    "arsenal-eco":           "💰 Économie",
    "arsenal-ecologie":      "🌱 Écologie",
    "arsenal-international": "🌍 International",
    "arsenal-social":        "✊ Social",
    "arsenal-attaques":      "🎯 Débats politiques",
    "arsenal-medias":        "📺 Médias",
}
CATEGORY_COLORS: dict[str, int] = {
    "actu-chaude":           0xE74C3C,
    "arsenal-eco":           0xD4A017,
    "arsenal-ecologie":      0x27AE60,
    "arsenal-international": 0x1F3A93,
    "arsenal-social":        0xCC2229,
    "arsenal-attaques":      0x8E44AD,
    "arsenal-medias":        0x7F8C8D,
}
CATEGORY_SOURCE_EMOJI: dict[str, str] = {
    "actu-chaude":           "🔥",
    "arsenal-eco":           "💰",
    "arsenal-ecologie":      "🌱",
    "arsenal-international": "🌍",
    "arsenal-social":        "✊",
    "arsenal-attaques":      "🎯",
    "arsenal-medias":        "📺",
}
PRIORITY_EMOJI = {1: "🔴", 2: "🟠", 3: "🟡"}
MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
JOURS_FR = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
SOURCES_YAML = DATAS_DIR / "rss_sources_politique.yaml"
KEYWORDS_YAML = DATAS_DIR / "rss_keywords_politique.yaml"
STATE_JSON = DATAS_DIR / "rss_state_politique.json"
logger = logging.getLogger("bot.veille_rss_politique")
@dataclass
class Source:
    id: str
    url: str
    category: str
    language: str
    priority: int
    active: bool
    notes: str = ""
@dataclass
class Article:
    guid_hash: str
    source_id: str
    title: str
    url: str
    category: str
    priority: int
    published_at: datetime
    summary: str = ""
    keyword_boost: int = 0
    @property
    def score(self) -> float:
        prio_score = (4 - self.priority) * 1000
        age_minutes = max(
            0,
            (datetime.now(timezone.utc) - self.published_at).total_seconds() / 60,
        )
        freshness = max(0, 1000 - age_minutes)
        return prio_score + freshness + self.keyword_boost
def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
def _load_state() -> dict[str, Any]:
    if not STATE_JSON.exists():
        return {
            "schema_version": 1,
            "published": {},
            "fetch_state": {},
            "last_digest_at": None,
            "channels": {},
        }
    with STATE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)
def _save_state(state: dict[str, Any]) -> None:
    _atomic_write_json(STATE_JSON, state)
def _make_yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=2, offset=0)
    y.width = 200
    return y
def _load_sources_raw() -> Any:
    if not SOURCES_YAML.exists():
        raise FileNotFoundError(f"Fichier sources introuvable : {SOURCES_YAML}")
    with SOURCES_YAML.open("r", encoding="utf-8") as f:
        return _make_yaml().load(f)
def _save_sources_raw(data: Any) -> None:
    SOURCES_YAML.parent.mkdir(parents=True, exist_ok=True)
    tmp = SOURCES_YAML.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        _make_yaml().dump(data, f)
    os.replace(tmp, SOURCES_YAML)
def _load_keywords() -> dict[str, dict[str, list[str]]]:
    if not KEYWORDS_YAML.exists():
        logger.info("rss_keywords_politique.yaml absent, scoring désactivé")
        return {cat: {"boost": [], "blacklist": []} for cat in (*VALID_CATEGORIES, "all")}
    with KEYWORDS_YAML.open("r", encoding="utf-8") as f:
        raw = _make_yaml().load(f) or {}
    result: dict[str, dict[str, list[str]]] = {}
    for cat in (*VALID_CATEGORIES, "all"):
        cat_data = raw.get(cat, {}) or {}
        result[cat] = {
            "boost": [str(k).strip() for k in (cat_data.get("boost") or []) if str(k).strip()],
            "blacklist": [str(k).strip() for k in (cat_data.get("blacklist") or []) if str(k).strip()],
        }
    return result
def _is_valid_url(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False
def _validate_source_id(sid: str) -> str | None:
    if not sid:
        return "id ne peut pas être vide"
    if len(sid) > 50:
        return "id trop long (max 50 caractères)"
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]*", sid):
        return "id doit contenir uniquement minuscules/chiffres/tirets/underscores"
    return None
def _load_sources() -> list[Source]:
    raw = _load_sources_raw()
    if raw is None:
        raise ValueError(f"{SOURCES_YAML.name} est vide")
    if not isinstance(raw, list):
        raise ValueError(f"{SOURCES_YAML.name} doit contenir une liste à la racine")
    seen_ids: set[str] = set()
    sources: list[Source] = []
    for idx, item in enumerate(raw):
        if not hasattr(item, "get"):
            raise ValueError(f"Source #{idx} n'est pas un objet")
        try:
            sid = item["id"]; url = item["url"]; category = item["category"]
            language = item["language"]; priority = item["priority"]; active = item["active"]
        except KeyError as e:
            raise ValueError(f"Source #{idx} : champ manquant {e}") from e
        if sid in seen_ids:
            raise ValueError(f"Source id dupliqué : {sid!r}")
        seen_ids.add(sid)
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Source {sid!r} : category {category!r} invalide (attendu {VALID_CATEGORIES})")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Source {sid!r} : priority {priority!r} invalide (1/2/3)")
        if language not in VALID_LANGUAGES:
            raise ValueError(f"Source {sid!r} : language {language!r} invalide (fr/en)")
        if not isinstance(active, bool):
            raise ValueError(f"Source {sid!r} : active doit être booléen")
        sources.append(Source(
            id=str(sid), url=str(url), category=str(category),
            language=str(language), priority=int(priority), active=bool(active),
            notes=str(item.get("notes", "")),
        ))
    return sources
def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    if not text or not keywords:
        return []
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]
def _apply_keyword_scoring(
    article: Article,
    keywords: dict[str, dict[str, list[str]]],
) -> tuple[bool, list[str], list[str]]:
    text = f"{article.title} {article.summary}"
    cat_kw = keywords.get(article.category, {"boost": [], "blacklist": []})
    all_kw = keywords.get("all", {"boost": [], "blacklist": []})
    blacklist_words = (cat_kw.get("blacklist", []) or []) + (all_kw.get("blacklist", []) or [])
    blacklist_matches = _match_keywords(text, blacklist_words)
    if blacklist_matches:
        return False, [], blacklist_matches
    boost_words = (cat_kw.get("boost", []) or []) + (all_kw.get("boost", []) or [])
    boost_matches = _match_keywords(text, boost_words)
    article.keyword_boost = len(boost_matches) * KEYWORD_BOOST_POINTS
    return True, boost_matches, []
def _hash_guid(guid: str) -> str:
    return hashlib.md5(guid.encode("utf-8")).hexdigest()
def _entry_to_datetime(entry: Any) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = getattr(entry, key, None) or (entry.get(key) if hasattr(entry, "get") else None)
        if struct:
            return datetime.fromtimestamp(_time_mod.mktime(struct), tz=timezone.utc)
    return datetime.now(timezone.utc)
async def _fetch_one_source(
    session: aiohttp.ClientSession,
    source: Source,
    fetch_state: dict[str, Any],
) -> tuple[list[Article], str | None]:
    state = fetch_state.setdefault(source.id, {
        "last_fetched_at": None, "last_etag": None, "last_modified": None,
        "last_error": None, "consecutive_errors": 0,
    })
    headers = {"User-Agent": USER_AGENT}
    if state.get("last_etag"):
        headers["If-None-Match"] = state["last_etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        async with session.get(source.url, headers=headers, timeout=timeout) as resp:
            if resp.status == 304:
                state["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
                state["last_error"] = None
                state["consecutive_errors"] = 0
                return [], None
            if resp.status >= 400:
                err = f"HTTP {resp.status}"
                state["last_error"] = err
                state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
                return [], err
            content = await resp.read()
            state["last_etag"] = resp.headers.get("ETag")
            state["last_modified"] = resp.headers.get("Last-Modified")
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        err = f"{type(e).__name__}: {e}"
        state["last_error"] = err
        state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
        return [], err
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        err = f"feedparser bozo: {parsed.bozo_exception!r}"
        state["last_error"] = err
        state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
        return [], err
    articles: list[Article] = []
    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title", "")
        if not guid:
            continue
        articles.append(Article(
            guid_hash=_hash_guid(guid),
            source_id=source.id,
            title=entry.get("title", "(sans titre)").strip(),
            url=entry.get("link", ""),
            category=source.category,
            priority=source.priority,
            published_at=_entry_to_datetime(entry),
            summary=entry.get("summary", "")[:300],
        ))
    state["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    state["last_error"] = None
    state["consecutive_errors"] = 0
    return articles, None
async def _fetch_all(sources: list[Source], state: dict[str, Any]) -> list[Article]:
    fetch_state = state.setdefault("fetch_state", {})
    active_sources = [s for s in sources if s.active]
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            _fetch_one_source(session, s, fetch_state) for s in active_sources
        ], return_exceptions=False)
    out: list[Article] = []
    for (arts, _err) in results:
        out.extend(arts)
    return out
def _prune_published(state: dict[str, Any]) -> None:
    published: dict[str, Any] = state.get("published", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    to_remove = []
    for h, meta in published.items():
        posted = meta.get("posted_at")
        if not posted:
            continue
        try:
            dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < cutoff:
            to_remove.append(h)
    for h in to_remove:
        del published[h]
def _filter_and_select(
    articles: list[Article], state: dict[str, Any],
) -> tuple[dict[str, list[Article]], dict[str, int]]:
    published = state.get("published", {})
    now = datetime.now(timezone.utc)
    keywords = _load_keywords()
    cutoffs: dict[str, datetime] = {
        cat: now - timedelta(hours=DIGEST_WINDOW_HOURS_BY_CAT.get(cat, DIGEST_WINDOW_HOURS_DEFAULT))
        for cat in VALID_CATEGORIES
    }
    stats = {"blacklisted": 0, "boosted": 0, "expired": 0, "duplicate": 0}
    by_category: dict[str, list[Article]] = {c: [] for c in VALID_CATEGORIES}
    for art in articles:
        if art.guid_hash in published:
            stats["duplicate"] += 1
            continue
        cutoff = cutoffs.get(art.category, now - timedelta(hours=DIGEST_WINDOW_HOURS_DEFAULT))
        if art.published_at < cutoff:
            stats["expired"] += 1
            continue
        kept, boost_matches, _bl = _apply_keyword_scoring(art, keywords)
        if not kept:
            stats["blacklisted"] += 1
            continue
        if boost_matches:
            stats["boosted"] += 1
        by_category[art.category].append(art)
    for cat in by_category:
        by_category[cat].sort(key=lambda a: a.score, reverse=True)
        by_category[cat] = by_category[cat][:DIGEST_MAX_ARTICLES]
    return by_category, stats
def _format_age(published_at: datetime) -> str:
    delta = datetime.now(timezone.utc) - published_at
    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"il y a {hours} h"
    days = hours // 24
    return f"il y a {days} j"
def _format_date_fr(dt: datetime) -> str:
    jour = JOURS_FR[dt.weekday()]
    mois = MOIS_FR[dt.month - 1]
    return f"{jour} {dt.day} {mois} {dt.year}"
def _format_article_field(article: Article) -> tuple[str, str]:
    prio_emoji = PRIORITY_EMOJI.get(article.priority, "⚪")
    title = article.title.strip()
    if len(title) > 200:
        title = title[:197] + "…"
    age = _format_age(article.published_at)
    source_emoji = CATEGORY_SOURCE_EMOJI.get(article.category, "📰")
    flag = "🇫🇷"
    value = (
        f"{prio_emoji} [**{title}**]({article.url})\n"
        f"{source_emoji} `{article.source_id}` · {flag} · _{age}_"
    )
    return "​", value
def _build_digest_embeds(category: str, articles: list[Article]) -> list[discord.Embed]:
    now_paris = datetime.now(PARIS_TZ)
    base_title = f"{CATEGORY_TITLES[category]} : {_format_date_fr(now_paris)}"
    color = CATEGORY_COLORS[category]
    timestamp = datetime.now(timezone.utc)
    if not articles:
        embed = discord.Embed(
            title=base_title,
            description="_Aucun article dans la fenêtre de fraîcheur._",
            color=color, timestamp=timestamp,
        )
        embed.set_footer(text="0 article · 0 source")
        embed.set_image(url=EMBED_SPACER_URL)
        return [embed]
    sources_count = len({a.source_id for a in articles})
    chunks = [articles[i:i + ARTICLES_PER_EMBED] for i in range(0, len(articles), ARTICLES_PER_EMBED)]
    chunks = chunks[:DIGEST_MAX_EMBEDS_PER_CATEGORY]
    total_displayed = sum(len(c) for c in chunks)
    embeds: list[discord.Embed] = []
    n_chunks = len(chunks)
    for idx, chunk_articles in enumerate(chunks, start=1):
        is_first, is_last = idx == 1, idx == n_chunks
        embed_kwargs = {"color": color}
        if is_first:
            embed_kwargs["title"] = base_title
        if is_last:
            embed_kwargs["timestamp"] = timestamp
        embed = discord.Embed(**embed_kwargs)
        for art in chunk_articles:
            field_name, field_value = _format_article_field(art)
            if len(field_name) > EMBED_FIELD_NAME_MAX:
                field_name = field_name[:EMBED_FIELD_NAME_MAX - 1] + "…"
            if len(field_value) > EMBED_FIELD_VALUE_MAX:
                field_value = field_value[:EMBED_FIELD_VALUE_MAX - 1] + "…"
            embed.add_field(name=field_name, value=field_value, inline=False)
        if is_last:
            embed.set_footer(text=f"{total_displayed} article(s) · {sources_count} source(s)")
        embed.set_image(url=EMBED_SPACER_URL)
        embeds.append(embed)
    return embeds
def _normalize_channel_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())
def _find_category(guild: discord.Guild, name: str) -> discord.CategoryChannel | None:
    target = _normalize_channel_name(name)
    for cat in guild.categories:
        if _normalize_channel_name(cat.name) == target:
            return cat
        if target in _normalize_channel_name(cat.name) or _normalize_channel_name(cat.name) in target:
            return cat
    return None
def _find_text_channel(guild: discord.Guild, name: str, parent: discord.CategoryChannel | None = None) -> discord.TextChannel | None:
    target = _normalize_channel_name(name)
    candidates = parent.channels if parent else guild.channels
    for ch in candidates:
        if isinstance(ch, discord.TextChannel) and _normalize_channel_name(ch.name) == target:
            return ch
    return None
class VeilleRSSPolitique(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._sources: list[Source] = []
        try:
            self._reload_sources()
        except Exception:
            logger.exception("Init : échec chargement sources")
    def cog_unload(self):
        if self._daily_digest_loop.is_running():
            self._daily_digest_loop.cancel()
            logger.info("[politique] Loop digest quotidien arrêté (cog_unload)")
    def _reload_sources(self) -> None:
        self._sources = _load_sources()
        logger.info(
            "[politique] %d sources chargées (%d actives)",
            len(self._sources), sum(1 for s in self._sources if s.active),
        )
    async def cog_check(self, ctx: commands.Context) -> bool:
        if not ctx.guild or ctx.guild.id != ISTIC_GUILD_ID:
            return False
        if await self.bot.is_owner(ctx.author):
            return True
        if isinstance(ctx.author, discord.Member):
            return ctx.author.guild_permissions.administrator
        return False
    async def _log(
        self,
        message: str,
        *,
        title: str | None = None,
        color: int | None = None,
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        guild = self.bot.get_guild(ISTIC_GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(LOG_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title=title or "📡 Veille politique",
            description=message[:EMBED_DESCRIPTION_MAX] if message else None,
            color=color if color is not None else 0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name[:EMBED_FIELD_NAME_MAX], value=value[:EMBED_FIELD_VALUE_MAX], inline=inline)
        embed.set_footer(text="veille_rss_politique")
        await webhook_log.send_log(LOG_WEBHOOK, channel, embed=embed, username="Veille politique")
    async def _run_fetch_cycle(self) -> tuple[dict[str, list[Article]], dict[str, int]]:
        state = _load_state()
        _prune_published(state)
        articles = await _fetch_all(self._sources, state)
        by_category, stats = _filter_and_select(articles, state)
        _save_state(state)
        await self._auto_disable_failing_sources(state)
        return by_category, stats
    async def _auto_disable_failing_sources(self, state: dict[str, Any]) -> None:
        fetch_state = state.get("fetch_state", {})
        for sid, fs in fetch_state.items():
            if fs.get("consecutive_errors", 0) >= SOURCE_ERROR_THRESHOLD:
                source = next((s for s in self._sources if s.id == sid), None)
                if source and source.active:
                    source.active = False
                    await self._log(
                        f"La source **`{sid}`** a été désactivée automatiquement après "
                        f"**{SOURCE_ERROR_THRESHOLD}** erreurs consécutives.",
                        title="⚠️ Veille politique : Source désactivée",
                        color=0xE67E22,
                        fields=[
                            ("Source", f"`{sid}`", True),
                            ("Erreurs", str(fs.get('consecutive_errors', '?')), True),
                            ("Dernière erreur", f"`{fs.get('last_error', '?')}`", False),
                        ],
                    )
    def _digest_already_today(self, state: dict[str, Any]) -> bool:
        last = state.get("last_digest_at")
        if not last:
            return False
        try:
            last_utc = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            return False
        return last_utc.astimezone(PARIS_TZ).date() == datetime.now(PARIS_TZ).date()
    async def _resolve_channels(self, guild: discord.Guild) -> dict[str, discord.TextChannel]:
        state = _load_state()
        cache = state.setdefault("channels", {})
        out: dict[str, discord.TextChannel] = {}
        cat = _find_category(guild, VEILLE_POL_CATEGORY_NAME)
        for category, channel_name in VEILLE_POL_CHANNEL_NAMES.items():
            cached_id = cache.get(category)
            if cached_id:
                ch = guild.get_channel(int(cached_id))
                if isinstance(ch, discord.TextChannel):
                    out[category] = ch
                    continue
            ch = _find_text_channel(guild, channel_name, parent=cat)
            if ch:
                out[category] = ch
                cache[category] = str(ch.id)
        _save_state(state)
        return out
    async def _post_digests(
        self, by_category: dict[str, list[Article]], state: dict[str, Any],
    ) -> dict[str, int]:
        guild = self.bot.get_guild(ISTIC_GUILD_ID)
        if not guild:
            await self._log("❌ Guild Veille introuvable, abandon digest",
                            title="❌ Veille politique : erreur", color=0xE74C3C)
            return {}
        channels = await self._resolve_channels(guild)
        posted_counts: dict[str, int] = {}
        published = state.setdefault("published", {})
        now_iso = datetime.now(timezone.utc).isoformat()
        for category, articles in by_category.items():
            channel = channels.get(category)
            if not channel:
                logger.warning("[politique] Salon %s non résolu, skip", category)
                await self._log(
                    f"Salon `{VEILLE_POL_CHANNEL_NAMES[category]}` introuvable. "
                    f"Lance `!veille_pol setup-channels` pour le créer.",
                    title=f"⚠️ Veille politique : Salon manquant ({category})",
                    color=0xE67E22,
                )
                continue
            if SKIP_EMPTY_CATEGORIES and not articles:
                posted_counts[category] = 0
                continue
            embeds = _build_digest_embeds(category, articles)
            first_msg_id: str | None = None
            send_failed = False
            for batch_start in range(0, len(embeds), MESSAGE_MAX_EMBEDS):
                batch = embeds[batch_start:batch_start + MESSAGE_MAX_EMBEDS]
                try:
                    msg = await channel.send(embeds=batch)
                    if first_msg_id is None:
                        first_msg_id = str(msg.id)
                except discord.HTTPException:
                    logger.exception("[politique] Échec envoi digest %s", category)
                    send_failed = True
                    break
            if send_failed:
                continue
            for art in articles:
                published[art.guid_hash] = {
                    "source_id": art.source_id, "title": art.title, "url": art.url,
                    "category": art.category, "published_at": art.published_at.isoformat(),
                    "posted_at": now_iso, "message_id": first_msg_id or "?",
                }
            posted_counts[category] = len(articles)
        state["last_digest_at"] = now_iso
        _save_state(state)
        return posted_counts
    async def _post_morning_summary(self, state: dict[str, Any], posted_counts: dict[str, int], stats: dict[str, int]) -> None:
        fetch_state = state.get("fetch_state", {})
        failing = [(sid, fs.get("consecutive_errors", 0), fs.get("last_error", "?"))
                   for sid, fs in fetch_state.items() if fs.get("consecutive_errors", 0) > 0]
        counts_lines = []
        total_posted = 0
        for cat in VEILLE_POL_CHANNEL_NAMES:
            n = posted_counts.get(cat, 0)
            emoji = CATEGORY_SOURCE_EMOJI[cat]
            label = " _(vide)_" if n == 0 else ""
            counts_lines.append(f"{emoji} **{cat}** : `{n}`{label}")
            total_posted += n
        counts_field = "\n".join(counts_lines)
        if failing:
            sources_lines = ["⚠️ Sources en erreur :"]
            for sid, errors, last_err in failing:
                short_err = (last_err or "?")[:80]
                sources_lines.append(f"• `{sid}` : {errors} err., `{short_err}`")
            sources_field = "\n".join(sources_lines)
            color = 0xF39C12
        else:
            sources_field = "✅ Toutes les sources fonctionnent."
            color = 0x2ECC71
        active = sum(1 for s in self._sources if s.active)
        stats_field = (
            f"Articles trackés (30j) : `{len(state.get('published', {}))}`\n"
            f"Sources actives : `{active}` / `{len(self._sources)}`\n"
            f"Filtres : `{stats.get('duplicate',0)}` doublons · "
            f"`{stats.get('expired',0)}` expirés · "
            f"`{stats.get('blacklisted',0)}` blacklistés · "
            f"`{stats.get('boosted',0)}` boostés"
        )
        await self._log(
            f"Récapitulatif du cycle automatique. **{total_posted}** article(s) posté(s) au total.",
            title="📡 Veille politique : Digest matinal",
            color=color,
            fields=[
                ("Articles par catégorie", counts_field, False),
                ("Sources", sources_field, False),
                ("Stats globales", stats_field, False),
            ],
        )
    async def _run_daily_cycle(self, source: str) -> dict[str, int]:
        state = _load_state()
        if source == "auto" and self._digest_already_today(state):
            await self._log(
                "Le digest a déjà été posté aujourd'hui. Aucune action.",
                title="ℹ️ Veille politique : Digest skip",
                color=0x95A5A6,
            )
            return {}
        by_category, stats = await self._run_fetch_cycle()
        state = _load_state()
        posted_counts = await self._post_digests(by_category, state)
        if source == "auto":
            await self._post_morning_summary(state, posted_counts, stats)
        return posted_counts
    @tasks.loop(time=time(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, tzinfo=PARIS_TZ))
    async def _daily_digest_loop(self):
        logger.info("[politique] Loop digest déclenché à %s", datetime.now(PARIS_TZ))
        if self._lock.locked():
            await self._log(
                "Digest auto skip : un cycle manuel était en cours au moment du déclenchement.",
                title="⚠️ Veille politique : Conflit cycle", color=0xE67E22,
            )
            return
        async with self._lock:
            try:
                await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("[politique] Erreur cycle auto")
                await self._log(
                    f"Une erreur est survenue lors du cycle automatique :\n```\n{type(e).__name__}: {e}\n```",
                    title="❌ Veille politique : Erreur digest auto", color=0xE74C3C,
                )
    @_daily_digest_loop.before_loop
    async def _before_daily_loop(self):
        await self.bot.wait_until_ready()
        logger.info("[politique] Loop digest prêt (heure cible : %02d:%02d Paris)", DIGEST_HOUR, DIGEST_MINUTE)
    @commands.Cog.listener()
    async def on_ready(self):
        if not self._daily_digest_loop.is_running():
            self._daily_digest_loop.start()
            logger.info("[politique] Loop digest démarré")
        else:
            return
        now_paris = datetime.now(PARIS_TZ)
        target = now_paris.replace(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, second=0, microsecond=0)
        if now_paris <= target:
            return
        state = _load_state()
        if self._digest_already_today(state):
            return
        await self._log(
            f"Bot démarré à **{now_paris.strftime('%H:%M')}** (Paris) après l'heure du digest "
            f"({DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d}). Exécution immédiate.",
            title="🔁 Veille politique : Catch-up", color=0xF39C12,
        )
        if self._lock.locked():
            return
        async with self._lock:
            try:
                await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("[politique] Erreur catch-up")
                await self._log(
                    f"Erreur catch-up :\n```\n{type(e).__name__}: {e}\n```",
                    title="❌ Veille politique : Erreur catch-up", color=0xE74C3C,
                )
    @commands.group(name="veille_pol", aliases=["vp", "veillepol"], invoke_without_command=True)
    async def vp_group(self, ctx: commands.Context):
        await ctx.send(
            "**Commandes `!veille_pol` (alias `!vp`) :**\n"
            "`setup-channels` : crée la catégorie + 5 salons s'ils n'existent pas\n"
            "`fetch-now` : cycle manuel (test rapide, pas de récap logs)\n"
            "`trigger-now` : déclenche le cycle 'auto' (avec récap dans #logs)\n"
            "`status` : état des sources et compteurs\n"
            "`reload` : recharge YAML sources + keywords\n"
            "`sources …` : list/add/remove/toggle/test\n"
            "`keywords` : affiche les mots-clés boost + blacklist\n"
            "\n"
            f"_Digest auto programmé à {DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d} (Paris) chaque jour._"
        )
    @vp_group.command(name="setup-channels")
    async def setup_channels(self, ctx: commands.Context):
        guild = ctx.guild
        if not guild:
            return
        await ctx.send("🔄 Setup des salons en cours…")
        cat = _find_category(guild, VEILLE_POL_CATEGORY_NAME)
        created_cat = False
        if not cat:
            cat = await guild.create_category(VEILLE_POL_CATEGORY_NAME, reason="Setup veille politique")
            created_cat = True
        created: list[str] = []
        existing: list[str] = []
        for category, channel_name in VEILLE_POL_CHANNEL_NAMES.items():
            ch = _find_text_channel(guild, channel_name, parent=cat)
            if ch:
                existing.append(f"`#{ch.name}`")
                continue
            new_ch = await guild.create_text_channel(
                channel_name, category=cat,
                topic=f"Veille politique : {CATEGORY_TITLES[category]}",
                reason="Setup veille politique",
            )
            created.append(f"`#{new_ch.name}`")
        state = _load_state()
        state["channels"] = {}
        _save_state(state)
        await self._resolve_channels(guild)
        msg_lines = []
        if created_cat:
            msg_lines.append(f"📁 Catégorie créée : **{cat.name}**")
        else:
            msg_lines.append(f"📁 Catégorie déjà présente : **{cat.name}**")
        if created:
            msg_lines.append(f"🆕 Salons créés : {', '.join(created)}")
        if existing:
            msg_lines.append(f"✅ Salons déjà présents : {', '.join(existing)}")
        await ctx.send("\n".join(msg_lines))
        await self._log(
            f"Setup-channels exécuté par {ctx.author.mention}.",
            title="🛠️ Veille politique : Setup-channels",
            color=0x2ECC71 if (created or existing) else 0xE67E22,
            fields=[
                ("Catégorie", "créée" if created_cat else "existante", True),
                ("Salons créés", str(len(created)), True),
                ("Salons existants", str(len(existing)), True),
            ],
        )
    @vp_group.command(name="fetch-now")
    async def fetch_now(self, ctx: commands.Context):
        if self._lock.locked():
            await ctx.send("⏳ Un cycle est déjà en cours, patiente.")
            return
        async with self._lock:
            await ctx.send("🔄 Cycle de fetch en cours…")
            try:
                posted = await self._run_daily_cycle(source="manual")
            except Exception as e:
                logger.exception("[politique] Erreur fetch")
                await ctx.send(f"❌ Erreur : `{type(e).__name__}: {e}`")
                return
            summary = " · ".join(f"{cat}={n}" for cat, n in posted.items()) or "rien posté"
            await ctx.send(f"✅ Cycle terminé. Posté : {summary}")
    @vp_group.command(name="trigger-now")
    async def trigger_now(self, ctx: commands.Context):
        if self._lock.locked():
            await ctx.send("⏳ Un cycle est déjà en cours, patiente.")
            return
        async with self._lock:
            await ctx.send("🔄 Cycle 'auto' manuel en cours…")
            try:
                posted = await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("[politique] Erreur trigger")
                await ctx.send(f"❌ Erreur : `{type(e).__name__}: {e}`")
                return
            summary = " · ".join(f"{cat}={n}" for cat, n in posted.items()) or "rien posté (déjà fait)"
            await ctx.send(f"✅ Cycle auto terminé. Posté : {summary}\n_(Vérifie #logs pour le récap matinal.)_")
    @vp_group.command(name="status")
    async def status(self, ctx: commands.Context):
        state = _load_state()
        fetch_state = state.get("fetch_state", {})
        last_digest = state.get("last_digest_at") or "jamais"
        published_count = len(state.get("published", {}))
        lines = [
            f"**Veille politique : État**",
            f"Dernier digest : `{last_digest}`",
            f"Articles trackés (30j) : `{published_count}`",
            f"Sources : `{sum(1 for s in self._sources if s.active)}` actives / `{len(self._sources)}` total",
            "",
            "**Détail par source :**",
        ]
        for s in self._sources:
            fs = fetch_state.get(s.id, {})
            errors = fs.get("consecutive_errors", 0)
            last_err = fs.get("last_error") or "OK"
            emoji = "✅" if s.active and errors == 0 else ("❌" if not s.active else "⚠️")
            lines.append(f"{emoji} `{s.id}` ({s.category}, prio {s.priority}) : erreurs : {errors}, {last_err}")
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n…(tronqué)"
        await ctx.send(text)
    @vp_group.command(name="reload")
    async def reload(self, ctx: commands.Context):
        try:
            self._reload_sources()
            await ctx.send(
                f"✅ Rechargé : {len(self._sources)} sources "
                f"({sum(1 for s in self._sources if s.active)} actives)"
            )
        except Exception as e:
            await ctx.send(f"❌ Erreur de rechargement : `{type(e).__name__}: {e}`")
    @vp_group.group(name="sources", invoke_without_command=True)
    async def sources_group(self, ctx: commands.Context):
        await ctx.send(
            "**Commandes `!veille_pol sources` :**\n"
            "`list` · `add <id> <url> <cat> [prio]` · `remove <id>` · "
            "`toggle <id>` · `test <url>`\n"
            f"_Catégories : {', '.join(sorted(VALID_CATEGORIES))}_"
        )
    @sources_group.command(name="list")
    async def sources_list(self, ctx: commands.Context):
        by_cat: dict[str, list[Source]] = {c: [] for c in VALID_CATEGORIES}
        for s in self._sources:
            by_cat.setdefault(s.category, []).append(s)
        embed = discord.Embed(
            title="📡 Sources veille politique",
            color=0x5865F2, timestamp=datetime.now(timezone.utc),
        )
        for cat in VEILLE_POL_CHANNEL_NAMES:
            sources = sorted(by_cat.get(cat, []), key=lambda x: (x.priority, x.id))
            if not sources:
                continue
            lines = []
            for s in sources:
                check = "✅" if s.active else "⛔"
                prio = PRIORITY_EMOJI.get(s.priority, "⚪")
                lines.append(f"{check} {prio} `{s.id}`")
            embed.add_field(
                name=f"{CATEGORY_SOURCE_EMOJI[cat]} {cat} ({len(sources)})",
                value="\n".join(lines)[:EMBED_FIELD_VALUE_MAX],
                inline=True,
            )
        total = len(self._sources)
        actives = sum(1 for s in self._sources if s.active)
        embed.set_footer(text=f"{total} sources · {actives} actives")
        await ctx.send(embed=embed)
    @sources_group.command(name="add")
    async def sources_add(self, ctx: commands.Context, sid: str, url: str, category: str, priority: int = 2):
        err = _validate_source_id(sid)
        if err:
            await ctx.send(f"❌ id invalide : {err}")
            return
        if category not in VALID_CATEGORIES:
            await ctx.send(f"❌ category invalide : `{category}` (attendu : {', '.join(sorted(VALID_CATEGORIES))})")
            return
        if priority not in VALID_PRIORITIES:
            await ctx.send(f"❌ priority invalide : `{priority}` (attendu : 1, 2 ou 3)")
            return
        if any(s.id == sid for s in self._sources):
            await ctx.send(f"❌ source `{sid}` existe déjà")
            return
        await ctx.send(f"🧪 Test de l'URL en cours…")
        ok, msg, n = await self._test_source_url(url)
        if not ok:
            await ctx.send(f"❌ Test échoué : {msg}\n→ Source non ajoutée.")
            return
        raw = _load_sources_raw()
        raw.append({
            "id": sid, "url": url, "category": category,
            "language": "fr", "priority": priority, "active": True,
            "notes": f"Ajouté via !veille_pol sources add le {datetime.now(PARIS_TZ).strftime('%Y-%m-%d')}",
        })
        _save_sources_raw(raw)
        self._reload_sources()
        await ctx.send(f"✅ Source `{sid}` ajoutée ({n} articles au test). Total : {len(self._sources)}.")
        await self._log(
            f"Source `{sid}` ajoutée par {ctx.author.mention}.",
            title="➕ Veille politique : Source ajoutée", color=0x2ECC71,
            fields=[
                ("ID", f"`{sid}`", True),
                ("Catégorie", category, True),
                ("Priorité", str(priority), True),
                ("URL", url[:200], False),
                ("Articles test", str(n), True),
            ],
        )
    @sources_group.command(name="remove")
    async def sources_remove(self, ctx: commands.Context, sid: str):
        if not any(s.id == sid for s in self._sources):
            await ctx.send(f"❌ source `{sid}` introuvable")
            return
        await ctx.send(f"⚠️ Confirme la suppression de `{sid}` en réagissant ✅ dans 30s.")
        msg = await ctx.send("⏳")
        await msg.add_reaction("✅")
        try:
            def chk(r, u):
                return u == ctx.author and str(r.emoji) == "✅" and r.message.id == msg.id
            await self.bot.wait_for("reaction_add", timeout=30.0, check=chk)
        except asyncio.TimeoutError:
            await ctx.send("⏰ Timeout, suppression annulée.")
            return
        raw = _load_sources_raw()
        for idx, item in enumerate(raw):
            if hasattr(item, "get") and item.get("id") == sid:
                del raw[idx]
                break
        _save_sources_raw(raw)
        self._reload_sources()
        await ctx.send(f"🗑️ Source `{sid}` supprimée. Total : {len(self._sources)}.")
        await self._log(
            f"Source `{sid}` supprimée par {ctx.author.mention}.",
            title="🗑️ Veille politique : Source supprimée", color=0xE67E22,
        )
    @sources_group.command(name="toggle")
    async def sources_toggle(self, ctx: commands.Context, sid: str):
        raw = _load_sources_raw()
        target = None
        for item in raw:
            if hasattr(item, "get") and item.get("id") == sid:
                target = item; break
        if target is None:
            await ctx.send(f"❌ source `{sid}` introuvable")
            return
        new_state = not bool(target.get("active", True))
        target["active"] = new_state
        _save_sources_raw(raw)
        self._reload_sources()
        await ctx.send(f"🔁 Source `{sid}` → **{'active' if new_state else 'désactivée'}**.")
    @sources_group.command(name="test")
    async def sources_test(self, ctx: commands.Context, url: str):
        await ctx.send(f"🧪 Test de `{url}`…")
        ok, msg, n = await self._test_source_url(url)
        emoji = "✅" if ok else "❌"
        await ctx.send(f"{emoji} {msg}")
    async def _test_source_url(self, url: str, timeout_sec: int = HTTP_TIMEOUT_SECONDS) -> tuple[bool, str, int]:
        if not _is_valid_url(url):
            return False, "URL invalide (http/https requis)", 0
        fake = Source(id="__test__", url=url, category="lfi", language="fr", priority=1, active=True)
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_sec)
            connector = aiohttp.TCPConnector(limit=1)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                articles, err = await _fetch_one_source(session, fake, {})
        except Exception as e:
            return False, f"Erreur réseau : {type(e).__name__}: {e}", 0
        if err:
            return False, f"Erreur fetch : {err}", 0
        if not articles:
            return False, "Flux récupéré mais 0 article (flux vide ?)", 0
        return True, f"OK : {len(articles)} articles récupérés", len(articles)
    @vp_group.command(name="keywords")
    async def keywords_cmd(self, ctx: commands.Context):
        kw = _load_keywords()
        embed = discord.Embed(
            title="🔑 Mots-clés veille politique",
            color=0x5865F2, timestamp=datetime.now(timezone.utc),
        )
        for cat in (*VEILLE_POL_CHANNEL_NAMES, "all"):
            cat_kw = kw.get(cat, {"boost": [], "blacklist": []})
            boost = cat_kw.get("boost", [])
            blacklist = cat_kw.get("blacklist", [])
            if not boost and not blacklist:
                continue
            value = ""
            if boost:
                value += f"**Boost** ({len(boost)}) : " + ", ".join(f"`{k}`" for k in boost[:8])
                if len(boost) > 8:
                    value += f", … +{len(boost) - 8}"
                value += "\n"
            if blacklist:
                value += f"**Blacklist** ({len(blacklist)}) : " + ", ".join(f"`{k}`" for k in blacklist[:5])
                if len(blacklist) > 5:
                    value += f", … +{len(blacklist) - 5}"
            embed.add_field(
                name=f"{CATEGORY_SOURCE_EMOJI.get(cat, '🌐')} {cat}",
                value=value[:EMBED_FIELD_VALUE_MAX],
                inline=False,
            )
        await ctx.send(embed=embed)
async def setup(bot: commands.Bot):
    await bot.add_cog(VeilleRSSPolitique(bot))