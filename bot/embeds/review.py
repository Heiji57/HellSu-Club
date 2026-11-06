"""관리자용 Embed: 인증 상세, 검토 목록, 회원 목록, 처리 결과. 그리고 회원용 지난 결과 알림."""

from collections.abc import Sequence

import discord

from app.core.week import to_kst_date
from app.models import Member, ReviewDecision, VerificationStatus
from app.services.notification_service import Notice
from app.services.review_service import PendingItem, ReviewOutcome, VerificationDetail
from bot.embeds.common import format_day, format_time, message_link, truncate

STATUS_TITLE = {
    VerificationStatus.APPROVED: "✅ 승인",
    VerificationStatus.REVIEW_REQUIRED: "⏳ 검토 대기",
    VerificationStatus.REJECTED: "❌ 거절",
}
STATUS_COLOR = {
    VerificationStatus.APPROVED: discord.Color.green(),
    VerificationStatus.REVIEW_REQUIRED: discord.Color.orange(),
    VerificationStatus.REJECTED: discord.Color.red(),
}
DECISION_TEXT = {
    ReviewDecision.APPROVE: "승인",
    ReviewDecision.REVIEW: "검토 요청",
    ReviewDecision.REJECT: "거절",
    ReviewDecision.REVOKE: "출석 취소",
}
OUTCOME_ICON = {
    ReviewDecision.APPROVE: "✅",
    ReviewDecision.REJECT: "❌",
    ReviewDecision.REVOKE: "🚫",
}


def status_title(status: VerificationStatus, *, revoked: bool = False) -> str:
    return "🚫 출석 취소" if revoked else STATUS_TITLE[status]


def detail_embed(
    detail: VerificationDetail, *, guild_id: int, admin_channel_id: int, image_url: str | None
) -> discord.Embed:
    v, m = detail.verification, detail.member
    embed = discord.Embed(
        title=f"인증 #{v.id} · {status_title(detail.status, revoked=detail.revoked)}",
        color=STATUS_COLOR[detail.status],
    )
    member_text = f"<@{m.discord_user_id}> ({m.display_name})"
    if not m.is_active:
        member_text += " · 비활성"
    embed.add_field(name="회원", value=member_text)
    embed.add_field(name="출석일", value=format_day(v.workout_date))
    embed.add_field(name="제출 시각", value=format_time(v.submitted_at))
    embed.add_field(
        name="내용", value=truncate(v.content, 1024) if v.content else "(없음)", inline=False
    )
    if v.has_image and image_url is None:
        embed.add_field(name="사진", value="보관 기간이 지나 파일이 삭제되었습니다.", inline=False)

    lines = []
    for r in detail.reviews:
        who = "규칙" if r.reviewer_type == "RULE" else f"<@{r.actor_discord_id}>"
        line = f"{format_time(r.created_at)} · {who} · {DECISION_TEXT[ReviewDecision(r.decision)]}"
        if r.reason:
            line += f" · {truncate(r.reason, 150)}"
        lines.append(line)
    embed.add_field(name="판정 이력", value=truncate("\n".join(lines), 1024) or "-", inline=False)

    if detail.matches:
        match_lines = []
        for ref, distance, exact in detail.matches:
            kind = "완전 일치" if exact else f"차이 {distance}"
            line = (
                f"#{ref.id} · {kind} · {ref.member_display_name} · {format_day(ref.workout_date)}"
            )
            if ref.review_message_id:
                link = message_link(guild_id, admin_channel_id, ref.review_message_id)
                line += f" · [검토 메시지]({link})"
            match_lines.append(line)
        embed.add_field(
            name="중복 탐지", value=truncate("\n".join(match_lines), 1024), inline=False
        )
    if image_url:
        embed.set_image(url=image_url)
    return embed


def mark_processed(embed: discord.Embed, outcome: ReviewOutcome) -> discord.Embed:
    """관리자 채널의 검토 요청 메시지에 처리 결과를 표시한다."""
    embed = embed.copy()
    embed.color = STATUS_COLOR[outcome.status]
    value = (
        f"{OUTCOME_ICON[outcome.decision]} {DECISION_TEXT[outcome.decision]}"
        f" · <@{outcome.actor_discord_id}>"
    )
    if outcome.reason:
        value += f"\n사유: {truncate(outcome.reason, 900)}"
    # 같은 이름의 필드가 있으면(승인 후 출석 취소) 덮어쓴다
    for i, field in enumerate(embed.fields):
        if field.name == "처리 결과":
            embed.set_field_at(i, name="처리 결과", value=value, inline=False)
            break
    else:
        embed.add_field(name="처리 결과", value=value, inline=False)
    embed.set_footer(text=f"인증 #{outcome.verification_id}")
    return embed


def pending_embed(
    items: Sequence[PendingItem], total: int, *, guild_id: int, admin_channel_id: int
) -> discord.Embed:
    embed = discord.Embed(title=f"검토 대기 {total}건", color=discord.Color.orange())
    if not items:
        embed.description = "검토할 인증이 없습니다."
        return embed
    lines = []
    for it in items:
        line = (
            f"**#{it.verification_id}** · {it.member_display_name} · {format_day(it.workout_date)}"
        )
        if it.review_message_id:
            line += f" · [메시지]({message_link(guild_id, admin_channel_id, it.review_message_id)})"
        if it.reason:
            line += f"\n　{truncate(it.reason, 120)}"
        lines.append(line)
    embed.description = truncate("\n".join(lines), 4000)
    if total > len(items):
        embed.set_footer(text=f"오래된 순으로 {len(items)}건만 표시합니다.")
    return embed


def members_embed(members: Sequence[Member]) -> discord.Embed:
    active = [m for m in members if m.is_active]
    embed = discord.Embed(
        title=f"회원 목록 · 활성 {len(active)}명 / 전체 {len(members)}명",
        color=discord.Color.blurple(),
    )
    if not members:
        embed.description = "등록된 회원이 없습니다. 회원 역할을 부여하면 자동으로 등록됩니다."
        return embed
    lines = []
    for m in members:
        mark = "🟢" if m.is_active else "⚪"
        line = f"{mark} {m.display_name} · <@{m.discord_user_id}> · `{m.discord_user_id}`"
        line += f" · 가입 {format_day(to_kst_date(m.joined_at))}"
        if not m.is_active and m.deactivated_at:
            line += f" · 비활성 {format_day(to_kst_date(m.deactivated_at))}"
        lines.append(line)
    embed.description = truncate("\n".join(lines), 4000)
    return embed


def notices_embed(notices: Sequence[Notice]) -> discord.Embed:
    lines = []
    for n in notices:
        if n.revoked:
            text = "🚫 출석이 취소되었습니다"
        elif n.status is VerificationStatus.APPROVED:
            text = "✅ 승인되어 출석으로 인정되었습니다"
        else:
            text = "❌ 거절되었습니다"
        line = f"**{format_day(n.workout_date)}** 인증 #{n.verification_id} · {text}"
        if n.reason and n.status is VerificationStatus.REJECTED:
            line += f"\n　사유: {truncate(n.reason, 200)}"
        lines.append(line)
    return discord.Embed(
        title="📬 지난 인증 결과",
        description=truncate("\n".join(lines), 4000),
        color=discord.Color.blurple(),
    )
