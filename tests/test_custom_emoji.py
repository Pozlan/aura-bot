from types import SimpleNamespace
from app.utils.custom_emoji import extract_custom_emoji_ids


class FakeEntity:
    def __init__(self, type_, custom_emoji_id=None):
        self.type = type_
        self.custom_emoji_id = custom_emoji_id


def _msg(entities=None, caption_entities=None, reply_to=None):
    return SimpleNamespace(
        entities=entities, caption_entities=caption_entities, reply_to_message=reply_to
    )


def test_extracts_single_custom_emoji():
    msg = _msg(entities=[FakeEntity("custom_emoji", "12345")])
    assert extract_custom_emoji_ids(msg) == ["12345"]


def test_ignores_non_custom_emoji_entities():
    msg = _msg(entities=[FakeEntity("bold"), FakeEntity("custom_emoji", "999")])
    assert extract_custom_emoji_ids(msg) == ["999"]


def test_dedupes_preserving_order():
    msg = _msg(entities=[
        FakeEntity("custom_emoji", "111"),
        FakeEntity("custom_emoji", "222"),
        FakeEntity("custom_emoji", "111"),
    ])
    assert extract_custom_emoji_ids(msg) == ["111", "222"]


def test_no_entities_returns_empty():
    msg = _msg(entities=None)
    assert extract_custom_emoji_ids(msg) == []


def test_reads_from_reply_target_when_present():
    original = _msg(entities=[FakeEntity("custom_emoji", "777")])
    reply = _msg(entities=[FakeEntity("bold")], reply_to=original)
    assert extract_custom_emoji_ids(reply) == ["777"]


def test_checks_caption_entities_too():
    msg = _msg(entities=[], caption_entities=[FakeEntity("custom_emoji", "555")])
    assert extract_custom_emoji_ids(msg) == ["555"]
