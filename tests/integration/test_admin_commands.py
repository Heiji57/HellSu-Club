"""관리자 기능의 봇 계층: 검토 버튼 → 모달 → 처리 → 검토 메시지 갱신, /관리 명령, 지난 결과 알림."""

import re
from datetime import datetime
from types import SimpleNamespace

import discord
import pytest
from sqlalchemy import select

from app.core.exceptions import AlreadyProcessed, ReasonRequired
from app.core.week import KST
from app.models import ClubPolicy, Verification, VerificationStatus
from bot.checks import AdminOnly
from bot.commands.admin import AdminCommands
from bot.commands.me import MeCommands
from bot.commands.verification import VerificationCommands
from bot.views.confirm_view import ConfirmView
from bot.views.review_view import ReasonModal, ReviewButton
from tests.fakes import make_interaction
from tests.images import photo

MEMBER, OTHER, ADMIN = 1001, 1002, 9001


def inter(env, user_id: int = ADMIN, name: str = "관리자", day: int = 4):
    return make_interaction(
        env.bot,
        user_id=user_id,
        display_name=name,
        created_at=datetime(2026, 11, day, 12, 0, tzinfo=KST),
    )


async def submit(env, user_id: int, day: int, *, image: bytes | None = None, name="회원"):
    i = inter(env, user_id, name, day)
    kwargs = {"content": "스쿼트 5세트, 런지 3세트"}
    if image is not None:
        info = env.fetcher.add(image)
        kwargs["photo"] = SimpleNamespace(url=info.url, filename=info.filename, size=info.size)
    await VerificationCommands.verify.callback(VerificationCommands(env.bot), i, **kwargs)
    return i


@pytest.fixture
async def review_post(bot_env):
    """영희(OTHER)의 사진을 철수(MEMBER)가 다시 올려 관리자 채널에 검토 요청이 올라온 상태."""
    await submit(bot_env, OTHER, 3, image=photo(1), name="영희")
    await submit(bot_env, MEMBER, 4, image=photo(1), name="철수")
    [post] = bot_env.channel.sent
    async with bot_env.db.transaction() as s:
        vid = await s.scalar(
            select(Verification.id).where(Verification.status == VerificationStatus.REVIEW_REQUIRED)
        )
    bot_env.admin = True
    return SimpleNamespace(post=post, verification_id=vid, message_id=555_001)


def buttons(view: discord.ui.View) -> dict[str, ReviewButton]:
    return {item.action: item for item in view.children if isinstance(item, ReviewButton)}


async def submit_modal(env, button: ReviewButton, reason: str, user_id: int = ADMIN):
    click = inter(env, user_id)
    await button.callback(click)
    modal = click.response.modal
    assert isinstance(modal, ReasonModal)
    modal.reason._value = reason  # 사용자가 입력한 값
    done = inter(env, user_id)
    await modal.on_submit(done)
    return done


async def status_of(env, vid: int) -> str:
    async with env.db.transaction() as s:
        return await s.scalar(select(Verification.status).where(Verification.id == vid))


class TestReviewButtons:
    async def test_review_post_has_persistent_buttons(self, review_post):
        view = review_post.post["view"]
        assert view.timeout is None
        found = buttons(view)
        assert list(found) == ["approve", "reject", "detail"]
        assert found["approve"].custom_id == f"review:approve:{review_post.verification_id}"

    def test_custom_id_round_trip(self):
        match = re.fullmatch(ReviewButton.__discord_ui_compiled_template__, "review:reject:42")
        assert match and (match["action"], match["vid"]) == ("reject", "42")

    async def test_approve_via_modal_updates_message(self, bot_env, review_post):
        approve = buttons(review_post.post["view"])["approve"]
        done = await submit_modal(bot_env, approve, "")  # 승인 사유는 선택

        assert await status_of(bot_env, review_post.verification_id) == "APPROVED"
        assert "승인했습니다" in done.response.sent[0]["content"]
        message = bot_env.channel.messages[review_post.message_id]
        [edit] = message.edits
        fields = {f.name: f.value for f in edit["embed"].fields}
        assert fields["처리 결과"].startswith(f"✅ 승인 · <@{ADMIN}>")
        assert list(buttons(edit["view"])) == ["revoke", "detail"]  # 승인 후에는 출석 취소만

    async def test_reject_requires_reason(self, bot_env, review_post):
        reject = buttons(review_post.post["view"])["reject"]
        with pytest.raises(ReasonRequired):
            await submit_modal(bot_env, reject, "   ")
        assert await status_of(bot_env, review_post.verification_id) == "REVIEW_REQUIRED"

    async def test_modal_errors_are_shown_privately(self, bot_env, review_post):
        modal = ReasonModal("approve", review_post.verification_id)
        i = inter(bot_env)
        await modal.on_error(i, AlreadyProcessed())
        assert i.response.sent[0] == {"content": "이미 처리된 인증입니다.", "ephemeral": True}

    async def test_second_admin_gets_already_processed(self, bot_env, review_post):
        view = review_post.post["view"]
        await submit_modal(bot_env, buttons(view)["reject"], "중복 사진")
        with pytest.raises(AlreadyProcessed):
            await submit_modal(bot_env, buttons(view)["approve"], "", user_id=9002)
        assert await status_of(bot_env, review_post.verification_id) == "REJECTED"

    async def test_non_admin_cannot_press(self, bot_env, review_post):
        bot_env.admin = False
        click = inter(bot_env, MEMBER)
        await buttons(review_post.post["view"])["approve"].callback(click)
        assert click.response.sent[0]["content"] == AdminOnly.user_message
        assert not hasattr(click.response, "modal")

    async def test_admin_role_is_rechecked_on_submit(self, bot_env, review_post):
        modal = ReasonModal("approve", review_post.verification_id)
        bot_env.admin = False  # 모달을 연 뒤 역할이 회수됨
        await modal.on_submit(inter(bot_env))
        assert await status_of(bot_env, review_post.verification_id) == "REVIEW_REQUIRED"

    async def test_detail_button(self, bot_env, review_post):
        click = inter(bot_env)
        await buttons(review_post.post["view"])["detail"].callback(click)
        [reply] = click.followup.sent
        assert reply["ephemeral"] is True
        assert isinstance(reply["file"], discord.File)
        fields = {f.name: f.value for f in reply["embed"].fields}
        assert "영희" in fields["중복 탐지"]
        assert list(buttons(reply["view"])) == ["approve", "reject"]


class TestAdminCommands:
    async def test_non_admin_is_refused(self, bot_env):
        bot_env.admin = False
        with pytest.raises(AdminOnly):
            await AdminCommands(bot_env.bot).interaction_check(inter(bot_env, MEMBER))

    async def test_pending_list(self, bot_env, review_post):
        i = inter(bot_env)
        await AdminCommands.pending_list.callback(AdminCommands(bot_env.bot), i)
        [reply] = i.followup.sent
        assert reply["embed"].title == "검토 대기 1건"
        [button] = reply["view"].children
        assert button.item.label == f"#{review_post.verification_id} 상세"

    async def test_member_list(self, bot_env, review_post):
        i = inter(bot_env)
        await AdminCommands.member_list.callback(AdminCommands(bot_env.bot), i)
        assert i.followup.sent[0]["embed"].title == "회원 목록 · 활성 2명 / 전체 2명"

    async def test_revoke_command(self, bot_env, review_post):
        approve = buttons(review_post.post["view"])["approve"]
        await submit_modal(bot_env, approve, "")
        i = inter(bot_env)
        await AdminCommands.revoke.callback(
            AdminCommands(bot_env.bot),
            i,
            verification_id=review_post.verification_id,
            reason="운동 사진이 아님",
        )
        assert "출석을 취소했습니다" in i.followup.sent[0]["content"]
        message = bot_env.channel.messages[review_post.message_id]
        fields = {f.name: f.value for f in message.edits[-1]["embed"].fields}
        assert fields["처리 결과"].startswith("🚫 출석 취소")
        assert "운동 사진이 아님" in fields["처리 결과"]
        assert list(buttons(message.edits[-1]["view"])) == ["detail"]

    async def test_target_next_week(self, bot_env):
        i = inter(bot_env)
        await AdminCommands.set_target.callback(
            AdminCommands(bot_env.bot), i, target=5, apply="다음주"
        )
        assert "다음 주(11월 9일 (월)" in i.followup.sent[0]["content"]
        assert (
            await bot_env.bot.services.policies.target_for(
                bot_env.bot.services.statistics.current_week().next()
            )
            == 5
        )

    async def test_target_this_week_needs_confirmation(self, bot_env):
        bot_env.admin = True
        for day in (2, 3):
            await submit(bot_env, MEMBER, day, name="철수")
        i = inter(bot_env)
        await AdminCommands.set_target.callback(
            AdminCommands(bot_env.bot), i, target=2, apply="이번주"
        )
        [reply] = i.followup.sent
        assert "4회 → 2회" in reply["embed"].description
        assert "0명 → 1명" in reply["embed"].description
        async with bot_env.db.transaction() as s:
            assert await s.scalar(select(ClubPolicy).where(ClubPolicy.weekly_target == 2)) is None

        view: ConfirmView = reply["view"]
        click = inter(bot_env)
        await view.confirm.callback(click)
        assert click.response.edited[0]["content"] == "이번 주부터 주간 목표를 2회로 바꿨습니다."
        assert all(b.disabled for b in view.children)
        stats = await bot_env.bot.services.statistics.week_stats()
        assert (stats.target, stats.achieved_count) == (2, 1)

    async def test_confirm_only_by_owner(self, bot_env):
        i = inter(bot_env)
        await AdminCommands.set_target.callback(
            AdminCommands(bot_env.bot), i, target=2, apply="이번주"
        )
        view = i.followup.sent[0]["view"]
        assert await view.interaction_check(inter(bot_env, 9002)) is False


class TestNotices:
    async def test_member_sees_admin_decision_once(self, bot_env, review_post):
        await submit_modal(bot_env, buttons(review_post.post["view"])["reject"], "다른 회원의 사진")
        bot_env.admin = False

        first = inter(bot_env, MEMBER, "철수")
        await MeCommands.my_attendance.callback(MeCommands(bot_env.bot), first)
        assert len(first.followup.sent) == 2
        notice = first.followup.sent[1]
        assert notice["ephemeral"] is True
        assert notice["embed"].title == "📬 지난 인증 결과"
        assert "❌ 거절" in notice["embed"].description
        assert "다른 회원의 사진" in notice["embed"].description

        again = inter(bot_env, MEMBER, "철수")
        await MeCommands.my_attendance.callback(MeCommands(bot_env.bot), again)
        assert len(again.followup.sent) == 1

    async def test_notice_after_verification_command(self, bot_env, review_post):
        await submit_modal(bot_env, buttons(review_post.post["view"])["approve"], "확인함")
        bot_env.admin = False
        i = await submit(bot_env, MEMBER, 5, name="철수")
        assert [m["embed"].title for m in i.followup.sent] == ["✅ 인증 완료", "📬 지난 인증 결과"]
