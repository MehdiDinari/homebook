from __future__ import annotations

import logging
import re
from typing import Any

import httpx

OPENLIB_SEARCH_URL = "https://openlibrary.org/search.json"
OPENLIB_WORK_URL = "https://openlibrary.org/works/{work_id}.json"
OPENLIB_COVER_BY_ID = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

logger = logging.getLogger(__name__)


def _extract_description(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        raw = value.get("value")
        if isinstance(raw, str):
            return raw.strip()
    return ""


def _extract_doc_description(doc: dict[str, Any]) -> str:
    first_sentence = doc.get("first_sentence")
    if isinstance(first_sentence, str):
        return first_sentence.strip()
    if isinstance(first_sentence, dict):
        raw = first_sentence.get("value")
        if isinstance(raw, str):
            return raw.strip()
    if isinstance(first_sentence, list) and first_sentence:
        first = first_sentence[0]
        if isinstance(first, str):
            return first.strip()
        if isinstance(first, dict):
            raw = first.get("value")
            if isinstance(raw, str):
                return raw.strip()
    return ""


def _iso_language(code: str | None) -> str:
    if not code:
        return "fr"
    code = code.lower().strip()
    if len(code) == 2:
        return code
    if len(code) >= 3:
        return code[:2]
    return "fr"


def _extract_year(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"\b(\d{4})\b", value)
        if m:
            return int(m.group(1))
    return None


def _tags(title: str, categories: list[str]) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]+", f"{title} {' '.join(categories)}".lower())
    stop = {"the", "and", "for", "avec", "dans", "les", "des", "une", "pour"}
    out: list[str] = []
    seen = set()
    for w in words:
        if w in stop or len(w) <= 2:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:30]


def _dedupe_books(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_work_id: set[str] = set()
    by_key: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        work_id = str(row.get("work_id") or "").strip()
        if not work_id or work_id in by_work_id:
            continue
        title = str(row.get("title") or "").strip().lower()
        author = str(row.get("author") or "").strip().lower()
        language = str(row.get("language") or "").strip().lower()
        key = (title, author, language)
        if title and author and key in by_key:
            continue
        by_work_id.add(work_id)
        if title and author:
            by_key.add(key)
        out.append(row)
    return out


async def search_books(
    query: str,
    *,
    limit: int = 20,
    page: int = 1,
    language: str | None = None,
    category: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    terms = [query.strip()]
    if category:
        terms.append(f"subject:{category.strip()}")
    if tag:
        terms.append(f"subject:{tag.strip()}")
    q = " ".join([t for t in terms if t])
    params = {"q": q, "limit": min(max(limit, 1), 100), "page": max(int(page), 1)}
    if language:
        params["language"] = language

    headers = {"User-Agent": "HomeBook/1.0", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(OPENLIB_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception:
        logger.exception("OpenLibrary search failed")
        return []

    docs = payload.get("docs") or []
    out: list[dict[str, Any]] = []

    for doc in docs:
        key = doc.get("key") or ""
        if not key:
            continue
        work_id = key.split("/")[-1]
        title = str(doc.get("title") or "").strip()
        authors = doc.get("author_name") or []
        author = ", ".join([str(a) for a in authors if a])

        desc = _extract_doc_description(doc)
        cover_id = doc.get("cover_i")
        cover_url = OPENLIB_COVER_BY_ID.format(cover_id=cover_id) if cover_id else ""

        categories = [str(s) for s in (doc.get("subject") or []) if isinstance(s, str)][:20]
        lang_codes = doc.get("language") or []
        lang = _iso_language(lang_codes[0] if lang_codes else None)
        year = _extract_year(doc.get("first_publish_year"))

        out.append(
            {
                "work_id": work_id,
                "title": title,
                "author": author,
                "description": desc,
                "cover_url": cover_url,
                "language": lang,
                "categories": categories,
                "tags": _tags(title, categories),
                "year": year,
                "rating": None,
                "ratings_count": 0,
                "web_reader_link": None,
                "source_payload": doc,
            }
        )

    return _dedupe_books(out)


async def get_book(work_id: str) -> dict[str, Any] | None:
    url = OPENLIB_WORK_URL.format(work_id=work_id)
    headers = {"User-Agent": "HomeBook/1.0", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.exception("OpenLibrary get_book failed")
        return None

    title = str(data.get("title") or "").strip()
    categories = [str(s) for s in (data.get("subjects") or []) if isinstance(s, str)][:30]
    description = _extract_description(data.get("description"))

    cover_url = ""
    covers = data.get("covers") or []
    if covers:
        first = covers[0]
        if isinstance(first, int):
            cover_url = OPENLIB_COVER_BY_ID.format(cover_id=first)

    authors: list[str] = []
    for item in data.get("authors") or []:
        auth = item.get("author") if isinstance(item, dict) else None
        key = auth.get("key") if isinstance(auth, dict) else None
        if not key:
            continue
        author_url = f"https://openlibrary.org{key}.json"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                a = await client.get(author_url, headers=headers)
                if a.status_code == 200:
                    authors.append(str(a.json().get("name") or "").strip())
        except Exception:
            continue

    author = ", ".join([x for x in authors if x])
    language = "fr"
    languages = data.get("languages") or []
    if languages:
        first = languages[0]
        if isinstance(first, dict):
            language = _iso_language((first.get("key") or "").split("/")[-1])

    year = _extract_year(data.get("first_publish_date") or data.get("created", {}).get("value"))

    return {
        "work_id": work_id,
        "title": title,
        "author": author,
        "description": description,
        "cover_url": cover_url,
        "language": language,
        "categories": categories,
        "tags": _tags(title, categories),
        "year": year,
        "rating": None,
        "ratings_count": 0,
        "web_reader_link": None,
        "source_payload": data,
    }
