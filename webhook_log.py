from __future__ import annotations
import discord
import httpx
async def send_log(
    webhook_url: str | None,
    channel: discord.abc.Messageable | None,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    username: str = "AboekaBot",
) -> None:
    if webhook_url:
        payload: dict = {"username": username}
        if content:
            payload["content"] = content[:2000]
        if embed is not None:
            payload["embeds"] = [embed.to_dict()]
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(webhook_url, json=payload)
            return
        except Exception:
            pass
    if channel is None:
        return
    try:
        if embed is not None:
            await channel.send(embed=embed)
        elif content:
            await channel.send(content[:1900])
    except discord.DiscordException:
        pass