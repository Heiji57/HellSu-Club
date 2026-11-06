import discord

from app.core.week import Week
from app.models import VerificationStatus
from app.services.policy_service import TargetOverview
from app.services.statistics_service import HistoryItem, HistoryPage, MyWeek, WeekStats
from bot.embeds.common import WEEKDAYS, format_day, format_time, progress_bar, truncate


def week_range(week: Week) -> str:
    return f"{format_day(week.start)} ~ {format_day(week.end)}"


def my_week_embed(data: MyWeek) -> discord.Embed:
    if data.achieved:
        color, headline = discord.Color.green(), "🎉 이번 주 목표를 달성했습니다!"
    elif data.remaining > data.days_left:
        color = discord.Color.light_grey()
        headline = f"이번 주에 남은 날({data.days_left}일)보다 남은 횟수가 많습니다."
    else:
        color, headline = discord.Color.blurple(), f"목표까지 {data.remaining}회 남았습니다."

    embed = discord.Embed(title="이번 주 내 출석", description=headline, color=color)
    embed.add_field(
        name="진행",
        value=f"{data.count} / {data.target}회\n{progress_bar(data.count, data.target)}",
        inline=False,
    )
    embed.add_field(name="운동한 요일", value=_weekday_line(data), inline=False)
    if data.pending_reviews:
        embed.add_field(
            name="검토 대기",
            value=f"{data.pending_reviews}건 (승인되면 횟수에 더해집니다)",
            inline=False,
        )
    embed.set_footer(text=week_range(data.week))
    return embed


def _weekday_line(data: MyWeek) -> str:
    attended = {d.weekday() for d in data.days}
    names = [WEEKDAYS[i] for i in sorted(attended)]
    return " · ".join(f"**{n}**" for n in names) if names else "아직 없습니다."


STATUS_TEXT = {
    VerificationStatus.APPROVED: "✅ 승인",
    VerificationStatus.REVIEW_REQUIRED: "⏳ 검토 대기",
    VerificationStatus.REJECTED: "❌ 거절",
}


def history_line(item: HistoryItem) -> tuple[str, str]:
    status = "🚫 출석 취소" if item.revoked else STATUS_TEXT[item.status]
    name = f"{format_day(item.workout_date)} · {status}"
    parts = []
    if item.content:
        parts.append(truncate(item.content.replace("\n", " "), 80))
    if item.has_image:
        parts.append("📷 사진")
    if item.reason:
        parts.append(f"사유: {truncate(item.reason, 200)}")
    parts.append(f"#{item.verification_id} · {format_time(item.submitted_at)} 제출")
    return name, "\n".join(parts)


def history_embed(page: HistoryPage) -> discord.Embed:
    embed = discord.Embed(title="내 인증 기록", color=discord.Color.blurple())
    if not page.items:
        embed.description = "아직 인증 기록이 없습니다. `/인증`으로 시작해 보세요."
        return embed
    for item in page.items:
        name, value = history_line(item)
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text=f"전체 {page.total}건 · {page.page} / {page.total_pages} 페이지")
    return embed


def ranking_embed(stats: WeekStats, *, viewer_id: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"이번 주 현황 · 목표 {stats.target}회",
        color=discord.Color.gold(),
    )
    if not stats.rows:
        embed.description = "집계 대상 회원이 없습니다."
    else:
        lines = []
        for row in stats.rows:
            mark = "🏆" if row.achieved else "▫️"
            name = row.display_name
            if row.discord_user_id == viewer_id:
                name = f"**{name} (나)**"
            lines.append(f"{mark} {name} · {row.count}회")
        embed.description = truncate("\n".join(lines), 4000)
        embed.add_field(
            name="달성",
            value=f"{stats.achieved_count} / {stats.member_count}명 ({stats.achievement_rate:.0%})",
        )
        embed.add_field(name="총 출석", value=f"{stats.total_attendance}회")
    embed.set_footer(text=week_range(stats.week))
    return embed


def target_embed(overview: TargetOverview) -> discord.Embed:
    embed = discord.Embed(title="주간 목표", color=discord.Color.blurple())
    embed.add_field(
        name="이번 주",
        value=f"**{overview.this_target}회**\n{week_range(overview.this_week)}",
        inline=False,
    )
    next_value = f"**{overview.next_target}회**"
    if overview.changes_next_week:
        next_value += f" (이번 주 {overview.this_target}회에서 변경 예정)"
    embed.add_field(
        name="다음 주",
        value=f"{next_value}\n{week_range(overview.next_week)}",
        inline=False,
    )
    embed.set_footer(text="한 주는 월요일 00:00부터 일요일 23:59까지입니다 (한국 시간).")
    return embed
