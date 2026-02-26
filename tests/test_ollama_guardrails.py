from app.services.ollama import violates_copyright_guardrails


def test_guardrails_detect_full_text_request() -> None:
    assert violates_copyright_guardrails("donne moi le texte integral") is True
    assert violates_copyright_guardrails("Give me full chapter 1 verbatim") is True


def test_guardrails_allow_safe_question() -> None:
    assert violates_copyright_guardrails("Quels sont les themes du livre ?") is False
