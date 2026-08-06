"""LLM-assisted repairs.

Kept in one module, behind one interface, for a reason: everything here
*invents* content, and inventing content on a creator's behalf is the most
dangerous thing this tool does. A wrong dead-link report wastes a minute. A
hallucinated tag on 200 videos is the creator's channel now saying something
they didn't say.

So the rules here are stricter than anywhere else in the codebase:

  1. **Never invent facts.** Suggestions are derived from text the creator
     already wrote — their own title and description. The model rephrases and
     categorises; it does not add claims.
  2. **Everything is reviewable.** Output goes through `plan` like any other
     fix, shown as a diff, applied only on confirmation.
  3. **Degrade to nothing.** No API key, a timeout, a malformed response — the
     check reports the problem without a fix rather than guessing. A missing
     suggestion is fine; a bad one is not.
  4. **Bounded output.** Tags are length-capped and count-capped, stripped of
     punctuation, and de-duplicated against what is already there.

Provider is OpenAI-compatible, so Featherless, gemini-router, OpenRouter and
Ollama all work through the same path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from .config import CONFIG

TIMEOUT = httpx.Timeout(45.0, connect=10.0)
MAX_TAGS = 12
MAX_TAG_CHARS = 30
# YouTube rejects the whole update if the tag list exceeds 500 characters.
MAX_TOTAL_TAG_CHARS = 450


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class Provider:
    base_url: str
    api_key: str | None
    model: str
    name: str

    @classmethod
    def resolve(cls) -> "Provider":
        """Featherless if configured, else the local gemini-router."""
        if CONFIG.featherless_api_key:
            return cls(CONFIG.featherless_base_url,
                       CONFIG.featherless_api_key,
                       CONFIG.featherless_model, "featherless")
        return cls(CONFIG.gemini_router_base_url, None,
                   CONFIG.gemini_router_model, "gemini-router")


def _chat(prompt: str, *, system: str, max_tokens: int = 400) -> str:
    provider = Provider.resolve()
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    try:
        response = httpx.post(
            f"{provider.base_url}/chat/completions",
            headers=headers,
            json={
                "model": provider.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,   # low — this is categorisation, not prose
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"{provider.name}: {exc}") from exc

    if response.status_code != 200:
        raise LLMUnavailable(
            f"{provider.name} returned {response.status_code}: "
            f"{response.text[:200]}")

    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMUnavailable(f"{provider.name}: unreadable response") from exc


def _extract_json_array(text: str) -> list:
    """Pull the first JSON array out of a response.

    Models wrap JSON in prose and fences no matter how firmly you ask them not
    to. Rather than fight that, find the array.
    """
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        bare = re.search(r"\[.*\]", text, re.S)
        candidate = bare.group(0) if bare else None
    if candidate is None:
        raise LLMUnavailable("no JSON array in response")
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"malformed JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise LLMUnavailable("expected a JSON array")
    return parsed


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------

_TAG_SYSTEM = (
    "You generate YouTube tags. You are given a video's title and description, "
    "written by the creator. Return ONLY a JSON array of 8-12 lowercase tag "
    "strings.\n\n"
    "Rules:\n"
    "- Every tag must be supported by the title or description. Do not invent "
    "topics the creator did not mention.\n"
    "- Use terms a viewer would actually type into search.\n"
    "- Mix broad (the field) and specific (the exact subject).\n"
    "- No hashtags, no punctuation, no duplicates, max 30 characters each.\n"
    "- No clickbait and no unrelated popular terms.\n"
    "- Output the array and nothing else."
)


def suggest_tags(title: str, description: str,
                 existing: list[str] | None = None) -> list[str]:
    """Tags grounded in the creator's own words.

    Returns the *complete* proposed list (existing tags preserved and first),
    ready to hand to a Fix.
    """
    existing = list(existing or [])
    prompt = (f"Title: {title}\n\n"
              f"Description:\n{description[:1500] or '(empty)'}")

    raw = _chat(prompt, system=_TAG_SYSTEM, max_tokens=300)
    suggested = _extract_json_array(raw)

    clean: list[str] = []
    seen = {t.lower().strip() for t in existing}
    for item in suggested:
        if not isinstance(item, str):
            continue
        tag = re.sub(r"[^\w\s-]", "", item).strip().lower()
        tag = re.sub(r"\s+", " ", tag)
        if not tag or len(tag) > MAX_TAG_CHARS or tag in seen:
            continue
        seen.add(tag)
        clean.append(tag)
        if len(clean) >= MAX_TAGS:
            break

    if not clean:
        raise LLMUnavailable("no usable tags in response")

    merged = existing + clean
    # YouTube rejects the entire update if the tag list is too long, which
    # would fail the write for reasons that look unrelated to tags.
    total = 0
    capped: list[str] = []
    for tag in merged:
        total += len(tag) + 1
        if total > MAX_TOTAL_TAG_CHARS:
            break
        capped.append(tag)
    return capped


# ---------------------------------------------------------------------------
# chapters
# ---------------------------------------------------------------------------

_CHAPTER_SYSTEM = (
    "You write YouTube chapter lists from a transcript with timestamps.\n\n"
    "Rules:\n"
    "- Return ONLY a JSON array of objects: "
    '[{"t": "00:00", "label": "Intro"}, ...]\n'
    "- The FIRST chapter must be exactly 00:00. YouTube renders nothing "
    "otherwise.\n"
    "- At least 3 chapters, at most 12.\n"
    "- Consecutive chapters must be at least 10 seconds apart.\n"
    "- Labels: 2-5 words, describing what is actually said in that section. "
    "Never invent sections not present in the transcript.\n"
    "- Timestamps must be in ascending order and within the video's length."
)


def suggest_chapters(title: str, transcript: str,
                     duration_seconds: int) -> list[tuple[str, str]]:
    """Chapters from a timestamped transcript.

    The transcript is required and must carry timestamps — chapters are claims
    about *when* things happen, and there is no honest way to guess that from
    a title. Callers that cannot obtain one must not call this.
    """
    if not transcript.strip():
        raise LLMUnavailable("no transcript available")

    prompt = (f"Video title: {title}\n"
              f"Video length: {_fmt(duration_seconds)}\n\n"
              f"Transcript:\n{transcript[:12000]}")

    raw = _chat(prompt, system=_CHAPTER_SYSTEM, max_tokens=700)
    parsed = _extract_json_array(raw)

    chapters: list[tuple[int, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        stamp, label = item.get("t"), item.get("label")
        if not isinstance(stamp, str) or not isinstance(label, str):
            continue
        seconds = _to_seconds(stamp)
        if seconds is None or (duration_seconds and seconds >= duration_seconds):
            continue
        chapters.append((seconds, label.strip()[:60]))

    chapters.sort(key=lambda c: c[0])

    # Enforce YouTube's rules ourselves rather than trusting the model. A
    # chapter list that violates them renders as no chapters at all, which
    # would look like the fix silently did nothing.
    valid: list[tuple[int, str]] = []
    for seconds, label in chapters:
        if not label:
            continue
        if valid and seconds - valid[-1][0] < 10:
            continue
        valid.append((seconds, label))

    if len(valid) < 3:
        raise LLMUnavailable(
            f"only {len(valid)} valid chapters after filtering (need 3+)")
    if valid[0][0] != 0:
        valid[0] = (0, valid[0][1])

    return [(_fmt(s), label) for s, label in valid[:12]]


def render_chapters(chapters: list[tuple[str, str]]) -> str:
    return "\n".join(f"{stamp} {label}" for stamp, label in chapters)


# ---------------------------------------------------------------------------


def _fmt(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _to_seconds(stamp: str) -> int | None:
    parts = stamp.strip().split(":")
    if not all(p.strip().isdigit() for p in parts):
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    if len(values) == 2:
        return values[0] * 60 + values[1]
    if len(values) == 3:
        return values[0] * 3600 + values[1] * 60 + values[2]
    return None


def available() -> bool:
    return bool(CONFIG.featherless_api_key)
