"""
Outgoing webhook notifications for task lifecycle events.

Supports Slack Incoming Webhooks, Discord webhooks, and generic HTTP endpoints.
All failures are silently swallowed — notifications must never affect task status.
"""
import httpx


async def send_webhook_notifications(
    event: dict,
    task_title: str,
    run_id: str,
    settings: dict,
) -> None:
    """Fire outgoing notifications for relevant event types. Always non-fatal."""
    event_type = event.get("type")

    if event_type == "done":
        final_status = event.get("status", "")
        if final_status == "completed" and not settings.get("notify_on_complete", True):
            return
        if final_status in ("failed", "aborted") and not settings.get("notify_on_failure", True):
            return
        # "review" uses the complete notification path (it's a success variant)
        if final_status == "review" and not settings.get("notify_on_complete", True):
            return
    elif event_type in ("bash_approval_request", "plan_approval_request"):
        if not settings.get("notify_on_approval", False):
            return
    else:
        return  # All other event types are ignored

    message = _format_message(event_type, event, task_title)
    await _dispatch(message, event_type, event, task_title, run_id, settings)


def _format_message(event_type: str, event: dict, task_title: str) -> str:
    if event_type == "done":
        status = event.get("status", "unknown")
        icons = {"completed": "✅", "failed": "❌", "aborted": "⛔", "review": "👀"}
        icon = icons.get(status, "•")
        return f"{icon} *{task_title}* — {status}"
    if event_type == "bash_approval_request":
        cmd = (event.get("command") or "")[:200]
        return f"⏸ *{task_title}* needs bash approval:\n```{cmd}```"
    if event_type == "plan_approval_request":
        return f"📋 *{task_title}* — plan ready for review"
    return f"• *{task_title}* — {event_type}"


async def _dispatch(
    message: str,
    event_type: str,
    event: dict,
    task_title: str,
    run_id: str,
    settings: dict,
) -> None:
    slack_url = settings.get("slack_webhook_url") or ""
    discord_url = settings.get("discord_webhook_url") or ""
    generic_url = settings.get("generic_webhook_url") or ""

    if not any([slack_url, discord_url, generic_url]):
        return

    status = event.get("status", "")
    color_map = {"completed": "good", "failed": "danger", "aborted": "warning", "review": "#5865F2"}

    payloads: list[tuple[str, dict]] = []

    if slack_url:
        color = color_map.get(status, "#888888")
        payloads.append((
            slack_url,
            {"attachments": [{"color": color, "text": message, "mrkdwn_in": ["text"]}]},
        ))

    if discord_url:
        discord_colors = {"completed": 0x57F287, "failed": 0xED4245, "aborted": 0xFEE75C, "review": 0x5865F2}
        embed: dict = {"description": message.replace("*", "**")}
        if status in discord_colors:
            embed["color"] = discord_colors[status]
        payloads.append((discord_url, {"embeds": [embed]}))

    if generic_url:
        payloads.append((
            generic_url,
            {"event": event_type, "task_title": task_title, "run_id": run_id, "data": event},
        ))

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for url, body in payloads:
                try:
                    await client.post(url, json=body)
                except Exception:
                    pass
    except Exception:
        pass
