from datetime import date

import pytest

from app.core.exceptions import EmptySubmission, InvalidSubmission, TextTooShort
from app.models import ReviewDecision, VerificationStatus
from app.services.verification_service import (
    DuplicateMatch,
    SubmissionResult,
    decide,
    find_duplicates,
    normalize_content,
    validate_input,
)

A = "0" * 16


class TestInput:
    @pytest.mark.parametrize(
        ("raw", "expected"), [(None, None), ("  ", None), (" 스쿼트 ", "스쿼트")]
    )
    def test_normalize(self, raw, expected):
        assert normalize_content(raw) == expected

    def test_empty(self):
        with pytest.raises(EmptySubmission):
            validate_input(None, has_image=False, text_min_length=10)

    def test_short_text_only(self):
        # 공백을 빼면 9자
        with pytest.raises(TextTooShort) as e:
            validate_input("스쿼트 5세트 했음 ㅎ", has_image=False, text_min_length=10)
        assert "지금 9자" in e.value.user_message

    def test_text_only_long_enough(self):
        validate_input(
            "스쿼트 5세트 했습니다", has_image=False, text_min_length=10
        )  # 공백 제외 10자

    def test_short_text_with_image_is_fine(self):
        validate_input("오운완", has_image=True, text_min_length=10)
        validate_input(None, has_image=True, text_min_length=10)

    def test_too_long(self):
        with pytest.raises(InvalidSubmission):
            validate_input("가" * 1001, has_image=True, text_min_length=10)


class TestDuplicates:
    def test_no_candidates(self):
        assert find_duplicates(A, A, sha_matches=[], candidates=[], threshold=4) == ()

    def test_exact_match_comes_first_and_is_not_repeated(self):
        candidates = [(1, A), (2, "000000000000000f"), (3, "00000000000000ff")]
        matches = find_duplicates(A, A, sha_matches=[3], candidates=candidates, threshold=4)
        assert [(m.verification_id, m.distance, m.exact) for m in matches] == [
            (3, 0, True),
            (1, 0, False),
            (2, 4, False),
        ]

    def test_threshold_is_inclusive(self):
        candidates = [(1, "000000000000000f"), (2, "000000000000001f")]  # 거리 4, 5
        matches = find_duplicates(A, A, sha_matches=[], candidates=candidates, threshold=4)
        assert [m.verification_id for m in matches] == [1]

    def test_keeps_closest_ten(self):
        candidates = [(i, A) for i in range(20)]
        assert len(find_duplicates(A, A, sha_matches=[], candidates=candidates, threshold=4)) == 10


class TestDecide:
    def test_text_only_is_approved(self):
        d = decide((), has_image=False)
        assert (d.status, d.decision) == (VerificationStatus.APPROVED, ReviewDecision.APPROVE)

    def test_unique_image_is_approved(self):
        assert decide((), has_image=True).status is VerificationStatus.APPROVED

    def test_exact_match_needs_review(self):
        d = decide((DuplicateMatch(7, 0, True),), has_image=True)
        assert (d.status, d.decision) == (
            VerificationStatus.REVIEW_REQUIRED,
            ReviewDecision.REVIEW,
        )
        assert "#7" in d.reason and "같은 파일" in d.reason
        assert d.details == {"matches": [{"verification_id": 7, "distance": 0, "exact": True}]}

    def test_similar_match_needs_review(self):
        d = decide((DuplicateMatch(8, 3, False),), has_image=True)
        assert d.status is VerificationStatus.REVIEW_REQUIRED
        assert "차이 3" in d.reason


@pytest.mark.parametrize(
    ("status", "count", "target", "expected"),
    [
        (VerificationStatus.APPROVED, 3, 3, True),
        (VerificationStatus.APPROVED, 2, 3, False),
        (VerificationStatus.APPROVED, 4, 3, False),  # 이미 달성한 뒤의 추가 인증
        (VerificationStatus.REVIEW_REQUIRED, 3, 3, False),
    ],
)
def test_just_achieved(status, count, target, expected):
    result = SubmissionResult(1, status, date(2026, 11, 4), "", None, count, target)
    assert result.just_achieved is expected
