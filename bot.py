from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from pathlib import Path
import discord
import httpx
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")
import publisher
import reprocess
import webhook_log
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gaylord")
TOKEN = os.environ["DISCORD_BOT_TOKEN"]
LISTEN_CHANNEL_ID = int(os.environ["LISTEN_CHANNEL_ID"])
RETIRED_LOG_CHANNEL_IDS = {1493760267300110466}
GENERAL_LOG_CHANNEL_ID = 1518182717139976344
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "0") or 0)
if LOG_CHANNEL_ID in RETIRED_LOG_CHANNEL_IDS:
    LOG_CHANNEL_ID = GENERAL_LOG_CHANNEL_ID
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0") or 0)
LOG_WEBHOOK_GENERAL = os.environ.get("LOG_WEBHOOK_GENERAL")
SERVICE_PY_URL = os.environ.get("SERVICE_PY_URL", "http://127.0.0.1:8000").rstrip("/")
WHISPER_MODELS = ("tiny", "base", "small", "medium")
API_BASE = os.environ.get("ABOEKA_API_BASE", "http://127.0.0.1:3000").rstrip("/")
PUBLIC_BASE = os.environ.get("ABOEKA_PUBLIC_BASE", "https://aboeka.fr").rstrip("/")
BOT_SECRET = os.environ.get("ABOEKA_BOT_SECRET", "")
BOT_INSTANCE = os.environ.get("BOT_INSTANCE", "server")
BOT_CLAIM_DELAY_S = float(os.environ.get("BOT_CLAIM_DELAY_S", "0") or 0)
PROCESS_LINKS = os.environ.get("PROCESS_LINKS", "true").lower() != "false"
VISITOR_ROLE_NAME = os.environ.get("VISITOR_ROLE_NAME", "Visiteur")
GENERATION_TIMEOUT_S = 3 * 60 * 60
POLL_INTERVAL_S = 6
_PLATFORM_PATTERNS = [
    ("TikTok", re.compile(r"https?://(?:[\w-]+\.)?tiktok\.com/\S+", re.I)),
    ("Instagram", re.compile(r"https?://(?:[\w-]+\.)?instagram\.com/\S+", re.I)),
    ("YouTube", re.compile(r"https?://(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/\S+", re.I)),
    ("X", re.compile(r"https?://(?:[\w-]+\.)?(?:x\.com|twitter\.com)/\S+", re.I)),
    ("Reddit", re.compile(r"https?://(?:[\w-]+\.)?reddit\.com/\S+", re.I)),
    ("GitHub", re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+", re.I)),
]
def detect_links(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for platform, pat in _PLATFORM_PATTERNS:
        for m in pat.finditer(text or ""):
            url = m.group(0).rstrip(").,>")
            if url not in seen:
                seen.add(url)
                found.append((platform, url))
    return found
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.members = True
intents.guilds = True
class AboekaBot(commands.Bot):
    async def setup_hook(self) -> None:
        for ext in ("extensions.cours_pipeline", "extensions.veille_rss", "extensions.veille_rss_politique"):
            try:
                await self.load_extension(ext)
                log.info("Extension chargee → %s", ext)
            except Exception as e:
                log.error("Impossible de charger %s → %s", ext, e)
client = AboekaBot(command_prefix="!", intents=intents)
@client.tree.command(
    name="whisper",
    description="Choisir le modele Whisper (transcription) pour aujourd'hui",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None,
)
@discord.app_commands.describe(modele="Du plus rapide (tiny) au plus precis mais lent (medium)")
@discord.app_commands.choices(modele=[
    discord.app_commands.Choice(name="tiny (le + rapide)", value="tiny"),
    discord.app_commands.Choice(name="base (rapide, defaut)", value="base"),
    discord.app_commands.Choice(name="small (precis, lent)", value="small"),
    discord.app_commands.Choice(name="medium (tres precis, tres lent)", value="medium"),
])
async def whisper_cmd(
    interaction: discord.Interaction, modele: discord.app_commands.Choice[str]
) -> None:
    perms = getattr(interaction.user, "guild_permissions", None)
    if not (perms and perms.administrator):
        await interaction.response.send_message("Reserve aux admins.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    assert _http is not None
    try:
        r = await _http.post(f"{SERVICE_PY_URL}/config/whisper", json={"model": modele.value})
        r.raise_for_status()
        active = r.json().get("model", modele.value)
    except Exception as exc:
        await interaction.followup.send(f"Echec : {exc}", ephemeral=True)
        return
    await interaction.followup.send(
        f"Modele Whisper regle sur **{active}** pour aujourd'hui "
        f"(repassera automatiquement en `base` demain).",
        ephemeral=True,
    )
    await _log_channel(
        f"Modele Whisper change en **{active}** par {interaction.user} "
        f"(valable aujourd'hui, retour auto en base demain)."
    )
_queue: "asyncio.Queue[tuple[discord.Message, str, str]]" = asyncio.Queue()
_http: httpx.AsyncClient | None = None
_reprocess_started = False
async def _log_channel(text: str) -> None:
    if not LOG_CHANNEL_ID and not LOG_WEBHOOK_GENERAL:
        return
    ch = client.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    await webhook_log.send_log(LOG_WEBHOOK_GENERAL, ch, content=text)
async def _react(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except discord.DiscordException:
        pass
async def _post_link_thread(
    message: discord.Message, fiche: dict, page: str, forum_url: str | None = None
):
    title = (fiche.get("title") or "Fiche aboeka").strip()
    try:
        thread = message.thread or await message.create_thread(
            name=title[:100], auto_archive_duration=1440
        )
    except discord.DiscordException as exc:
        log.warning("Creation du fil echouee : %s", exc)
        return None
    try:
        await thread.remove_user(message.author)
    except discord.DiscordException as exc:
        log.warning("Retrait de l'auteur du fil echoue : %s", exc)
    note = fiche.get("note")
    note_txt = f" · {note}/20" if note is not None else ""
    body = f"\N{PAGE FACING UP} **{title}**{note_txt}\nFiche : {page}"
    if forum_url:
        body += f"\nDiscussion : {forum_url}"
    try:
        await thread.send(body, allowed_mentions=discord.AllowedMentions.none())
    except discord.DiscordException as exc:
        log.warning("Envoi dans le fil echoue : %s", exc)
    return thread
def _chunk_messages(blocks: list[str], limit: int = 1900) -> list[str]:
    out: list[str] = []
    cur = ""
    for b in blocks:
        b = b[:limit]
        if cur and len(cur) + len(b) + 2 > limit:
            out.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        out.append(cur)
    return out
def _render_reception(reception: dict) -> list[str]:
    messages: list[str] = []
    analysis = (reception.get("analysis") or "").strip()
    if analysis:
        header = "\N{SPEECH BALLOON} **Reception** : analyse des commentaires\n\n"
        paras = analysis.split("\n\n")
        messages += _chunk_messages([header + paras[0]] + paras[1:])
    replies = [r for r in (reception.get("replies") or []) if r.get("response")]
    if replies:
        blocks = ["\N{CLIPBOARD} **Reponses commentaire par commentaire**"]
        for r in replies:
            likes = r.get("likes") or 0
            nb = r.get("replies") or 0
            head = f"**{r.get('n')}. {r.get('author')}** · ❤️ {likes}"
            if nb:
                head += f" · \N{SPEECH BALLOON} {nb}"
            lines = [head]
            trad = (r.get("translation") or "").strip()
            text = (r.get("text") or "").strip()
            flag = (r.get("lang") or "").strip()
            if trad:
                lines.append(f"> \U0001F1EB\U0001F1F7 {trad}")
                lines.append(f"> {flag} {text}")
            else:
                lines.append(f"> {flag} {text}")
            lines.append(f"↳ {r.get('response')}")
            blocks.append("\n".join(lines))
        messages += _chunk_messages(blocks)
    return messages
async def _fetch_reception(dossier: str) -> dict | None:
    if _http is None:
        return None
    try:
        r = await _http.get(
            f"{API_BASE}/api/bot/reception/{dossier}",
            headers={"X-Bot-Secret": BOT_SECRET},
        )
        if r.status_code == 200:
            return r.json().get("reception") or None
    except Exception:
        pass
    return None
_RECEPTION_POSTED = Path(__file__).resolve().parent / "data" / "reception_posted.json"
def _load_posted() -> set[str]:
    try:
        return set(json.loads(_RECEPTION_POSTED.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()
def _mark_posted(dossier: str) -> None:
    posted = _load_posted()
    posted.add(dossier)
    try:
        _RECEPTION_POSTED.parent.mkdir(parents=True, exist_ok=True)
        _RECEPTION_POSTED.write_text(json.dumps(sorted(posted)), encoding="utf-8")
    except OSError as exc:
        log.warning("ecriture reception_posted echouee : %s", exc)
def _thread_id_from_url(thread_url: str | None) -> int | None:
    if not thread_url:
        return None
    try:
        return int(thread_url.rstrip("/").split("/")[-1])
    except (ValueError, AttributeError):
        return None
async def _post_reception(channel_id: int | None, reception: dict) -> bool:
    if not channel_id:
        return False
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.DiscordException as exc:
            log.warning("Thread forum %s introuvable pour la reception : %s", channel_id, exc)
            return False
    for msg in _render_reception(reception):
        try:
            await channel.send(msg, allowed_mentions=discord.AllowedMentions.none())
        except discord.DiscordException as exc:
            log.warning("Envoi reception dans le thread forum echoue : %s", exc)
            return False
    return True
async def _reception_poller() -> None:
    await client.wait_until_ready()
    while True:
        try:
            published = publisher._load_published()
            posted = _load_posted()
            for dossier, info in published.items():
                if dossier in posted:
                    continue
                reception = await _fetch_reception(dossier)
                if not reception:
                    continue
                tid = info.get("thread_id")
                if tid and await _post_reception(int(tid), reception):
                    _mark_posted(dossier)
                    log.info("Reception postee dans le thread forum (dossier %s)", dossier)
        except Exception as exc:
            log.warning("Cycle du poller de reception en erreur : %s", exc)
        await asyncio.sleep(30)
async def _fetch_publishable() -> list[dict]:
    try:
        r = await _http.get(f"{API_BASE}/api/bot/fiches", headers={"X-Bot-Secret": BOT_SECRET})
        r.raise_for_status()
        return r.json().get("fiches") or []
    except Exception as exc:
        log.warning("Recuperation des fiches a publier echouee : %s", exc)
        return []
async def _publish_poller() -> None:
    await client.wait_until_ready()
    while True:
        try:
            published = publisher._load_published()
            for fiche in await _fetch_publishable():
                dossier = str(fiche.get("dossier") or fiche.get("id"))
                if dossier in published:
                    continue
                url = await publisher.publish_fiche(client, fiche)
                if url:
                    log.info("Thread forum cree pour une fiche du site (dossier %s) : %s", dossier, url)
        except Exception as exc:
            log.warning("Cycle du poller de publication en erreur : %s", exc)
        await asyncio.sleep(60)
async def _claim(message_id: int) -> bool:
    if BOT_CLAIM_DELAY_S > 0:
        await asyncio.sleep(BOT_CLAIM_DELAY_S)
    assert _http is not None
    claim_url = f"{API_BASE}/api/bot/claim"
    try:
        r = await _http.post(
            claim_url,
            json={"key": str(message_id), "instanceId": BOT_INSTANCE},
            headers={"X-Bot-Secret": BOT_SECRET},
        )
        r.raise_for_status()
        granted = bool(r.json().get("granted"))
        log.info("[claim] %s → granted=%s", claim_url, granted)
        return granted
    except Exception as exc:
        log.warning("[claim] API injoignable (%s: %s), on traite quand meme", type(exc).__name__, exc)
        return True
async def _generate(url: str) -> dict:
    assert _http is not None
    headers = {"X-Bot-Secret": BOT_SECRET, "Content-Type": "application/json"}
    endpoint = f"{API_BASE}/api/bot/generate"
    log.info("[generate] POST %s pour url=%s", endpoint, url)
    try:
        r = await _http.post(endpoint, json={"url": url}, headers=headers)
    except Exception as exc:
        log.error("[generate] Connexion echouee vers %s (%s) : %s", endpoint, type(exc).__name__, exc)
        raise
    log.info("[generate] Reponse HTTP %s de %s", r.status_code, endpoint)
    r.raise_for_status()
    job_id = r.json().get("jobId")
    if not job_id:
        raise RuntimeError("pas de jobId renvoye par l'API")
    log.info("[generate] jobId=%s, debut du polling", job_id)
    waited = 0
    while waited < GENERATION_TIMEOUT_S:
        await asyncio.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
        poll_url = f"{API_BASE}/api/bot/generate/{job_id}"
        try:
            s = await _http.get(poll_url, headers={"X-Bot-Secret": BOT_SECRET})
        except Exception as exc:
            log.warning("[generate] Erreur poll %s (%s) : %s", poll_url, type(exc).__name__, exc)
            raise
        s.raise_for_status()
        data = s.json()
        status = data.get("status")
        log.debug("[generate] Poll status=%s (waited=%ds)", status, waited)
        if status == "done":
            log.info("[generate] Fiche prete apres %ds pour %s", waited, url)
            return data["fiche"]
        if status == "error":
            raise RuntimeError(data.get("error") or "echec de generation")
    raise TimeoutError("generation trop longue (timeout)")
async def _worker() -> None:
    while True:
        message, platform, url = await _queue.get()
        try:
            log.info("Generation %s : %s", platform, url)
            await _react(message, "\N{HOURGLASS}")
            fiche = await _generate(url)
            page = f"{PUBLIC_BASE}{fiche.get('pageUrl', '')}"
            await _react(message, "\N{WHITE HEAVY CHECK MARK}")
            thread_url = None
            try:
                thread_url = await publisher.publish_fiche(client, fiche, force_repost=True)
            except Exception as exc:
                log.warning("Publication forum echouee pour %s : %s", url, exc)
            await _post_link_thread(message, fiche, page, thread_url)
            if thread_url:
                await _log_channel(f"Republiee sur le forum : {fiche.get('title')}\n{thread_url}")
        except Exception as exc:
            log.warning("Echec generation %s (%s) : %s", url, type(exc).__name__, exc, exc_info=True)
            await _react(message, "\N{CROSS MARK}")
            await _log_channel(f"Echec sur {url} : {type(exc).__name__}: {exc}")
        finally:
            _queue.task_done()
async def _resume_pending() -> None:
    channel = client.get_channel(LISTEN_CHANNEL_ID)
    if channel is None:
        log.warning("[reprise] Salon %s introuvable", LISTEN_CHANNEL_ID)
        return
    recovered = 0
    try:
        async for message in channel.history(limit=60):
            if message.author.bot:
                continue
            emoji_names = {str(r.emoji) for r in message.reactions}
            if "\N{EYES}" not in emoji_names:
                continue
            if "\N{WHITE HEAVY CHECK MARK}" in emoji_names or "\N{CROSS MARK}" in emoji_names:
                continue
            links = detect_links(message.content)
            if not links:
                continue
            log.info("[reprise] Message %s remis en file (%d lien(s))", message.id, len(links))
            for platform, url in links:
                await _queue.put((message, platform, url))
            recovered += 1
    except discord.DiscordException as exc:
        log.warning("[reprise] Scan canal echoue : %s", exc)
        return
    if recovered:
        log.info("[reprise] %d message(s) recupere(s) apres restart", recovered)
    else:
        log.info("[reprise] Aucun lien en attente")
@client.event
async def on_ready() -> None:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=60)
        client.loop.create_task(_worker())
        client.loop.create_task(_reception_poller())
    log.info("Connecte comme %s (id=%s)", client.user, client.user.id if client.user else "?")
    log.info("Config : API_BASE=%s PUBLIC_BASE=%s BOT_INSTANCE=%s CLAIM_DELAY=%.1fs PROCESS_LINKS=%s",
             API_BASE, PUBLIC_BASE, BOT_INSTANCE, BOT_CLAIM_DELAY_S, PROCESS_LINKS)
    if PROCESS_LINKS:
        log.info("Ecoute le salon %s, file de generation prete", LISTEN_CHANNEL_ID)
        client.loop.create_task(_resume_pending())
        global _reprocess_started
        campaign = reprocess.has_targets()
        if not campaign:
            client.loop.create_task(_publish_poller())
        else:
            log.info("Campagne active : _publish_poller differe (anti-course threads)")
        if not _reprocess_started and campaign:
            _reprocess_started = True
            ctx = {
                "listen_channel_id": LISTEN_CHANNEL_ID,
                "public_base": PUBLIC_BASE,
                "generate": _generate,
                "post_link_thread": _post_link_thread,
                "detect_links": detect_links,
                "react": _react,
                "log_channel": _log_channel,
            }
            client.loop.create_task(reprocess.run(client, ctx))
            log.info("Campagne de relance ciblee demarree")
    else:
        log.info("PROCESS_LINKS=false : liens ignores, seuls !cours/!veille actifs")
    if GUILD_ID:
        try:
            await client.tree.sync(guild=discord.Object(id=GUILD_ID))
            log.info("Slash commands synchronisees sur le serveur %s", GUILD_ID)
        except discord.DiscordException as exc:
            log.warning("Sync slash commands echouee : %s", exc)
@client.event
async def on_member_join(member: discord.Member) -> None:
    if GUILD_ID and member.guild.id != GUILD_ID:
        return
    if member.bot:
        return
    if not await _claim(member.id):
        return
    role = discord.utils.get(member.guild.roles, name=VISITOR_ROLE_NAME)
    if role is None:
        log.warning("on_member_join : role '%s' introuvable, rien a assigner", VISITOR_ROLE_NAME)
        return
    try:
        await member.add_roles(role, reason="Auto-role visiteur a l'arrivee")
        log.info("Role %s assigne a %s (%s)", role.name, member, member.id)
    except discord.Forbidden:
        log.error(
            "Permissions insuffisantes pour assigner %s a %s "
            "(le role 'bot' doit avoir Gerer les roles et etre au-dessus de '%s')",
            role.name, member, role.name,
        )
    except discord.HTTPException as exc:
        log.error("Echec assignation du role a %s : %s", member, exc)
@client.event
async def on_message(message: discord.Message) -> None:
    await client.process_commands(message)
    if not PROCESS_LINKS:
        return
    if message.channel.id != LISTEN_CHANNEL_ID:
        return
    if message.type == discord.MessageType.thread_created:
        try:
            await message.delete()
        except discord.DiscordException:
            pass
        return
    if message.author.bot:
        return
    links = detect_links(message.content)
    if not links:
        return
    if not await _claim(message.id):
        log.info("Lien %s deja pris par une autre instance, on passe", message.id)
        return
    await _react(message, "\N{EYES}")
    for platform, url in links:
        log.info("Lien en file (%s) : %s", platform, url)
        await _queue.put((message, platform, url))
_FATAL_EXIT_CODE = 2
def main() -> None:
    import sys
    try:
        client.run(TOKEN, log_handler=None)
    except discord.errors.LoginFailure as exc:
        log.critical(
            "Token Discord invalide ou révoqué : %s\n"
            "→ Regénère le token sur https://discord.com/developers/applications "
            "et mets à jour .env (DISCORD_BOT_TOKEN). "
            "L'auto-restart est désactivé pour éviter la boucle.",
            exc,
        )
        sys.exit(_FATAL_EXIT_CODE)
if __name__ == "__main__":
    main()