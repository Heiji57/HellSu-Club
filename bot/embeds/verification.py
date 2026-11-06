from datetime import datetime

import discord

from app.models import VerificationStatus
from app.services.verification_service import DuplicateMatch, SubmissionResult
from bot.embeds.common import format_day, format_time, message_link, progress_bar, truncate

STATUS_LABELS = {
    VerificationStatus.APPROVED: "승인",
    VerificationStatus.REVIEW_REQUIRED: "검토 대기",
    VerificationStatus.REJECTED: "거절",
}


def submission_embed(result: SubmissionResult) -> discord.Embed:
    """제출한 회원에게만 보이는 결과. 다른 회원의 인증 정보는 담지 않는다."""
    if result.approved:
        embed = discord.Embed(title="✅ 인증 완료", color=discord.Color.green())
        if result.just_achieved:
            embed.description = "🎉 이번 주 목표를 달성했습니다!"
    else:
        embed = discord.Embed(
            title="⏳ 관리자 확인 중",
            description=(
                "이전에 올라온 사진과 같거나 비슷한 사진이라 관리자가 확인합니다.\n"
                "결과는 다음에 봇 명령어를 사용할 때 알려 드립니다."
            ),
            color=discord.Color.orange(),
        )
    embed.add_field(name="출석일", value=format_day(result.workout_date))
    progress = f"{result.week_count} / {result.week_target}회"
    remaining = result.week_target - result.week_count
    if remaining > 0:
        progress += f" (남은 횟수 {remaining}회)"
    embed.add_field(
        name="이번 주", value=f"{progress}\n{progress_bar(result.week_count, result.week_target)}"
    )
    embed.set_footer(text=f"인증 #{result.verification_id}")
    return embed


def review_request_embed(
    result: SubmissionResult,
    *,
    member: discord.abc.User,
    submitted_at: datetime,
    guild_id: int,
    admin_channel_id: int,
    image_url: str | None,
) -> discord.Embed:
    """관리자 채널에 게시하는 검토 요청."""
    embed = discord.Embed(
        title=f"🔍 검토 요청 · 인증 #{result.verification_id}",
        color=discord.Color.orange(),
    )
    embed.add_field(name="회원", value=f"{member.mention} ({member.display_name})")
    embed.add_field(name="출석일", value=format_day(result.workout_date))
    embed.add_field(name="제출 시각", value=format_time(submitted_at))
    if result.content:
        embed.add_field(name="내용", value=truncate(result.content, 1024), inline=False)
    embed.add_field(name="판정 사유", value=result.reason, inline=False)
    if result.matches:
        lines = [_match_line(m, guild_id, admin_channel_id) for m in result.matches]
        value = truncate("\n".join(lines), 1024)
        embed.add_field(name="일치한 기존 인증", value=value, inline=False)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=f"인증 #{result.verification_id}")
    return embed


def _match_line(match: DuplicateMatch, guild_id: int, admin_channel_id: int) -> str:
    kind = "완전 일치" if match.exact else f"차이 {match.distance}"
    line = f"#{match.verification_id} · {kind}"
    ref = match.ref
    if ref is not None:
        status = STATUS_LABELS.get(VerificationStatus(ref.status), ref.status)
        line += f" · {ref.member_display_name} · {format_day(ref.workout_date)} · {status}"
        if ref.review_message_id:
            link = message_link(guild_id, admin_channel_id, ref.review_message_id)
            line += f" · [검토 메시지]({link})"
    return line
