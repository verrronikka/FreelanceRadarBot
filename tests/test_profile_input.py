"""Валидация ввода профиля (сценарий A): ≤ 20 слов, каждое 2–64 символа, исключения с минусом."""
from bot.handlers.start import parse_keywords


def test_keywords_and_exclusions():
    kws, exc, err = parse_keywords("Монтаж, After  Effects; -свадьба\nreels")
    assert err is None
    assert kws == ["монтаж", "after effects", "reels"]
    assert exc == ["свадьба"]


def test_too_short_word():
    _, _, err = parse_keywords("a, монтаж")
    assert err and "от 2 до 64" in err


def test_too_many_words():
    _, _, err = parse_keywords(", ".join(f"слово{i}" for i in range(21)))
    assert err and "максимум 20" in err
