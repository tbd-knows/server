"""Boundary tests; SQL behavior requires separate isolated PostgreSQL proof."""

from datetime import datetime, timezone

import pytest

from tbd_server.messaging.core import ReceivedMessage
from tbd_server.user.store import Problem


def message(**overrides):
    return ReceivedMessage(
        **{
            "native_id": "fake-native-1",
            "account": "+12125550100",
            "peer": "+12125550101",
            "text": " \n🙂 https://example.test\n ",
            "conversation_id": "conversation_fake",
            "unsupported": False,
            "source_at": datetime(2026, 11, 8, tzinfo=timezone.utc),
            **overrides,
        }
    )


def test_content_is_exact_and_limits_use_utf8_bytes():
    original = " \n🙂 https://example.test\n "
    assert message(text=original).text == original
    assert message(text="🙂" * 16384).text == "🙂" * 16384
    with pytest.raises(Problem) as error:
        message(text="🙂" * 16385)
    assert error.value.status == 413
    assert message(native_id="é" * 128).native_id == "é" * 128
    with pytest.raises(Problem) as error:
        message(native_id="é" * 129)
    assert error.value.status == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": None},
        {"text": ""},
        {"text": 1},
        {"native_id": ""},
        {"unsupported": "false"},
        {"source_at": datetime(2026, 11, 8)},
        {"source_at": None},
        {"channel": "sms"},
        {"account": ""},
        {"peer": ""},
    ],
)
def test_invalid_content_or_identity_is_rejected(overrides):
    with pytest.raises(Problem) as error:
        message(**overrides)
    assert error.value.status == 422


def test_whitespace_and_media_caption_rules():
    assert message(text=" \n\t").text == " \n\t"
    assert message(text=None, unsupported=True).text is None
    assert message(text="caption\n", unsupported=True).text == "caption\n"


@pytest.mark.parametrize("text", ["\ud800", "a\x00b"])
def test_invalid_postgres_text_is_rejected_before_sql(text):
    with pytest.raises(Problem) as error:
        message(text=text)
    assert error.value.status == 422
