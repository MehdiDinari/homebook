from app.services.text import extract_hashtags, extract_mentions


def test_extract_hashtags_and_mentions() -> None:
    text = "Hello #Books #books @12 and @42 and #AI_2026"
    assert extract_hashtags(text) == ["ai_2026", "books"]
    assert extract_mentions(text) == [12, 42]
