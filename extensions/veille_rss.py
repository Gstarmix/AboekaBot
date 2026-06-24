from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import time as _time_mod
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import aiohttp
import discord
import feedparser
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from discord.ext import commands, tasks
import webhook_log
ISTIC_GUILD_ID = 1466806132998672466
ADMIN_ROLE_ID = 1493905604241129592
LOG_CHANNEL_ID = int(os.environ.get("LOG_VEILLE_TECH_CHANNEL_ID", "0") or 0) or 1518182709850013836
LOG_WEBHOOK = os.environ.get("LOG_WEBHOOK_VEILLE_TECH")
if LOG_CHANNEL_ID == 1493760267300110466:
    LOG_CHANNEL_ID = 1518182709850013836
VEILLE_CHANNELS: dict[str, int] = {
    "cyber": 1497581112224911462,
    "ia":    1497581185189150781,
    "dev":   1497581209927159949,
    "tech":  1497581249521258737,
}
VALID_CATEGORIES = {"cyber", "ia", "dev", "tech"}
VALID_PRIORITIES = {1, 2, 3}
VALID_LANGUAGES = {"fr", "en"}
DIGEST_MAX_ARTICLES = 10
DIGEST_WINDOW_HOURS_BY_CAT: dict[str, int] = {
    "cyber": 72,
    "ia":    72,
    "dev":   72,
    "tech":  24,
}
DIGEST_WINDOW_HOURS_DEFAULT = 24
SOURCE_ERROR_THRESHOLD = 5
HTTP_TIMEOUT_SECONDS = 15
PRUNE_DAYS = 30
KEYWORD_BOOST_POINTS = 500
EMBED_DESCRIPTION_MAX = 4096
EMBED_FIELD_NAME_MAX = 256
EMBED_FIELD_VALUE_MAX = 1024
EMBED_TOTAL_MAX = 6000
EMBED_FIELDS_MAX = 25
ARTICLES_PER_EMBED = 5
DIGEST_MAX_EMBEDS_PER_CATEGORY = 2
MESSAGE_MAX_EMBEDS = 10
EMBED_SPACER_URL = "https://www.zupimages.net/up/26/17/j8a7.png"
MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
JOURS_FR = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]
PARIS_TZ = ZoneInfo("Europe/Paris")
DIGEST_HOUR = 8
DIGEST_MINUTE = 0
SKIP_EMPTY_CATEGORIES = True
USER_AGENT = "BotGSTAR-VeilleRSS/1.0 (+gaylordaboeka@gmail.com)"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
SOURCES_YAML = DATAS_DIR / "rss_sources.yaml"
KEYWORDS_YAML = DATAS_DIR / "rss_keywords.yaml"
STATE_JSON = DATAS_DIR / "rss_state.json"
PRIORITY_EMOJI = {1: "🔴", 2: "🟠", 3: "🟡"}
CATEGORY_COLORS = {
    "cyber": 0xC0392B,
    "ia":    0x8E44AD,
    "dev":   0x27AE60,
    "tech":  0x2980B9,
}
CATEGORY_TITLES = {
    "cyber": "📰 Veille cybersécurité",
    "ia":    "🤖 Veille IA",
    "dev":   "💻 Veille dev",
    "tech":  "📱 Tech news",
}
CATEGORY_SOURCE_EMOJI = {
    "cyber": "📰",
    "ia":    "🤖",
    "dev":   "💻",
    "tech":  "📱",
}
logger = logging.getLogger("bot.veille_rss")
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
        }
    with STATE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)
def _save_state(state: dict[str, Any]) -> None:
    _atomic_write_json(STATE_JSON, state)
def _make_yaml() -> YAML:
    yaml_inst = YAML()
    yaml_inst.preserve_quotes = True
    yaml_inst.indent(mapping=2, sequence=2, offset=0)
    yaml_inst.width = 200
    return yaml_inst
def _load_sources_raw() -> Any:
    if not SOURCES_YAML.exists():
        raise FileNotFoundError(f"Fichier sources introuvable : {SOURCES_YAML}")
    yaml_inst = _make_yaml()
    with SOURCES_YAML.open("r", encoding="utf-8") as f:
        return yaml_inst.load(f)
def _save_sources_raw(data: Any) -> None:
    yaml_inst = _make_yaml()
    SOURCES_YAML.parent.mkdir(parents=True, exist_ok=True)
    tmp = SOURCES_YAML.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml_inst.dump(data, f)
    os.replace(tmp, SOURCES_YAML)
def _find_source_index(raw: Any, source_id: str) -> int:
    for idx, item in enumerate(raw):
        if hasattr(item, "get") and item.get("id") == source_id:
            return idx
    return -1
def _is_valid_url(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False
def _validate_source_id(source_id: str) -> str | None:
    if not source_id:
        return "id ne peut pas être vide"
    if len(source_id) > 50:
        return "id trop long (max 50 caractères)"
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]*", source_id):
        return (
            "id doit contenir uniquement minuscules, chiffres, tirets, "
            "underscores ; commencer par lettre ou chiffre"
        )
    return None
def _load_keywords() -> dict[str, dict[str, list[str]]]:
    if not KEYWORDS_YAML.exists():
        logger.info("rss_keywords.yaml absent, scoring mots-clés désactivé")
        return {cat: {"boost": [], "blacklist": []} for cat in (*VALID_CATEGORIES, "all")}
    yaml_inst = _make_yaml()
    with KEYWORDS_YAML.open("r", encoding="utf-8") as f:
        raw = yaml_inst.load(f) or {}
    result: dict[str, dict[str, list[str]]] = {}
    for cat in (*VALID_CATEGORIES, "all"):
        cat_data = raw.get(cat, {}) or {}
        result[cat] = {
            "boost": [str(k).strip() for k in (cat_data.get("boost") or []) if str(k).strip()],
            "blacklist": [str(k).strip() for k in (cat_data.get("blacklist") or []) if str(k).strip()],
        }
    return result
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
def _load_sources() -> list[Source]:
    raw = _load_sources_raw()
    if raw is None:
        raise ValueError("rss_sources.yaml est vide")
    if not isinstance(raw, list):
        raise ValueError("rss_sources.yaml doit contenir une liste à la racine")
    seen_ids: set[str] = set()
    sources: list[Source] = []
    for idx, item in enumerate(raw):
        if not hasattr(item, "get"):
            raise ValueError(f"Source #{idx} n'est pas un objet")
        try:
            sid = item["id"]
            url = item["url"]
            category = item["category"]
            language = item["language"]
            priority = item["priority"]
            active = item["active"]
        except KeyError as e:
            raise ValueError(f"Source #{idx} : champ manquant {e}") from e
        if sid in seen_ids:
            raise ValueError(f"Source id dupliqué : {sid!r}")
        seen_ids.add(sid)
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Source {sid!r} : category {category!r} invalide "
                f"(attendu : {VALID_CATEGORIES})"
            )
        if priority not in VALID_PRIORITIES:
            raise ValueError(
                f"Source {sid!r} : priority {priority!r} invalide "
                f"(attendu : 1, 2 ou 3)"
            )
        if language not in VALID_LANGUAGES:
            raise ValueError(
                f"Source {sid!r} : language {language!r} invalide "
                f"(attendu : fr, en)"
            )
        if not isinstance(active, bool):
            raise ValueError(f"Source {sid!r} : active doit être booléen")
        sources.append(Source(
            id=str(sid), url=str(url), category=str(category),
            language=str(language), priority=int(priority), active=bool(active),
            notes=str(item.get("notes", "")),
        ))
    return sources
def _hash_guid(guid: str) -> str:
    return hashlib.md5(guid.encode("utf-8")).hexdigest()
def _entry_to_datetime(entry: Any) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = getattr(entry, key, None) or entry.get(key) if hasattr(entry, "get") else None
        if struct:
            return datetime.fromtimestamp(_time_mod.mktime(struct), tz=timezone.utc)
    return datetime.now(timezone.utc)
async def _fetch_one_source(
    session: aiohttp.ClientSession,
    source: Source,
    fetch_state: dict[str, Any],
) -> tuple[list[Article], str | None]:
    state = fetch_state.setdefault(source.id, {
        "last_fetched_at": None,
        "last_etag": None,
        "last_modified": None,
        "last_error": None,
        "consecutive_errors": 0,
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
        guid = (
            entry.get("id")
            or entry.get("guid")
            or entry.get("link")
            or entry.get("title", "")
        )
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
    all_articles: list[Article] = []
    for (articles, _err) in results:
        all_articles.extend(articles)
    return all_articles
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
    articles: list[Article],
    state: dict[str, Any],
) -> dict[str, list[Article]]:
    published = state.get("published", {})
    now = datetime.now(timezone.utc)
    keywords = _load_keywords()
    cutoffs: dict[str, datetime] = {}
    for cat in VALID_CATEGORIES:
        hours = DIGEST_WINDOW_HOURS_BY_CAT.get(cat, DIGEST_WINDOW_HOURS_DEFAULT)
        cutoffs[cat] = now - timedelta(hours=hours)
    stats_blacklisted = 0
    stats_boosted = 0
    by_category: dict[str, list[Article]] = {c: [] for c in VALID_CATEGORIES}
    for art in articles:
        if art.guid_hash in published:
            continue
        cutoff = cutoffs.get(art.category, now - timedelta(hours=DIGEST_WINDOW_HOURS_DEFAULT))
        if art.published_at < cutoff:
            continue
        kept, boost_matches, blacklist_matches = _apply_keyword_scoring(art, keywords)
        if not kept:
            stats_blacklisted += 1
            logger.debug(
                "Article blacklisté: %s (mots: %s)",
                art.title[:60], blacklist_matches,
            )
            continue
        if boost_matches:
            stats_boosted += 1
            logger.debug(
                "Article boosté +%d: %s (mots: %s)",
                art.keyword_boost, art.title[:60], boost_matches,
            )
        by_category[art.category].append(art)
    if stats_blacklisted or stats_boosted:
        logger.info(
            "R-D scoring : %d articles boostés, %d articles blacklistés",
            stats_boosted, stats_blacklisted,
        )
    for cat in by_category:
        by_category[cat].sort(key=lambda a: a.score, reverse=True)
        by_category[cat] = by_category[cat][:DIGEST_MAX_ARTICLES]
    return by_category
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
def _build_digest_embeds(category: str, articles: list[Article]) -> list[discord.Embed]:
    now_paris = datetime.now(PARIS_TZ)
    date_fr = _format_date_fr(now_paris)
    base_title = f"{CATEGORY_TITLES[category]} : {date_fr}"
    color = CATEGORY_COLORS[category]
    timestamp = datetime.now(timezone.utc)
    if not articles:
        embed = discord.Embed(
            title=base_title,
            description="_Aucun article dans la fenêtre de fraîcheur._",
            color=color,
            timestamp=timestamp,
        )
        embed.set_footer(text="0 article · 0 source")
        embed.set_image(url=EMBED_SPACER_URL)
        return [embed]
    fr_count = sum(1 for a in articles if _detect_lang_for_article(a) == "fr")
    en_count = len(articles) - fr_count
    sources_count = len({a.source_id for a in articles})
    chunks: list[list[Article]] = []
    for i in range(0, len(articles), ARTICLES_PER_EMBED):
        chunks.append(articles[i : i + ARTICLES_PER_EMBED])
    if len(chunks) > DIGEST_MAX_EMBEDS_PER_CATEGORY:
        chunks = chunks[:DIGEST_MAX_EMBEDS_PER_CATEGORY]
    embeds: list[discord.Embed] = []
    n_chunks = len(chunks)
    total_displayed = sum(len(c) for c in chunks)
    for idx, chunk_articles in enumerate(chunks, start=1):
        is_first = (idx == 1)
        is_last = (idx == n_chunks)
        embed_kwargs = {"color": color}
        if is_first:
            embed_kwargs["title"] = base_title
        if is_last:
            embed_kwargs["timestamp"] = timestamp
        embed = discord.Embed(**embed_kwargs)
        for art in chunk_articles:
            field_name, field_value = _format_article_field(art)
            if len(field_name) > EMBED_FIELD_NAME_MAX:
                field_name = field_name[: EMBED_FIELD_NAME_MAX - 1] + "…"
            if len(field_value) > EMBED_FIELD_VALUE_MAX:
                field_value = field_value[: EMBED_FIELD_VALUE_MAX - 1] + "…"
            embed.add_field(name=field_name, value=field_value, inline=False)
        if is_last:
            footer_parts = [
                f"{total_displayed} article{'s' if total_displayed > 1 else ''}",
                f"{sources_count} source{'s' if sources_count > 1 else ''}",
            ]
            if fr_count and en_count:
                footer_parts.append(f"🌐 {fr_count} FR · {en_count} EN")
            elif fr_count:
                footer_parts.append(f"🌐 {fr_count} FR")
            elif en_count:
                footer_parts.append(f"🌐 {en_count} EN")
            embed.set_footer(text=" · ".join(footer_parts))
        embed.set_image(url=EMBED_SPACER_URL)
        embeds.append(embed)
    return embeds
def _detect_lang_for_article(article: Article) -> str:
    try:
        sources = _load_sources()
        for s in sources:
            if s.id == article.source_id:
                return s.language
    except Exception:
        pass
    return "en"
def _format_article_field(article: Article) -> tuple[str, str]:
    prio_emoji = PRIORITY_EMOJI.get(article.priority, "⚪")
    title = article.title.strip()
    if len(title) > 200:
        title = title[:197] + "…"
    lang = _detect_lang_for_article(article)
    flag = "🇫🇷" if lang == "fr" else "🇬🇧"
    age = _format_age(article.published_at)
    source_emoji = CATEGORY_SOURCE_EMOJI.get(article.category, "📰")
    value = (
        f"{prio_emoji} [**{title}**]({article.url})\n"
        f"{source_emoji} `{article.source_id}` · {flag} · _{age}_"
    )
    name = "​"
    return name, value
class VeilleRSS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._sources: list[Source] = []
        self._reload_sources()
    def cog_unload(self):
        if self._daily_digest_loop.is_running():
            self._daily_digest_loop.cancel()
            logger.info("Loop digest quotidien arrêté (cog_unload)")
    def _reload_sources(self) -> None:
        self._sources = _load_sources()
        logger.info(
            "VeilleRSS : %d sources chargées (%d actives)",
            len(self._sources),
            sum(1 for s in self._sources if s.active),
        )
    async def cog_check(self, ctx: commands.Context) -> bool:
        if not ctx.guild or ctx.guild.id != ISTIC_GUILD_ID:
            return False
        if not isinstance(ctx.author, discord.Member):
            return False
        return any(role.id == ADMIN_ROLE_ID for role in ctx.author.roles)
    async def _log_to_channel(
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
            title=title or "📡 Veille RSS",
            description=message[:4096] if message else None,
            color=color if color is not None else 0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        if fields:
            for name, value, inline in fields:
                embed.add_field(
                    name=name[:256],
                    value=value[:1024],
                    inline=inline,
                )
        embed.set_footer(text="veille_rss")
        await webhook_log.send_log(LOG_WEBHOOK, channel, embed=embed, username="Veille tech")
    async def _run_fetch_cycle(self) -> dict[str, list[Article]]:
        state = _load_state()
        _prune_published(state)
        articles = await _fetch_all(self._sources, state)
        by_category = _filter_and_select(articles, state)
        _save_state(state)
        await self._auto_disable_failing_sources(state)
        return by_category
    async def _auto_disable_failing_sources(self, state: dict[str, Any]) -> None:
        fetch_state = state.get("fetch_state", {})
        for sid, fs in fetch_state.items():
            if fs.get("consecutive_errors", 0) >= SOURCE_ERROR_THRESHOLD:
                source = next((s for s in self._sources if s.id == sid), None)
                if source and source.active:
                    source.active = False
                    await self._log_to_channel(
                        f"La source **`{sid}`** a été désactivée automatiquement après "
                        f"**{SOURCE_ERROR_THRESHOLD}** erreurs consécutives.",
                        title="⚠️ VeilleRSS : Source désactivée",
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
            last_dt_utc = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            return False
        last_paris = last_dt_utc.astimezone(PARIS_TZ).date()
        today_paris = datetime.now(PARIS_TZ).date()
        return last_paris == today_paris
    async def _post_morning_summary(
        self,
        state: dict[str, Any],
        posted_counts: dict[str, int],
    ) -> None:
        fetch_state = state.get("fetch_state", {})
        failing_sources = [
            (sid, fs.get("consecutive_errors", 0), fs.get("last_error", "?"))
            for sid, fs in fetch_state.items()
            if fs.get("consecutive_errors", 0) > 0
        ]
        cat_emojis = {"cyber": "📰", "ia": "🤖", "dev": "💻", "tech": "📱"}
        counts_lines = []
        total_posted = 0
        for cat in ("cyber", "ia", "dev", "tech"):
            n = posted_counts.get(cat, 0)
            emoji = cat_emojis[cat]
            skipped = " _(vide)_" if n == 0 else ""
            counts_lines.append(f"{emoji} **{cat}** : `{n}`{skipped}")
            total_posted += n
        counts_field = "\n".join(counts_lines)
        if failing_sources:
            sources_lines = ["⚠️ Sources en erreur :"]
            for sid, errors, last_err in failing_sources:
                short_err = (last_err or "?")[:80]
                sources_lines.append(f"• `{sid}` : {errors} err., `{short_err}`")
            sources_field = "\n".join(sources_lines)
            color = 0xF39C12
        else:
            sources_field = "✅ Toutes les sources fonctionnent."
            color = 0x2ECC71
        total_published = len(state.get("published", {}))
        active_sources = sum(1 for s in self._sources if s.active)
        stats_field = (
            f"Articles trackés (30j) : `{total_published}`\n"
            f"Sources actives : `{active_sources}` / `{len(self._sources)}`"
        )
        await self._log_to_channel(
            f"Récapitulatif du cycle automatique. "
            f"**{total_posted}** article{'s' if total_posted > 1 else ''} "
            f"posté{'s' if total_posted > 1 else ''} au total.",
            title="📡 VeilleRSS : Digest matinal",
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
            logger.info("Digest auto skip : déjà posté aujourd'hui (Paris)")
            await self._log_to_channel(
                "Le digest a déjà été posté aujourd'hui. Aucune action.",
                title="ℹ️ VeilleRSS : Digest skip",
                color=0x95A5A6,
            )
            return {}
        by_category = await self._run_fetch_cycle()
        state = _load_state()
        posted_counts = await self._post_digests(by_category, state)
        if source == "auto":
            await self._post_morning_summary(state, posted_counts)
        return posted_counts
    async def _test_source_url(
        self, url: str, timeout_sec: int = HTTP_TIMEOUT_SECONDS,
    ) -> tuple[bool, str, int]:
        if not _is_valid_url(url):
            return False, "URL invalide (http/https requis)", 0
        fake_source = Source(
            id="__test__", url=url, category="cyber",
            language="fr", priority=1, active=True,
        )
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_sec)
            connector = aiohttp.TCPConnector(limit=1)
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector,
            ) as session:
                articles, err = await _fetch_one_source(session, fake_source, {})
        except Exception as e:
            return False, f"Erreur réseau : {type(e).__name__}: {e}", 0
        if err:
            return False, f"Erreur fetch : {err}", 0
        if not articles:
            return False, "Flux récupéré mais 0 article (flux vide ?)", 0
        return True, f"OK : {len(articles)} articles récupérés", len(articles)
    async def _post_digests(
        self,
        by_category: dict[str, list[Article]],
        state: dict[str, Any],
    ) -> dict[str, int]:
        guild = self.bot.get_guild(ISTIC_GUILD_ID)
        if not guild:
            await self._log_to_channel("❌ Guild ISTIC introuvable, abandon digest")
            return {}
        posted_counts: dict[str, int] = {}
        published = state.setdefault("published", {})
        now_iso = datetime.now(timezone.utc).isoformat()
        for category, articles in by_category.items():
            channel_id = VEILLE_CHANNELS.get(category, 0)
            if channel_id == 0:
                logger.warning("Salon %s non configuré (ID=0), skip", category)
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                logger.warning("Salon ID %s introuvable pour %s", channel_id, category)
                continue
            if SKIP_EMPTY_CATEGORIES and not articles:
                logger.info("Catégorie %s vide, skip post", category)
                posted_counts[category] = 0
                continue
            embeds = _build_digest_embeds(category, articles)
            first_msg_id: str | None = None
            send_failed = False
            for batch_start in range(0, len(embeds), MESSAGE_MAX_EMBEDS):
                batch = embeds[batch_start : batch_start + MESSAGE_MAX_EMBEDS]
                try:
                    msg = await channel.send(embeds=batch)
                    if first_msg_id is None:
                        first_msg_id = str(msg.id)
                except discord.HTTPException:
                    logger.exception("Échec envoi digest %s", category)
                    send_failed = True
                    break
            if send_failed:
                continue
            for art in articles:
                published[art.guid_hash] = {
                    "source_id": art.source_id,
                    "title": art.title,
                    "url": art.url,
                    "category": art.category,
                    "published_at": art.published_at.isoformat(),
                    "posted_at": now_iso,
                    "message_id": first_msg_id or "?",
                }
            posted_counts[category] = len(articles)
        state["last_digest_at"] = now_iso
        _save_state(state)
        return posted_counts
    @tasks.loop(time=time(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, tzinfo=PARIS_TZ))
    async def _daily_digest_loop(self):
        logger.info("Loop digest quotidien déclenché à %s", datetime.now(PARIS_TZ))
        if self._lock.locked():
            logger.warning("Digest auto skip : un cycle manuel est en cours")
            await self._log_to_channel(
                "Le digest auto a été skippé car un cycle manuel était en cours "
                "au moment du déclenchement.",
                title="⚠️ VeilleRSS : Conflit cycle",
                color=0xE67E22,
            )
            return
        async with self._lock:
            try:
                await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("Erreur cycle digest auto")
                await self._log_to_channel(
                    f"Une erreur est survenue lors du cycle automatique :\n"
                    f"```\n{type(e).__name__}: {e}\n```",
                    title="❌ VeilleRSS : Erreur digest auto",
                    color=0xE74C3C,
                )
    @_daily_digest_loop.before_loop
    async def _before_daily_loop(self):
        await self.bot.wait_until_ready()
        logger.info(
            "Loop digest quotidien prêt à tourner (heure cible : %02d:%02d Paris)",
            DIGEST_HOUR, DIGEST_MINUTE,
        )
    @commands.Cog.listener()
    async def on_ready(self):
        if not self._daily_digest_loop.is_running():
            self._daily_digest_loop.start()
            logger.info("Loop digest quotidien démarré")
            await self._log_to_channel(
                f"Loops quotidiens (tech + politique) armés. Prochain déclenchement "
                f"automatique prévu à **{DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d}** (Paris).",
                title="🟢 Veille : Démarrage",
                color=0x2ECC71,
            )
        else:
            logger.debug("Loop digest déjà en cours, on_ready idempotent")
            return
        now_paris = datetime.now(PARIS_TZ)
        digest_time_today = now_paris.replace(
            hour=DIGEST_HOUR, minute=DIGEST_MINUTE,
            second=0, microsecond=0,
        )
        if now_paris <= digest_time_today:
            logger.info(
                "Catch-up skip : on est à ou avant %02d:%02d Paris "
                "(heure actuelle %s, le loop déclenchera)",
                DIGEST_HOUR, DIGEST_MINUTE, now_paris.strftime("%H:%M:%S"),
            )
            return
        state = _load_state()
        if self._digest_already_today(state):
            logger.info("Catch-up skip : digest déjà posté aujourd'hui")
            return
        logger.info(
            "Catch-up déclenché : démarrage à %s, digest 8h00 raté",
            now_paris.strftime("%H:%M"),
        )
        await self._log_to_channel(
            f"Le bot a démarré à **{now_paris.strftime('%H:%M')}** (Paris) après "
            f"l'heure du digest ({DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d}). "
            f"Exécution immédiate du cycle pour ne pas rater la journée.",
            title="🔁 VeilleRSS : Catch-up digest",
            color=0xF39C12,
        )
        if self._lock.locked():
            logger.warning("Catch-up skip : un cycle est en cours")
            return
        async with self._lock:
            try:
                await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("Erreur catch-up digest")
                await self._log_to_channel(
                    f"Une erreur est survenue lors du catch-up :\n"
                    f"```\n{type(e).__name__}: {e}\n```",
                    title="❌ VeilleRSS : Erreur catch-up",
                    color=0xE74C3C,
                )
    @commands.group(name="veille", invoke_without_command=True)
    async def veille_group(self, ctx: commands.Context):
        await ctx.send(
            "**Commandes `!veille` disponibles :**\n"
            "`!veille fetch-now` : cycle manuel (test rapide, pas de récap logs)\n"
            "`!veille trigger-now` : déclenche le cycle 'auto' (avec récap dans #logs)\n"
            "`!veille status` : état des sources et compteurs\n"
            "`!veille reload` : recharge `rss_sources.yaml` et `rss_keywords.yaml`\n"
            "`!veille sources …` : gestion des sources (list/add/remove/toggle/test)\n"
            "`!veille keywords` : affiche les mots-clés de scoring (boost + blacklist)\n"
            "\n"
            f"_Digest auto programmé à {DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d} (Paris) chaque jour._"
        )
    @veille_group.command(name="fetch-now")
    async def fetch_now(self, ctx: commands.Context):
        if self._lock.locked():
            await ctx.send("⏳ Un cycle est déjà en cours, patiente.")
            return
        async with self._lock:
            await ctx.send("🔄 Cycle de fetch en cours…")
            try:
                posted = await self._run_daily_cycle(source="manual")
            except Exception as e:
                logger.exception("Erreur cycle fetch")
                await ctx.send(f"❌ Erreur : `{type(e).__name__}: {e}`")
                return
            summary = " · ".join(
                f"{cat}={n}" for cat, n in posted.items()
            ) or "rien posté"
            await ctx.send(f"✅ Cycle terminé. Posté : {summary}")
    @veille_group.command(name="status")
    async def status(self, ctx: commands.Context):
        state = _load_state()
        fetch_state = state.get("fetch_state", {})
        last_digest = state.get("last_digest_at") or "jamais"
        published_count = len(state.get("published", {}))
        lines = [
            f"**Veille RSS : État**",
            f"Dernier digest : `{last_digest}`",
            f"Articles trackés (30j) : `{published_count}`",
            f"Sources : `{sum(1 for s in self._sources if s.active)}` actives "
            f"/ `{len(self._sources)}` total",
            "",
            "**Détail par source :**",
        ]
        for s in self._sources:
            fs = fetch_state.get(s.id, {})
            errors = fs.get("consecutive_errors", 0)
            last_err = fs.get("last_error") or "OK"
            status_emoji = "✅" if s.active and errors == 0 else (
                "❌" if not s.active else "⚠️"
            )
            lines.append(
                f"{status_emoji} `{s.id}` ({s.category}, prio {s.priority}) : "
                f"erreurs : {errors}, {last_err}"
            )
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n…(tronqué)"
        await ctx.send(text)
    @veille_group.command(name="reload")
    async def reload(self, ctx: commands.Context):
        try:
            self._reload_sources()
            await ctx.send(
                f"✅ Rechargé : {len(self._sources)} sources "
                f"({sum(1 for s in self._sources if s.active)} actives)"
            )
        except Exception as e:
            await ctx.send(f"❌ Erreur de rechargement : `{type(e).__name__}: {e}`")
    @veille_group.command(name="trigger-now")
    async def trigger_now(self, ctx: commands.Context):
        if self._lock.locked():
            await ctx.send("⏳ Un cycle est déjà en cours, patiente.")
            return
        async with self._lock:
            await ctx.send("🔄 Déclenchement manuel du cycle auto (mode 'auto')…")
            try:
                posted = await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("Erreur cycle auto manuel")
                await ctx.send(f"❌ Erreur : `{type(e).__name__}: {e}`")
                return
            summary = " · ".join(
                f"{cat}={n}" for cat, n in posted.items()
            ) or "rien posté (déjà fait aujourd'hui ou aucun nouveau)"
            await ctx.send(
                f"✅ Cycle auto terminé. Posté : {summary}\n"
                f"_(Vérifie #logs pour le récap matinal.)_"
            )
    @veille_group.group(name="sources", invoke_without_command=True)
    async def sources_group(self, ctx: commands.Context):
        await ctx.send(
            "**Commandes `!veille sources` :**\n"
            "`!veille sources list` : liste toutes les sources\n"
            "`!veille sources add <id> <url> <cat> [prio]` : ajoute une source\n"
            "`!veille sources remove <id>` : retire une source\n"
            "`!veille sources toggle <id>` : active/désactive\n"
            "`!veille sources test <url>` : teste une URL sans l'ajouter\n"
            "\n"
            "_Catégories valides : cyber, ia, dev, tech_\n"
            "_Priorités valides : 1 (top), 2 (medium), 3 (low) ; défaut 2_"
        )
    @sources_group.command(name="list")
    async def sources_list(self, ctx: commands.Context):
        embeds: list[discord.Embed] = []
        now = datetime.now(timezone.utc)
        by_cat: dict[str, list[Source]] = {c: [] for c in VALID_CATEGORIES}
        for s in self._sources:
            by_cat[s.category].append(s)
        cat_emojis = {"cyber": "📰", "ia": "🤖", "dev": "💻", "tech": "📱"}
        embed_tech = discord.Embed(
            title="📡 Sources veille tech",
            color=0x5865F2,
            timestamp=now,
        )
        for cat in ("cyber", "ia", "dev", "tech"):
            sources = sorted(by_cat[cat], key=lambda x: (x.priority, x.id))
            if not sources:
                continue
            lines = []
            for s in sources:
                check = "✅" if s.active else "⛔"
                prio = PRIORITY_EMOJI.get(s.priority, "⚪")
                lang_flag = "🇫🇷" if s.language == "fr" else "🇬🇧"
                lines.append(f"{check} {prio} {lang_flag} `{s.id}`")
            embed_tech.add_field(
                name=f"{cat_emojis[cat]} {cat} ({len(sources)})",
                value="\n".join(lines)[:1024],
                inline=True,
            )
        total_t = len(self._sources)
        actives_t = sum(1 for s in self._sources if s.active)
        embed_tech.set_footer(text=f"{total_t} sources · {actives_t} actives")
        embeds.append(embed_tech)
        politique_cog = self.bot.get_cog("VeilleRSSPolitique")
        if politique_cog is not None and getattr(politique_cog, "_sources", None):
            from extensions.veille_rss_politique import (
                VEILLE_POL_CHANNEL_NAMES as POL_CHANS,
                CATEGORY_SOURCE_EMOJI as POL_EMOJIS,
            )
            pol_sources = politique_cog._sources
            embed_pol = discord.Embed(
                title="🗳️ Sources veille politique",
                color=0xCC2229,
                timestamp=now,
            )
            by_cat_pol: dict[str, list] = {}
            for s in pol_sources:
                by_cat_pol.setdefault(s.category, []).append(s)
            for cat in POL_CHANS:
                sources = sorted(by_cat_pol.get(cat, []), key=lambda x: (x.priority, x.id))
                if not sources:
                    continue
                lines = []
                for s in sources:
                    check = "✅" if s.active else "⛔"
                    prio = PRIORITY_EMOJI.get(s.priority, "⚪")
                    lines.append(f"{check} {prio} `{s.id}`")
                embed_pol.add_field(
                    name=f"{POL_EMOJIS.get(cat, '📡')} {cat} ({len(sources)})",
                    value="\n".join(lines)[:1024],
                    inline=True,
                )
            total_p = len(pol_sources)
            actives_p = sum(1 for s in pol_sources if s.active)
            embed_pol.set_footer(text=f"{total_p} sources · {actives_p} actives")
            embeds.append(embed_pol)
        await ctx.send(embeds=embeds)
    @sources_group.command(name="add")
    async def sources_add(
        self,
        ctx: commands.Context,
        source_id: str,
        url: str,
        category: str,
        priority: int = 2,
    ):
        err = _validate_source_id(source_id)
        if err:
            await ctx.send(f"❌ id invalide : {err}")
            return
        if category not in VALID_CATEGORIES:
            await ctx.send(
                f"❌ category invalide. Valides : {', '.join(VALID_CATEGORIES)}"
            )
            return
        if priority not in VALID_PRIORITIES:
            await ctx.send(f"❌ priority invalide (1, 2, ou 3)")
            return
        if not _is_valid_url(url):
            await ctx.send(f"❌ URL invalide (http/https + domaine requis)")
            return
        raw = _load_sources_raw()
        if _find_source_index(raw, source_id) >= 0:
            await ctx.send(f"❌ Une source avec l'id `{source_id}` existe déjà.")
            return
        test_msg = await ctx.send(f"🔄 Test de l'URL `{url}` en cours…")
        ok, msg, count = await self._test_source_url(url)
        if not ok:
            await test_msg.edit(content=f"❌ Test échoué : {msg}")
            return
        await test_msg.edit(content=f"✅ Test OK : {msg}")
        lang = "fr" if any(d in url for d in (".fr/", ".fr?", "://fr.")) else "en"
        from ruamel.yaml.comments import CommentedMap
        new_entry = CommentedMap()
        new_entry["id"] = source_id
        new_entry["url"] = url
        new_entry["category"] = category
        new_entry["language"] = lang
        new_entry["priority"] = priority
        new_entry["active"] = True
        new_entry["notes"] = f"Ajouté via !veille sources add le {datetime.now():%Y-%m-%d}"
        insert_idx = len(raw)
        cat_order = ["cyber", "ia", "dev", "tech"]
        target_cat_pos = cat_order.index(category)
        for i, item in enumerate(raw):
            item_cat = item.get("category") if hasattr(item, "get") else None
            if item_cat is None:
                continue
            try:
                item_pos = cat_order.index(item_cat)
            except ValueError:
                continue
            if item_pos > target_cat_pos:
                insert_idx = i
                break
        raw.insert(insert_idx, new_entry)
        _save_sources_raw(raw)
        self._reload_sources()
        await ctx.send(
            f"✅ Source `{source_id}` ajoutée à la catégorie **{category}** "
            f"(priorité {priority}, langue {lang}).\n"
            f"_{count} articles détectés au test._"
        )
        await self._log_to_channel(
            f"Source **`{source_id}`** ajoutée par {ctx.author.mention}.",
            title="➕ VeilleRSS : Source ajoutée",
            color=0x2ECC71,
            fields=[
                ("URL", url, False),
                ("Catégorie", category, True),
                ("Priorité", str(priority), True),
                ("Langue", lang, True),
            ],
        )
    @sources_group.command(name="remove")
    async def sources_remove(
        self,
        ctx: commands.Context,
        source_id: str,
    ):
        raw = _load_sources_raw()
        idx = _find_source_index(raw, source_id)
        if idx < 0:
            await ctx.send(f"❌ Aucune source avec l'id `{source_id}`.")
            return
        confirm_msg = await ctx.send(
            f"⚠️ Confirmer la suppression de la source `{source_id}` ?\n"
            f"Réponds **oui** dans les 30 secondes."
        )
        def check(m: discord.Message) -> bool:
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.strip().lower() in ("oui", "yes", "y", "o")
            )
        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await confirm_msg.edit(content="⏱️ Délai dépassé, suppression annulée.")
            return
        del raw[idx]
        _save_sources_raw(raw)
        self._reload_sources()
        await ctx.send(f"✅ Source `{source_id}` supprimée.")
        await self._log_to_channel(
            f"Source **`{source_id}`** supprimée par {ctx.author.mention}.",
            title="🗑️ VeilleRSS : Source supprimée",
            color=0xE67E22,
        )
    @sources_group.command(name="toggle")
    async def sources_toggle(
        self,
        ctx: commands.Context,
        source_id: str,
    ):
        raw = _load_sources_raw()
        idx = _find_source_index(raw, source_id)
        if idx < 0:
            await ctx.send(f"❌ Aucune source avec l'id `{source_id}`.")
            return
        current_active = bool(raw[idx].get("active", True))
        new_active = not current_active
        raw[idx]["active"] = new_active
        _save_sources_raw(raw)
        self._reload_sources()
        state_word = "activée" if new_active else "désactivée"
        emoji = "✅" if new_active else "⛔"
        await ctx.send(f"{emoji} Source `{source_id}` **{state_word}**.")
        await self._log_to_channel(
            f"Source **`{source_id}`** {state_word} par {ctx.author.mention}.",
            title=f"{emoji} VeilleRSS : Source toggle",
            color=0x2ECC71 if new_active else 0x95A5A6,
        )
    @sources_group.command(name="test")
    async def sources_test(
        self,
        ctx: commands.Context,
        url: str,
    ):
        if not _is_valid_url(url):
            await ctx.send(f"❌ URL invalide (http/https + domaine requis).")
            return
        msg = await ctx.send(f"🔄 Test de l'URL `{url}` en cours…")
        ok, result, count = await self._test_source_url(url)
        if ok:
            await msg.edit(
                content=f"✅ {result}\n_Tu peux maintenant l'ajouter via `!veille sources add`._"
            )
        else:
            await msg.edit(content=f"❌ {result}")
    @veille_group.command(name="keywords")
    async def keywords_show(self, ctx: commands.Context):
        try:
            keywords = _load_keywords()
        except Exception as e:
            await ctx.send(f"❌ Erreur lecture rss_keywords.yaml : `{type(e).__name__}: {e}`")
            return
        cat_emojis = {"all": "🌐", "cyber": "📰", "ia": "🤖", "dev": "💻", "tech": "📱"}
        fields = []
        for cat in ("all", "cyber", "ia", "dev", "tech"):
            cat_kw = keywords.get(cat, {"boost": [], "blacklist": []})
            boost = cat_kw.get("boost", [])
            blacklist = cat_kw.get("blacklist", [])
            if not boost and not blacklist:
                continue
            lines = []
            if boost:
                lines.append(f"**Boost ({len(boost)})** : " + ", ".join(f"`{w}`" for w in boost[:15]))
                if len(boost) > 15:
                    lines.append(f"  _(+ {len(boost) - 15} autres)_")
            if blacklist:
                lines.append(f"**Blacklist ({len(blacklist)})** : " + ", ".join(f"`{w}`" for w in blacklist[:10]))
                if len(blacklist) > 10:
                    lines.append(f"  _(+ {len(blacklist) - 10} autres)_")
            fields.append((
                f"{cat_emojis.get(cat, '❓')} {cat}",
                "\n".join(lines)[:1024],
                False,
            ))
        if not fields:
            await ctx.send("ℹ️ Aucun mot-clé configuré. Édite `datas/rss_keywords.yaml`.")
            return
        embed = discord.Embed(
            title="🔑 Mots-clés de scoring",
            description=(
                f"Boost = +{KEYWORD_BOOST_POINTS} points par match (article remonte).\n"
                f"Blacklist = article rejeté du digest.\n"
                f"_Édite `datas/rss_keywords.yaml` puis `!veille reload` pour mettre à jour._"
            ),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        await ctx.send(embed=embed)
async def setup(bot: commands.Bot):
    await bot.add_cog(VeilleRSS(bot))