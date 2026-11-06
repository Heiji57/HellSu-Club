from datetime import date, datetime

from app.core.week import to_kst

WEEKDAYS = "월화수목금토일"


def format_day(d: date) -> str:
    return f"{d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"


def format_time(ts: datetime) -> str:
    """KST로 표시."""
    k = to_kst(ts)
    return f"{format_day(k.date())} {k:%H:%M}"


def progress_bar(count: int, target: int) -> str:
    filled = min(count, target)
    bar = "🟩" * filled + "⬜" * (target - filled)
    if count > target:
        bar += f" +{count - target}"
    return bar


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def message_link(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
