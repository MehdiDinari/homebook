from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app.core.config import settings


_COPYRIGHT_GUARD_PATTERNS = [
    re.compile(r"\b(full\s+chapter|complete\s+book|verbatim|mot\s+pour\s+mot)\b", re.IGNORECASE),
    re.compile(r"\b(entier|int[eé]gral|texte\s+complet)\b", re.IGNORECASE),
]

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def violates_copyright_guardrails(user_message: str) -> bool:
    return any(p.search(user_message) for p in _COPYRIGHT_GUARD_PATTERNS)


def _sanitize_text(text: str) -> str:
    return _CTRL_RE.sub("", (text or "")).strip()


def _looks_like_prompt_echo(answer: str, user_message: str) -> bool:
    low = (answer or "").strip().lower()
    if not low:
        return True
    bad_markers = [
        "user question:",
        "instruction:",
        "instructions:",
        "answer in ",
        "known themes",
        "conversation memory:",
        "book title:",
        "book author:",
        "book context:",
    ]
    if any(marker in low for marker in bad_markers):
        return True
    user_low = _sanitize_text(user_message).lower()
    if user_low and len(user_low) >= 8 and low.startswith(user_low):
        return True
    return False


def _book_fallback(
    *,
    book_title: str,
    book_author: str,
    book_description: str,
    user_message: str,
) -> str:
    title = (book_title or "").strip() or "ce livre"
    author = (book_author or "").strip()
    user_low = (user_message or "").lower()
    desc = _sanitize_text(book_description or "")

    if "auteur" in user_low or "author" in user_low:
        if author:
            return f"L'auteur de {title} est {author}."
        return f"Je n'ai pas l'auteur exact pour {title} dans le contexte actuel."

    if "résum" in user_low or "resum" in user_low or "summary" in user_low:
        if desc:
            pieces = [x.strip() for x in re.split(r"[.!?]+", desc) if x.strip()]
            brief = ". ".join(pieces[:2]).strip()
            if brief:
                if not brief.endswith("."):
                    brief += "."
                return _sanitize_text(f"Résumé rapide de {title}: {brief}")
        author_part = f" de {author}" if author else ""
        return (
            f"Résumé rapide de {title}: c'est une oeuvre{author_part} "
            "centrée sur des thèmes humains et existentiels."
        )

    if desc:
        return _sanitize_text(f"Contexte utile sur {title}: {desc[:420]}")
    return f"Je peux t'aider sur {title} (résumé, auteur, thèmes). Pose une question précise."


def _candidate_base_urls(configured_base_url: str) -> list[str]:
    base = (configured_base_url or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:11434"

    candidates = [base]
    for fallback in ("http://127.0.0.1:11434", "http://localhost:11434"):
        if fallback not in candidates:
            candidates.append(fallback)

    parsed = urlparse(base)
    if (parsed.hostname or "").lower() == "ollama":
        host_fallback = "http://host.docker.internal:11434"
        if host_fallback not in candidates:
            candidates.append(host_fallback)

    return candidates


async def ask_ollama(
    *,
    book_title: str,
    book_author: str,
    book_description: str,
    book_categories: list[str] | None,
    user_message: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    system_prompt = (
        "You are a books assistant. Answer in concise French by default. "
        "Never provide long verbatim copyrighted excerpts. "
        "Use provided context first, then general literary knowledge when needed. "
        "If context is sparse, say what is uncertain instead of refusing immediately."
    )

    if violates_copyright_guardrails(user_message):
        return (
            "Je ne peux pas fournir d'extraits longs ou le texte intégral protégé. "
            "Je peux donner un résumé, les thèmes et une analyse."
        )

    categories = ", ".join([(x or "").strip() for x in (book_categories or []) if (x or "").strip()])
    context = (book_description or "").strip()
    if not context:
        context = "Contexte détaillé indisponible."

    # Keep prompts compact to fit small CPU-only models.
    # Ignore assistant history to avoid feedback loops when a bad answer is stored.
    history_lines: list[str] = []
    for item in (history or [])[-8:]:
        if item.get("role") == "assistant":
            continue
        role = "User"
        content = (item.get("content") or "").strip()
        if not content:
            continue
        history_lines.append(f"{role}: {content[:160]}")
    history_block = "\n".join(history_lines)

    prompt = (
        f"Book title: {book_title}\n"
        f"Book author: {book_author or 'Unknown'}\n"
        f"Book categories: {categories or 'Unknown'}\n"
        f"Book context: {context[:1200]}\n"
        f"Conversation memory:\n{history_block or 'No prior messages.'}\n"
        f"User question: {user_message}\n"
        "Instruction: answer in 4-8 concise sentences max. "
        "If exact details are missing, provide a useful answer based on known themes and mark uncertainty."
    )

    payload = {
        "model": settings.ollama_model,
        "prompt": f"{system_prompt}\n\n{prompt}",
        "stream": False,
        "options": {
            "num_ctx": 1024,
            "num_predict": 180,
            "temperature": 0.3,
        },
    }

    errors: list[str] = []
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        for base_url in _candidate_base_urls(settings.ollama_base_url):
            try:
                resp = await client.post(f"{base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                answer = _sanitize_text(str(data.get("response") or ""))
                if answer and not _looks_like_prompt_echo(answer, user_message):
                    return answer
            except Exception as exc:
                errors.append(f"{base_url}: {exc}")
                continue

    fallback = book_description.strip()
    if fallback:
        return _book_fallback(
            book_title=book_title,
            book_author=book_author,
            book_description=book_description,
            user_message=user_message,
        )

    if errors:
        return _book_fallback(
            book_title=book_title,
            book_author=book_author,
            book_description=book_description,
            user_message=user_message,
        )

    return (
        "Je n'ai pas encore assez de contexte sur ce livre. "
        "Essaie une question sur les thèmes, l'auteur ou relance la recherche."
    )
