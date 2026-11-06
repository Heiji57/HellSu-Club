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


class AlreadyVerifiedToday(AppError):
    user_message = "이미 오늘 인증했습니다. 내일 다시 인증해 주세요."


class WrongChannel(AppError):
    user_message = "인증 채널에서만 사용할 수 있습니다."


# --- 입력 검증: 인증으로 저장하지 않고 안내만 한다 (REQUIREMENTS 3.3) ---


class InvalidSubmission(AppError):
    """사용자가 입력을 고쳐 다시 제출하면 되는 오류."""


class EmptySubmission(InvalidSubmission):
    user_message = "운동 내용이나 사진 중 하나 이상을 입력해 주세요."


class TextTooShort(InvalidSubmission):
    user_message = "운동 내용을 조금 더 자세히 적어 주세요."


class UnsupportedImageFormat(InvalidSubmission):
    user_message = "JPEG, PNG, WEBP 형식의 사진만 올릴 수 있습니다."


class ImageTooLarge(InvalidSubmission):
    user_message = "사진 용량이 너무 큽니다."

    @classmethod
    def for_limit(cls, max_bytes: int) -> "ImageTooLarge":
        return cls(f"사진 용량이 너무 큽니다. {max_bytes / 1024 / 1024:g}MB 이하로 올려 주세요.")


class ImageUnreadable(InvalidSubmission):
    user_message = "사진을 불러오지 못했습니다. 다시 시도해 주세요."


# --- 관리자 처리 (REQUIREMENTS 6) ---


class VerificationNotFound(AppError):
    user_message = "해당 번호의 인증을 찾을 수 없습니다."


class AlreadyProcessed(AppError):
    """조건부 UPDATE가 0행: 다른 관리자가 먼저 처리했거나 상태가 맞지 않는다."""

    user_message = "이미 처리된 인증입니다."


class ReasonRequired(AppError):
    user_message = "사유를 입력해 주세요."
