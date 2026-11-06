import discord

# 구현된 명령어만 나열한다. Phase가 진행되며 추가된다.
MEMBER_COMMANDS: list[tuple[str, str]] = [
    (
        "/인증 [내용] [사진]",
        "오늘의 운동을 인증합니다. 하루 한 번, 내용이나 사진 중 하나 이상 필요합니다.",
    ),
    ("/내출석", "이번 주 출석 횟수, 남은 횟수, 운동한 요일을 봅니다."),
    ("/내기록 [페이지]", "내 인증 기록과 거절·취소 사유를 봅니다."),
    ("/랭킹", "이번 주 전체 회원의 출석 현황을 봅니다."),
    ("/목표", "이번 주와 다음 주 출석 목표를 봅니다."),
    ("/도움말", "이 안내를 다시 봅니다."),
]

ADMIN_COMMANDS: list[tuple[str, str]] = [
    ("/관리 시스템상태", "봇과 데이터베이스 상태, 활성 회원 수를 확인합니다."),
]

COLOR = discord.Color.blurple()


def help_embed(*, include_admin: bool) -> discord.Embed:
    embed = discord.Embed(
        title="헬스클럽 출석 봇 사용법",
        description="모든 응답은 본인에게만 보입니다. 한 주는 월요일부터 일요일까지입니다.",
        color=COLOR,
    )
    embed.add_field(
        name="회원 명령어",
        value="\n".join(f"`{name}` {desc}" for name, desc in MEMBER_COMMANDS),
        inline=False,
    )
    if include_admin:
        embed.add_field(
            name="관리자 명령어",
            value="\n".join(f"`{name}` {desc}" for name, desc in ADMIN_COMMANDS),
            inline=False,
        )
    return embed
