"""Discord 연결 없이 확인할 수 있는 봇 계층 테스트: 명령어 등록 형태, 오류 메시지, 표시 형식."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from discord import app_commands
from sqlalchemy.exc import OperationalError

from app.container import Services
from app.core.clock import FixedClock
from app.core.database import Database
from app.core.exceptions import InactiveMember
from app.core.week import Week
from app.models import VerificationStatus
from app.repositories.verification import VerificationRef
from app.services.policy_service import TargetOverview
from app.services.statistics_service import HistoryItem, HistoryPage, MemberWeek, MyWeek, WeekStats
from app.services.verification_service import DuplicateMatch, SubmissionResult
from bot.checks import AdminOnly
from bot.commands.admin import format_duration
from bot.embeds.common import format_day, progress_bar
from bot.embeds.help import help_embed
from bot.embeds.statistics import (
    history_embed,
    history_line,
    my_week_embed,
    ranking_embed,
    target_embed,
)
from bot.embeds.verification import review_request_embed, submission_embed
from bot.main import GymBot
from bot.responses import DB_ERROR, GENERIC_ERROR, user_message_for
from tests.fakes import make_settings


@pytest.fixture
async def bot():
    settings = make_settings()
    db = Database(settings.database_url)  # 연결은 실제 사용 시점에만 일어난다
    services = Services.build(settings, db, FixedClock(datetime(2026, 11, 4, tzinfo=UTC)))
    b = GymBot(services)
    await b.add_all_cogs()
    yield b
    await services.aclose()
    await db.dispose()


class TestCommandTree:
    async def test_registers_expected_commands(self, bot):
        names = {c.name for c in bot.tree.get_commands()}
        assert names == {"인증", "내출석", "내기록", "랭킹", "목표", "도움말", "관리"}

    async def test_verify_options(self, bot):
        payload = bot.tree.get_command("인증").to_dict(bot.tree)
        options = {o["name"]: o for o in payload["options"]}
        assert list(options) == ["내용", "사진"]
        assert not any(o.get("required") for o in options.values())
        assert options["내용"]["max_length"] == 1000
        assert options["사진"]["type"] == 11  # ATTACHMENT

    async def test_admin_group_is_hidden_and_guild_only(self, bot):
        group = bot.tree.get_command("관리")
        assert isinstance(group, app_commands.Group)
        assert group.guild_only
        assert group.default_permissions is not None and group.default_permissions.manage_guild
        assert {c.name for c in group.commands} == {
            "회원목록",
            "검토목록",
            "인증상세",
            "출석취소",
            "목표설정",
            "시스템상태",
        }

    async def test_target_options(self, bot):
        group = bot.tree.get_command("관리")
        payload = group.get_command("목표설정").to_dict(bot.tree)
        options = {o["name"]: o for o in payload["options"]}
        assert (options["횟수"]["min_value"], options["횟수"]["max_value"]) == (1, 7)
        assert [c["value"] for c in options["적용"]["choices"]] == ["다음주", "이번주"]
        assert not options["적용"].get("required")

    async def test_payloads_serialize(self, bot):
        # Discord로 보낼 형식으로 직렬화되는지(한글 이름 포함) 확인
        for cmd in bot.tree.get_commands():
            payload = cmd.to_dict(bot.tree)
            assert payload["name"] == cmd.name
            assert 1 <= len(payload["description"]) <= 100


class TestUserMessage:
    def test_app_error_message(self):
        assert user_message_for(InactiveMember()) == InactiveMember.user_message

    def test_wrapped_app_error_message(self):
        wrapped = SimpleNamespace(original=InactiveMember("직접 지정한 메시지"))
        assert user_message_for(wrapped) == "직접 지정한 메시지"

    def test_admin_only(self):
        assert user_message_for(AdminOnly()) == "관리자만 사용할 수 있는 명령어입니다."

    def test_generic_check_failure(self):
        assert "권한" in user_message_for(app_commands.CheckFailure())

    def test_database_error(self):
        err = OperationalError("SELECT 1", {}, ConnectionRefusedError())
        assert user_message_for(SimpleNamespace(original=err)) == DB_ERROR

    def test_unexpected_error_is_hidden(self):
        assert user_message_for(RuntimeError("내부 정보")) == GENERIC_ERROR


class TestPresentation:
    def test_help_fields_fit_discord_limits(self):
        for field in help_embed(include_admin=True).fields:
            assert len(field.value) <= 1024

    def test_help_hides_admin_section_for_members(self):
        assert [f.name for f in help_embed(include_admin=False).fields] == ["회원 명령어"]
        assert [f.name for f in help_embed(include_admin=True).fields] == [
            "회원 명령어",
            "관리자 명령어",
        ]

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(59, "0분"), (3600, "1시간 0분"), (90061, "1일 1시간 1분")],
    )
    def test_format_duration(self, seconds, expected):
        assert format_duration(seconds) == expected


def result(status=VerificationStatus.APPROVED, count=1, target=3, matches=()) -> SubmissionResult:
    return SubmissionResult(
        verification_id=42,
        status=status,
        workout_date=date(2026, 11, 4),
        reason="이전에 제출된 사진과 같은 파일입니다 (인증 #7).",
        content="스쿼트 5세트",
        week_count=count,
        week_target=target,
        matches=matches,
    )


class TestVerificationEmbeds:
    @pytest.mark.parametrize(
        ("count", "target", "expected"),
        [(0, 3, "⬜⬜⬜"), (2, 3, "🟩🟩⬜"), (3, 3, "🟩🟩🟩"), (5, 3, "🟩🟩🟩 +2")],
    )
    def test_progress_bar(self, count, target, expected):
        assert progress_bar(count, target) == expected

    def test_format_day(self):
        assert format_day(date(2026, 11, 8)) == "11월 8일 (일)"

    def test_approved(self):
        embed = submission_embed(result(count=2))
        assert embed.title == "✅ 인증 완료"
        assert embed.description is None
        assert "2 / 3회 (남은 횟수 1회)" in embed.fields[1].value

    def test_goal_achieved(self):
        assert "목표를 달성" in submission_embed(result(count=3)).description

    def test_review_does_not_reveal_other_members(self):
        match = DuplicateMatch(
            7, 0, True, VerificationRef(7, "영희", date(2026, 11, 3), "APPROVED", None)
        )
        embed = submission_embed(result(VerificationStatus.REVIEW_REQUIRED, 0, matches=(match,)))
        assert embed.title == "⏳ 관리자 확인 중"
        text = str(embed.to_dict())
        assert "영희" not in text and "#7" not in text

    def test_review_request_for_admins(self):
        ref = VerificationRef(7, "영희", date(2026, 11, 3), "APPROVED", 999)
        embed = review_request_embed(
            result(
                VerificationStatus.REVIEW_REQUIRED, 0, matches=(DuplicateMatch(7, 0, True, ref),)
            ),
            member=SimpleNamespace(mention="<@1001>", display_name="철수"),
            submitted_at=datetime(2026, 11, 4, 3, 0, tzinfo=UTC),
            guild_id=1,
            admin_channel_id=100,
            image_url="attachment://verification-42.jpg",
        )
        fields = {f.name: f.value for f in embed.fields}
        assert fields["제출 시각"] == "11월 4일 (수) 12:00"
        assert fields["내용"] == "스쿼트 5세트"
        assert "#7 · 완전 일치 · 영희 · 11월 3일 (화) · 승인" in fields["일치한 기존 인증"]
        assert "https://discord.com/channels/1/100/999" in fields["일치한 기존 인증"]
        assert embed.image.url == "attachment://verification-42.jpg"


class TestStatisticsEmbeds:
    week = Week(date(2026, 11, 2))

    def my_week(self, days, today=date(2026, 11, 4), target=3, pending=0) -> MyWeek:
        return MyWeek(self.week, today, target, tuple(date(2026, 11, d) for d in days), pending)

    def test_my_week_in_progress(self):
        embed = my_week_embed(self.my_week([2, 4], pending=1))
        assert embed.description == "목표까지 1회 남았습니다."
        fields = {f.name: f.value for f in embed.fields}
        assert fields["운동한 요일"] == "**월** · **수**"
        assert fields["검토 대기"].startswith("1건")
        assert embed.footer.text == "11월 2일 (월) ~ 11월 8일 (일)"

    def test_my_week_achieved(self):
        assert "달성" in my_week_embed(self.my_week([2, 3, 4])).description

    def test_my_week_out_of_days(self):
        # 일요일인데 2회 남음
        embed = my_week_embed(self.my_week([2], today=date(2026, 11, 8)))
        assert "남은 날(1일)" in embed.description

    def test_history_line_for_revoked(self):
        item = HistoryItem(
            verification_id=9,
            workout_date=date(2026, 11, 3),
            submitted_at=datetime(2026, 11, 3, 0, 30, tzinfo=UTC),
            content="스쿼트\n5세트",
            has_image=True,
            status=VerificationStatus.REJECTED,
            revoked=True,
            reason="다른 날 사진",
        )
        name, value = history_line(item)
        assert name == "11월 3일 (화) · 🚫 출석 취소"
        assert value.splitlines() == [
            "스쿼트 5세트",
            "📷 사진",
            "사유: 다른 날 사진",
            "#9 · 11월 3일 (화) 09:30 제출",
        ]

    def test_history_empty(self):
        assert "/인증" in history_embed(HistoryPage((), 1, 0)).description

    def test_ranking(self):
        rows = (MemberWeek(1, 11, "나래", 4, 3), MemberWeek(2, 22, "가람", 1, 3))
        embed = ranking_embed(WeekStats(self.week, 3, rows, 0), viewer_id=22)
        assert embed.description.splitlines() == ["🏆 나래 · 4회", "▫️ **가람 (나)** · 1회"]
        assert {f.name: f.value for f in embed.fields}["달성"] == "1 / 2명 (50%)"

    def test_target_change_is_announced(self):
        overview = TargetOverview(self.week, 3, self.week.next(), 4)
        assert "변경 예정" in target_embed(overview).fields[1].value
        same = TargetOverview(self.week, 3, self.week.next(), 3)
        assert "변경 예정" not in target_embed(same).fields[1].value
