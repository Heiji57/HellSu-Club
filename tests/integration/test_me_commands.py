"""/내출석, /내기록, /랭킹, /목표 핸들러를 가짜 Interaction으로 실행한다."""

from datetime import datetime

import pytest

from app.core.exceptions import NotAMember
from app.core.week import KST
from bot.commands.me import MeCommands
from bot.commands.verification import VerificationCommands
from bot.views.pagination_view import PaginationView
from tests.fakes import make_interaction

USER_ID = 1001
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=KST)


def interaction(env, user_id: int = USER_ID, name: str = "철수"):
    return make_interaction(env.bot, user_id=user_id, display_name=name, created_at=NOW)


async def verify_on(env, day: int, user_id: int = USER_ID, name: str = "철수") -> None:
    inter = make_interaction(
        env.bot,
        user_id=user_id,
        display_name=name,
        created_at=datetime(2026, 11, day, 9, 0, tzinfo=KST),
    )
    await VerificationCommands.verify.callback(
        VerificationCommands(env.bot), inter, content="스쿼트 5세트, 런지 3세트"
    )


async def call(env, command, inter, **kwargs):
    await command.callback(MeCommands(env.bot), inter, **kwargs)
    [reply] = inter.followup.sent
    assert reply["ephemeral"] is True
    return reply


async def test_my_attendance(bot_env):
    await verify_on(bot_env, 2)
    await verify_on(bot_env, 4)
    reply = await call(bot_env, MeCommands.my_attendance, interaction(bot_env))
    fields = {f.name: f.value for f in reply["embed"].fields}
    assert fields["진행"].startswith("2 / 4회")
    assert fields["운동한 요일"] == "**월** · **수**"


async def test_my_attendance_registers_member_on_first_use(bot_env):
    reply = await call(bot_env, MeCommands.my_attendance, interaction(bot_env))
    assert reply["embed"].fields[1].value == "아직 없습니다."


async def test_my_history_single_page_has_no_buttons(bot_env):
    await verify_on(bot_env, 2)
    reply = await call(bot_env, MeCommands.my_history, interaction(bot_env), page=1)
    assert "view" not in reply
    assert len(reply["embed"].fields) == 1


async def test_my_history_pages(bot_env):
    for day in range(1, 8):
        await verify_on(bot_env, day)
    reply = await call(bot_env, MeCommands.my_history, interaction(bot_env), page=1)
    view = reply["view"]
    assert isinstance(view, PaginationView)
    assert (view.page, view.total_pages, view.prev_button.disabled) == (1, 2, True)
    assert view.message is not None  # 시간이 지나면 버튼을 끄기 위해 보관한다

    click = interaction(bot_env)
    await view.next_button.callback(click)
    [edit] = click.response.edited
    assert len(edit["embed"].fields) == 2
    assert (view.page, view.next_button.disabled, view.prev_button.disabled) == (2, True, False)

    await view.on_timeout()
    assert all(item.disabled for item in view.children)
    assert view.message.edits


async def test_only_owner_can_turn_pages(bot_env):
    for day in range(1, 8):
        await verify_on(bot_env, day)
    reply = await call(bot_env, MeCommands.my_history, interaction(bot_env), page=1)
    other = interaction(bot_env, user_id=2002, name="영희")
    assert await reply["view"].interaction_check(other) is False
    assert other.response.sent[0]["ephemeral"] is True


async def test_ranking_marks_viewer(bot_env):
    await verify_on(bot_env, 2)
    await verify_on(bot_env, 2, user_id=2002, name="영희")
    await verify_on(bot_env, 3, user_id=2002, name="영희")
    reply = await call(bot_env, MeCommands.ranking, interaction(bot_env))
    lines = reply["embed"].description.splitlines()
    assert lines == ["▫️ 영희 · 2회", "▫️ **철수 (나)** · 1회"]


async def test_ranking_and_target_allow_admin_without_member_role(bot_env):
    bot_env.member, bot_env.admin = False, True
    await call(bot_env, MeCommands.ranking, interaction(bot_env))
    reply = await call(bot_env, MeCommands.target, interaction(bot_env))
    assert reply["embed"].fields[0].value.startswith("**4회**")


@pytest.mark.parametrize("command", [MeCommands.my_attendance, MeCommands.ranking])
async def test_non_member_is_refused(bot_env, command):
    bot_env.member = False
    with pytest.raises(NotAMember):
        await command.callback(MeCommands(bot_env.bot), interaction(bot_env))
