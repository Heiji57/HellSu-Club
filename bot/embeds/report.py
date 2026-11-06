import discord

from app.services.report_service import ReportSnapshot
from bot.embeds.common import truncate
from bot.embeds.statistics import week_range


def _member_lines(rows) -> str:
    return "\n".join(f"{r.display_name} · {r.count}회" for r in rows)


def report_embed(snapshot: ReportSnapshot, *, title: str = "📊 주간 리포트") -> discord.Embed:
    """공개 채널에 게시하는 주간 리포트(REQUIREMENTS 8, 옵션 A: 회원별 횟수 공개)."""
    embed = discord.Embed(
        title=f"{title} · {week_range(snapshot.week)}",
        color=discord.Color.gold(),
    )
    if not snapshot.rows:
        embed.description = "집계 대상 회원이 없습니다."
        return embed
    embed.description = (
        f"목표 **{snapshot.target}회** · 회원 {snapshot.member_count}명 · "
        f"총 출석 {snapshot.total_attendance}회\n"
        f"달성 **{snapshot.achieved_count}명** ({snapshot.achievement_rate:.0%})"
    )
    achieved = [r for r in snapshot.rows if r.achieved]
    missed = [r for r in snapshot.rows if not r.achieved]
    if achieved:
        embed.add_field(
            name=f"🏆 목표 달성 ({len(achieved)}명)",
            value=truncate(_member_lines(achieved), 1024),
            inline=False,
        )
    if missed:
        embed.add_field(
            name=f"💪 다음 주에 도전 ({len(missed)}명)",
            value=truncate(_member_lines(missed), 1024),
            inline=False,
        )
    if snapshot.pending_reviews:
        embed.set_footer(
            text=f"관리자 확인 중인 인증 {snapshot.pending_reviews}건은 반영되지 않았습니다."
        )
    return embed
