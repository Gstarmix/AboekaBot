from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
import discord
import publisher
log = logging.getLogger("gaylord.reprocess")
_DATA = Path(__file__).resolve().parent / "data"
_TARGETS = _DATA / "reprocess_targets.json"
_PROGRESS = _DATA / "reprocess_progress.json"
_RECEPTION_POSTED = _DATA / "reception_posted.json"
_STATUS_EMOJI = ("\N{EYES}", "\N{HOURGLASS}", "\N{WHITE HEAVY CHECK MARK}", "\N{CROSS MARK}")
_TERMINAL = {"ok", "partial", "fail", "skip"}
def has_targets() -> bool:
    return _TARGETS.is_file()
def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
def _save_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("écriture %s échouée : %s", path.name, exc)
def _drop_reception_posted(dossier: str) -> None:
    posted = set(_load_json(_RECEPTION_POSTED, []))
    if dossier in posted:
        posted.discard(dossier)
        _save_json(_RECEPTION_POSTED, sorted(posted))
async def _delete_forum_thread(client: discord.Client, dossier: str) -> None:
    published = publisher._load_published()
    info = published.get(dossier)
    if not info:
        return
    tid = info.get("thread_id")
    if tid:
        try:
            ch = client.get_channel(int(tid)) or await client.fetch_channel(int(tid))
            await ch.delete()
            log.info("[reprocess] ancien thread forum supprimé (dossier %s)", dossier)
        except discord.DiscordException as exc:
            log.info("[reprocess] thread forum %s déjà absent/non supprimé : %s", tid, exc)
    published.pop(dossier, None)
    publisher._save_published(published)
    _drop_reception_posted(dossier)
async def _delete_message_thread(message: discord.Message) -> None:
    th = message.thread
    if th is None:
        return
    try:
        await th.delete()
        log.info("[reprocess] ancien fil salon liens supprimé (msg %s)", message.id)
    except discord.DiscordException as exc:
        log.info("[reprocess] fil salon liens du msg %s non supprimé : %s", message.id, exc)
async def _clear_status_reactions(client: discord.Client, message: discord.Message) -> None:
    me = client.user
    for emoji in _STATUS_EMOJI:
        try:
            await message.remove_reaction(emoji, me)
        except discord.DiscordException:
            pass
_RETRY_BACKOFF_S = (8, 30, 90)
_PER_ITEM_TIMEOUT_S = 25 * 60
async def _generate_with_retry(generate, url: str):
    last = None
    for attempt in range(len(_RETRY_BACKOFF_S) + 1):
        try:
            return await asyncio.wait_for(generate(url), timeout=_PER_ITEM_TIMEOUT_S)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            log.warning("[reprocess] generation %s timeout (%d min) : on abandonne sans retry",
                        url, _PER_ITEM_TIMEOUT_S // 60)
            return None
        except Exception as exc:
            last = exc
            log.warning("[reprocess] generation %s tentative %d KO : %s",
                        url, attempt + 1, exc)
            if attempt < len(_RETRY_BACKOFF_S):
                await asyncio.sleep(_RETRY_BACKOFF_S[attempt])
    log.warning("[reprocess] generation %s abandonnee : %s", url, last)
    return None
async def run(client: discord.Client, ctx: dict) -> None:
    targets = _load_json(_TARGETS, [])
    if not targets:
        log.info("[reprocess] aucune cible")
        return
    progress = _load_json(_PROGRESS, {})
    listen_id = ctx["listen_channel_id"]
    public_base = ctx["public_base"]
    generate = ctx["generate"]
    post_link_thread = ctx["post_link_thread"]
    detect_links = ctx["detect_links"]
    react = ctx["react"]
    log_channel = ctx["log_channel"]
    channel = client.get_channel(listen_id)
    if channel is None:
        log.warning("[reprocess] salon %s introuvable, abandon", listen_id)
        return
    todo = [t for t in targets if progress.get(t, {}).get("status") not in _TERMINAL]
    done_already = len(targets) - len(todo)
    log.info("[reprocess] %d cible(s) au total, %d déjà faites, %d à traiter",
             len(targets), done_already, len(todo))
    await log_channel(
        f"♻️ Relance liens : {len(todo)} à traiter ({done_already} déjà faites sur {len(targets)})."
    )
    ok_count = fail_count = 0
    for idx, mid in enumerate(todo, 1):
        try:
            message = await channel.fetch_message(int(mid))
        except discord.DiscordException as exc:
            progress[mid] = {"status": "skip", "ts": datetime.now().isoformat(timespec="seconds"),
                             "note": f"message introuvable: {exc}"}
            _save_json(_PROGRESS, progress)
            continue
        links = detect_links(message.content)
        if not links:
            progress[mid] = {"status": "skip", "ts": datetime.now().isoformat(timespec="seconds"),
                             "note": "aucun lien détecté"}
            _save_json(_PROGRESS, progress)
            continue
        log.info("[reprocess] %d/%d msg %s : %d lien(s)", idx, len(todo), mid, len(links))
        await _delete_message_thread(message)
        fiches = []
        errors = []
        for platform, url in links:
            fiche = await _generate_with_retry(generate, url)
            if fiche is None:
                errors.append(f"{url}: echec apres retries")
                continue
            try:
                dossier = str(fiche.get("dossier") or fiche.get("id"))
                await _delete_forum_thread(client, dossier)
                try:
                    await publisher.publish_fiche(client, fiche)
                except Exception as exc:
                    log.warning("[reprocess] publication forum échouée %s : %s", url, exc)
                fiches.append(fiche)
            except Exception as exc:
                log.warning("[reprocess] post-traitement échoué %s : %s", url, exc)
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
        await _clear_status_reactions(client, message)
        if fiches:
            try:
                message = await channel.fetch_message(int(mid))
            except discord.DiscordException:
                pass
            first = fiches[0]
            page = f"{public_base}{first.get('pageUrl', '')}"
            try:
                await post_link_thread(message, first, page, None)
            except Exception as exc:
                log.warning("[reprocess] post fil salon liens échoué (msg %s) : %s", mid, exc)
            status = "ok" if not errors else "partial"
            await react(message, "\N{WHITE HEAVY CHECK MARK}")
            ok_count += 1
        else:
            status = "fail"
            await react(message, "\N{CROSS MARK}")
            fail_count += 1
        progress[mid] = {
            "status": status,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "dossiers": [str(f.get("dossier")) for f in fiches],
            "errors": errors,
        }
        _save_json(_PROGRESS, progress)
        if idx % 10 == 0 or idx == len(todo):
            await log_channel(
                f"♻️ Relance liens : {idx}/{len(todo)} traités "
                f"(✅ {ok_count} · ❌ {fail_count}) cette session."
            )
        await asyncio.sleep(2)
    await log_channel(
        f"♻️ Relance liens TERMINÉE : ✅ {ok_count} · ❌ {fail_count} sur {len(todo)} cette session."
    )
    log.info("[reprocess] terminé : ok=%d fail=%d", ok_count, fail_count)