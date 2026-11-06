"""Discord 역할 기반 권한 확인 (ADR-0004)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.core.exceptions import NotAMember

if TYPE_CHECKING:
    from bot.main import GymBot


class AdminOnly(app_commands.CheckFailure):
    """권한 확인 단계의 예외는 CheckFailure여야 트리의 on_error로 전달된다."""

    user_message = "관리자만 사용할 수 있는 명령어입니다."


def has_role(user: discord.abc.User, role_id: int) -> bool:
    """길드 멤버이고 해당 역할을 가지고 있는지. DM 등 길드 밖에서는 항상 False."""
    return isinstance(user, discord.Member) and user.get_role(role_id) is not None


def bot_of(interaction: discord.Interaction) -> GymBot:
    return interaction.client  # type: ignore[return-value]


def is_admin(interaction: discord.Interaction) -> bool:
    return has_role(interaction.user, bot_of(interaction).services.settings.admin_role_id)


def is_member(interaction: discord.Interaction) -> bool:
    return has_role(interaction.user, bot_of(interaction).services.settings.member_role_id)


async def ensure_member(interaction: discord.Interaction, *, allow_admin: bool = False) -> None:
    """회원 역할을 확인하고 DB 회원 정보를 맞춘다. 역할 변경 이벤트를 놓쳤어도 여기서 등록된다.

    allow_admin이면 회원 역할이 없는 관리자도 통과한다(조회 전용 명령).
    """
    if is_member(interaction):
        user = interaction.user
        await bot_of(interaction).services.members.sync(
            user.id, user.display_name, has_member_role=True
        )
        return
    if allow_admin and is_admin(interaction):
        return
    raise NotAMember()
