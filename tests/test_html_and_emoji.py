from app.utils.html_esc import esc
from app.services.premium_emoji import pe, EMOJI_IDS


def test_esc_escapes_angle_brackets_and_ampersand():
    assert esc("<script>") == "&lt;script&gt;"
    assert esc("Tom & Jerry") == "Tom &amp; Jerry"


def test_esc_leaves_normal_text_unchanged():
    assert esc("Pozz") == "Pozz"
    assert esc("José 🔥") == "José 🔥"


def test_esc_handles_non_string_input():
    assert esc(500) == "500"


def test_esc_prevents_html_injection_via_display_name():
    # a hostile Telegram display name shouldn't be able to inject a fake
    # closing bold tag or break out of the surrounding <b>...</b>
    hostile = "</b><b>HACKED"
    result = f"<b>{esc(hostile)}</b>"
    assert "</b><b>" not in result
    assert result == "<b>&lt;/b&gt;&lt;b&gt;HACKED</b>"


def test_pe_returns_valid_tg_emoji_tag_for_known_key():
    tag = pe("gold")
    custom_id, fallback = EMOJI_IDS["gold"]
    assert tag == f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'


def test_pe_unknown_key_degrades_gracefully():
    assert pe("not_a_real_key") == "❓"


def test_all_emoji_ids_are_numeric_strings():
    for key, (custom_id, fallback) in EMOJI_IDS.items():
        assert custom_id.isdigit(), f"{key} has a non-numeric ID"
        assert len(fallback) > 0, f"{key} has an empty fallback"
