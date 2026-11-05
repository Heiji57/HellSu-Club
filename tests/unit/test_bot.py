"""Discord 연결 없이 확인할 수 있는 봇 계층 테스트: 명령어 등록 형태, 오류 메시지, 표시 형식."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from discord import app_commands
from sqlalchemy.exc import OperationalError

from app.container import Services
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.database import Database
from app.core.exceptions import InactiveMember
from bot.checks import AdminOnly
from bot.commands.admin import format_duration
from bot.embeds.help import help_embed
from bot.main import GymBot
from bot.responses import DB_ERROR, GENERIC_ERROR, user_message_for


def make_settings() -> Settings:
    return Settings(
        discord_token="test-token",
        guild_id=1,
        member_role_id=10,
        admin_role_id=20,
        admin_channel_id=100,
        report_channel_id=200,
        database_url="postgresql+asyncpg://u:p@localhost:1/none",
    )


@pytest.fixture
async def bot():
    settings = make_settings()
    db = Database(settings.database_url)  # 연결은 실제 사용 시점에만 일어난다
    services = Services.build(settings, db, FixedClock(datetime(2026, 11, 4, tzinfo=UTC)))
    b = GymBot(services)
    await b.add_all_cogs()
    yield b
    await db.dispose()


class TestCommandTree:
    async def test_registers_expected_commands(self, bot):
        names = {c.name for c in bot.tree.get_commands()}
        assert names == {"도움말", "관리"}

    async def test_admin_group_is_hidden_and_guild_only(self, bot):
        group = bot.tree.get_command("관리")
        assert isinstance(group, app_commands.Group)
        assert group.guild_only
        assert group.default_permissions is not None and group.default_permissions.manage_guild
        assert {c.name for c in group.commands} == {"시스템상태"}

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
