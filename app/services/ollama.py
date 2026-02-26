from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app.core.config import settings


_COPYRIGHT_GUARD_PATTERNS = [
    re.compile(r"\b(full\s+chapter|complete\s+book|verbatim|mot\s+pour\s+mot)\b", re.IGNORECASE),
    re.compile(r"\b(entier|int[eé]gral|texte\s+complet)\b", re.IGNORECASE),
]


def violates_copyright_guardrails(user_message: str) -> bool:
    return any(p.search(user_message) for p in _COPYRIGHT_GUARD_PATTERNS)


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
    history_lines: list[str] = []
    for item in (history or [])[-6:]:
        role = "Assistant" if item.get("role") == "assistant" else "User"
        content = (item.get("content") or "").strip()
        if not content:
            continue
        history_lines.append(f"{role}: {content[:220]}")
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
                answer = str(data.get("response") or "").strip()
                if answer:
                    return answer
            except Exception as exc:
                errors.append(f"{base_url}: {exc}")
                continue

    fallback = book_description.strip()
    if fallback:
        return f"Résumé/context: {fallback[:700]}"

    if errors:
        return (
            "Le modèle local Ollama est indisponible pour le moment. "
            "Vérifie OLLAMA_BASE_URL et que le modèle "
            f"'{settings.ollama_model}' est installé et lancé."
        )

    return (
        "Je n'ai pas encore assez de contexte sur ce livre. "
        "Essaie une question sur les thèmes, l'auteur ou relance la recherche."
    )
