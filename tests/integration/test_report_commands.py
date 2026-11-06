"""리포트 게시 작업과 /관리 주간통계·주간리포트·시스템상태 (봇 계층)."""

from datetime import datetime

from sqlalchemy import select

from app.core.week import KST
from app.models import WeeklyReport
from bot.commands.admin import AdminCommands
from bot.commands.verification import VerificationCommands
from bot.tasks.weekly_report import WeeklyReportTask
from tests.fakes import FakeChannel, make_interaction

REPORT_CHANNEL = 200  # make_settings의 report_channel_id
ADMIN = 9001


def at(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=KST)


async def verify(env, user_id: int, name: str, when: datetime) -> None:
    env.clock.set(when)
    i = make_interaction(env.bot, user_id=user_id, display_name=name, created_at=when)
    await VerificationCommands.verify.callback(
        VerificationCommands(env.bot), i, content="스쿼트 5세트, 런지 3세트"
    )


async def setup_last_week(env, monkeypatch) -> FakeChannel:
    report_channel = FakeChannel()
    admin_channel = env.channel
    monkeypatch.setattr(
        env.bot,
        "get_channel",
        lambda cid: {REPORT_CHANNEL: report_channel, 100: admin_channel}.get(cid),
    )
    for day in (26, 27, 28, 29):
        await verify(env, 1001, "철수", at(10, day))
    await verify(env, 1002, "영희", at(10, 27))
    env.clock.set(at(11, 4))
    env.admin = True
    return report_channel


def admin(env):
    return make_interaction(env.bot, user_id=ADMIN, display_name="관리자", created_at=at(11, 4))


async def test_task_posts_report_once(bot_env, monkeypatch):
    report_channel = await setup_last_week(bot_env, monkeypatch)
    task = WeeklyReportTask(bot_env.bot)
    await task.loop.coro(task)
    await task.loop.coro(task)  # 5분 뒤 다시 돌아도 한 번만

    [post] = report_channel.sent
    embed = post["embed"]
    assert embed.title == "📊 주간 리포트 · 10월 26일 (월) ~ 11월 1일 (일)"
    assert "달성 **1명** (50%)" in embed.description
    fields = {f.name: f.value for f in embed.fields}
    assert fields["🏆 목표 달성 (1명)"] == "철수 · 4회"
    assert fields["💪 다음 주에 도전 (1명)"] == "영희 · 1회"
    assert post["allowed_mentions"].users is False


async def test_task_failure_is_retried(bot_env, monkeypatch):
    report_channel = await setup_last_week(bot_env, monkeypatch)
    calls = {"n": 0}
    original = report_channel.send

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("일시적 실패")
        return await original(**kwargs)

    monkeypatch.setattr(report_channel, "send", flaky)
    task = WeeklyReportTask(bot_env.bot)
    await task.loop.coro(task)  # 실패해도 예외로 작업이 멈추지 않는다
    assert report_channel.sent == []
    await task.loop.coro(task)
    assert len(report_channel.sent) == 1


async def test_weekly_report_preview_and_publish(bot_env, monkeypatch):
    report_channel = await setup_last_week(bot_env, monkeypatch)
    cog = AdminCommands(bot_env.bot)

    i = admin(bot_env)
    await AdminCommands.weekly_report.callback(cog, i, publish=False)
    assert "아직 게시되지 않았습니다" in i.followup.sent[0]["content"]
    assert report_channel.sent == []

    i = admin(bot_env)
    await AdminCommands.weekly_report.callback(cog, i, publish=True)
    assert i.followup.sent[0]["content"] == f"<#{REPORT_CHANNEL}>에 게시했습니다."
    async with bot_env.db.transaction() as s:
        row = await s.scalar(select(WeeklyReport))
    assert row.message_id == 555_001

    i = admin(bot_env)
    await AdminCommands.weekly_report.callback(cog, i, publish=True)
    assert i.followup.sent[0]["content"] == "이미 게시된 리포트입니다."
    i = admin(bot_env)
    await AdminCommands.weekly_report.callback(cog, i, publish=False)
    assert i.followup.sent[0]["content"].startswith("✅ 게시됨")


async def test_week_stats(bot_env, monkeypatch):
    await setup_last_week(bot_env, monkeypatch)
    i = admin(bot_env)
    await AdminCommands.week_stats.callback(AdminCommands(bot_env.bot), i, which="지난주")
    embed = i.followup.sent[0]["embed"]
    assert embed.title.startswith("📈 주간 통계 · 10월 26일")
    assert embed.footer.text == "실시간 집계 · 검토 대기 0건"


async def test_system_status(bot_env, monkeypatch):
    await setup_last_week(bot_env, monkeypatch)
    i = admin(bot_env)
    await AdminCommands.system_status.callback(AdminCommands(bot_env.bot), i)
    fields = {f.name: f.value for f in i.followup.sent[0]["embed"].fields}
    assert fields["데이터베이스"].startswith("🟢")
    assert fields["활성 회원"] == "2명"
    assert fields["검토 대기"] == "0건"
    assert fields["최근 리포트"] == "10월 26일 (월) 주 · ⏳ 미게시"
    assert fields["이미지 저장소"].startswith("0개")
