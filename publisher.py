from __future__ import annotations
import io
import json
import logging
import os
import re
from pathlib import Path
import discord
log = logging.getLogger("gaylord.publisher")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1466806132998672466"))
CATEGORY_NAME = os.environ.get("ANALYSES_CATEGORY", "ANALYSES & DOSSIERS")
VIDEO_DIR = Path(os.environ.get("ABOEKA_VIDEO_DIR", "/app/aboeka/web/public/videos"))
PUBLIC_BASE = os.environ.get("ABOEKA_PUBLIC_BASE", "https://aboeka.fr").rstrip("/")
_DATA = Path(__file__).resolve().parent / "data"
_PUBLISHED = _DATA / "published_threads.json"
_MSG_LIMIT = 1900
_MAX_FILES = 10
_MAX_FILE_BYTES = 24 * 1024 * 1024
CANONICAL_FORUMS = {
    "politique-française", "économie-et-social", "société-et-médias", "ia-et-technologie",
    "histoire-et-géopolitique", "international-et-solidarités", "religions-et-philosophie",
    "justice-et-libertés", "féminisme-et-luttes", "social-et-logement", "culture-et-éducation",
    "écologie-et-climat", "catégorie-libre", "élections-et-campagnes",
}
CLASSIFICATION_ALIASES = {
    "éducation": "culture-et-éducation", "société-et-éducation": "culture-et-éducation",
    "culture": "culture-et-éducation", "social-et-médias": "société-et-médias",
    "débats-et-rhétorique": "société-et-médias", "rhétorique": "société-et-médias",
    "médias": "société-et-médias", "médias-et-société": "société-et-médias",
    "économie": "économie-et-social", "social": "économie-et-social",
    "économie-et-finance": "économie-et-social", "histoire": "histoire-et-géopolitique",
    "géopolitique": "histoire-et-géopolitique", "international": "international-et-solidarités",
    "solidarités": "international-et-solidarités", "justice": "justice-et-libertés",
    "libertés": "justice-et-libertés", "droits-humains": "justice-et-libertés",
    "féminisme": "féminisme-et-luttes", "luttes": "féminisme-et-luttes",
    "écologie": "écologie-et-climat", "climat": "écologie-et-climat",
    "ia": "ia-et-technologie", "technologie": "ia-et-technologie", "tech": "ia-et-technologie",
    "religions": "religions-et-philosophie", "religion": "religions-et-philosophie",
    "philosophie": "religions-et-philosophie", "politique": "politique-française",
    "politique-france": "politique-française", "logement": "social-et-logement",
    "précarité": "social-et-logement", "campagne-2027": "élections-et-campagnes",
    "campagne": "élections-et-campagnes", "présidentielle": "élections-et-campagnes",
    "élections": "élections-et-campagnes", "élection": "élections-et-campagnes",
}
FALLBACK_FORUM = "catégorie-libre"
def slugify(text: str | None) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\W+", "-", s, flags=re.UNICODE)
    return s.strip("-")
def normalize_forum_slug(raw: str) -> str:
    if raw in CANONICAL_FORUMS:
        return raw
    if raw in CLASSIFICATION_ALIASES:
        return CLASSIFICATION_ALIASES[raw]
    head = raw.split("-")[0] if raw else ""
    if head in CLASSIFICATION_ALIASES:
        return CLASSIFICATION_ALIASES[head]
    return FALLBACK_FORUM
def _load_published() -> dict:
    try:
        return json.loads(_PUBLISHED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
def _save_published(data: dict) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    _PUBLISHED.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
def _chunks(text: str, size: int = _MSG_LIMIT) -> list[str]:
    out: list[str] = []
    cur = ""
    for line in (text or "").split("\n"):
        if len(cur) + len(line) + 1 > size:
            if cur:
                out.append(cur)
            while len(line) > size:
                out.append(line[:size])
                line = line[size:]
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out
async def _get_or_create_forum(guild: discord.Guild, slug: str) -> discord.ForumChannel | None:
    for ch in guild.channels:
        if isinstance(ch, discord.ForumChannel) and ch.name == slug:
            return ch
    category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    try:
        return await guild.create_forum(name=slug, category=category)
    except discord.DiscordException as exc:
        log.warning("create_forum %s echoue : %s", slug, exc)
        return None
def _header(fiche: dict) -> str:
    note = fiche.get("note")
    eng = fiche.get("engagement") or {}
    parts = [f"**{fiche.get('title', 'Fiche')}**"]
    meta = []
    if note is not None:
        meta.append(f"Note {note}/20")
    if fiche.get("plateforme"):
        meta.append(str(fiche["plateforme"]))
    if fiche.get("username"):
        meta.append(f"@{fiche['username']}")
    if meta:
        parts.append(" · ".join(meta))
    eng_bits = [f"{eng[k]} {lbl}" for k, lbl in
                (("views", "vues"), ("likes", "likes"), ("comments_count", "commentaires"), ("shares", "partages"))
                if eng.get(k) is not None]
    if eng_bits:
        parts.append("Engagement : " + " · ".join(eng_bits))
    if fiche.get("sourceUrl"):
        parts.append(f"Source : {fiche['sourceUrl']}")
    parts.append(f"Fiche complete : {PUBLIC_BASE}{fiche.get('pageUrl', '')}")
    return "\n".join(parts)
def _media_files(dossier: str, frames: list[str]) -> tuple[list, list]:
    base = VIDEO_DIR / dossier
    videos = []
    vp = base / "video.mp4"
    if vp.is_file() and vp.stat().st_size <= _MAX_FILE_BYTES:
        videos.append(discord.File(str(vp), filename="video.mp4"))
    frame_files = []
    fdir = base / "frames"
    if fdir.is_dir():
        for fp in sorted(fdir.glob("*.jpg")):
            if fp.is_file():
                frame_files.append(fp)
    return videos, frame_files
async def publish_fiche(
    client: discord.Client, fiche: dict, *, force_repost: bool = False
) -> str | None:
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        log.warning("guild %s introuvable", GUILD_ID)
        return None
    dossier = str(fiche.get("dossier") or fiche.get("id"))
    published = _load_published()
    if dossier in published:
        tid = published[dossier].get("thread_id")
        if force_repost:
            if tid:
                try:
                    ch = guild.get_thread(int(tid)) or await client.fetch_channel(int(tid))
                    await ch.delete()
                    log.info("Ancien thread forum supprime (dossier %s, re-drop)", dossier)
                except discord.DiscordException as exc:
                    log.info("Ancien thread forum %s deja absent : %s", tid, exc)
            published.pop(dossier, None)
            _save_published(published)
        else:
            ch = guild.get_thread(int(tid)) if tid else None
            if ch is not None:
                return ch.jump_url
            published.pop(dossier, None)
    slug = normalize_forum_slug(slugify(fiche.get("categorie")))
    forum = await _get_or_create_forum(guild, slug)
    if forum is None:
        return None
    name = (fiche.get("title") or f"Fiche {dossier}")[:90]
    created = await forum.create_thread(name=name, content=_header(fiche)[:_MSG_LIMIT])
    thread = created.thread
    for chunk in _chunks(fiche.get("body") or ""):
        await thread.send(chunk)
    transcription = (fiche.get("transcription") or "").strip()
    if transcription:
        bar = "=" * 60
        head = "\n".join([
            bar,
            f"FICHIER SOURCE : {dossier}",
            f"PLATEFORME     : {fiche.get('plateforme') or '?'}",
            bar, "",
        ])
        buf = io.BytesIO((head + transcription).encode("utf-8"))
        try:
            await thread.send(
                "\N{PAGE FACING UP} **Transcription Whisper**",
                file=discord.File(buf, filename=f"{dossier}.txt"),
            )
        except discord.DiscordException as exc:
            log.warning("envoi transcription echoue : %s", exc)
    videos, frame_files = _media_files(dossier, fiche.get("frames") or [])
    if videos:
        try:
            await thread.send(files=videos)
        except discord.DiscordException as exc:
            log.warning("envoi video echoue : %s", exc)
    for i in range(0, len(frame_files), _MAX_FILES):
        batch = [discord.File(str(fp), filename=fp.name) for fp in frame_files[i:i + _MAX_FILES]]
        try:
            await thread.send(files=batch)
        except discord.DiscordException as exc:
            log.warning("envoi frames echoue : %s", exc)
    comments = fiche.get("comments") or []
    if comments:
        lines = ["**Commentaires capturés**"]
        for c in comments[:10]:
            author = c.get("author") or "Anonyme"
            likes = c.get("likes") or 0
            lines.append(f"- {author} ({likes}) : {(c.get('text') or '')[:200]}")
        for chunk in _chunks("\n".join(lines)):
            await thread.send(chunk)
    published[dossier] = {"thread_id": str(thread.id), "forum_id": str(forum.id), "slug": slug}
    _save_published(published)
    log.info("Fiche publiee dans #%s : %s", slug, thread.jump_url)
    return thread.jump_url