class AppError(Exception):
    """사용자에게 그대로 보여줄 수 있는 메시지를 가진 도메인 예외의 기반 클래스."""

    user_message: str = "요청을 처리할 수 없습니다."

    def __init__(self, user_message: str | None = None) -> None:
        if user_message is not None:
            self.user_message = user_message
        super().__init__(self.user_message)


class NotAMember(AppError):
    user_message = "헬스클럽 회원 역할이 있어야 사용할 수 있습니다."


class InactiveMember(AppError):
    user_message = "비활성화된 회원입니다. 관리자에게 문의해 주세요."
