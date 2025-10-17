import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - SDK import problems
    OpenAI = None  # type: ignore


_ENV_LOCATIONS = [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
]


def _load_env_value(name: str, *, aliases: Iterable[str] | None = None) -> Optional[str]:
    candidates = [name, *(aliases or [])]
    for env_path in _ENV_LOCATIONS:
        if not env_path.exists():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                for variant in candidates:
                    if not line.startswith(variant):
                        continue
                    _, _, value = line.partition("=")
                    value = value.strip().strip('"').strip("'")
                    os.environ[variant] = value
                    if value:
                        return value
        except OSError:
            continue

    for variant in candidates:
        direct = os.getenv(variant)
        if direct:
            return direct.strip()
    return None


def _first_present(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


_API_KEY = _load_env_value("OPENAI_API_KEY")
_PROJECT_ID = _first_present(
    _load_env_value("OPENAI_PROJECT"),
    _load_env_value("OPENAI_PROJECT_ID"),
)
DEFAULT_MODEL = _first_present(
    _load_env_value("OPENAI_MODEL"),
    "gpt-4.1-mini",
)

_OFFLINE = not _API_KEY or OpenAI is None

if not _OFFLINE:
    client_kwargs = {"api_key": _API_KEY}
    if _PROJECT_ID:
        client_kwargs["project"] = _PROJECT_ID
    try:
        _CLIENT = OpenAI(**client_kwargs)
    except Exception:  # pragma: no cover - client bootstrap failure
        _CLIENT = None
        _OFFLINE = True
else:
    _CLIENT = None


MAX_SENTENCES = 2
MAX_SENTENCE_CHARS = 160


def _enforce_simple_sentences(text: str, max_sentences: int = MAX_SENTENCES) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return "I am here. I listen."

    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    simple: List[str] = []
    for part in parts:
        fragment = part.strip().replace(";", ",")
        if not fragment:
            continue
        if len(fragment) > MAX_SENTENCE_CHARS:
            fragment = fragment[: MAX_SENTENCE_CHARS - 3].rstrip() + "..."
        if fragment[-1:] not in ".?!":
            fragment += "."
        simple.append(fragment)
        if len(simple) >= max_sentences:
            break

    if not simple:
        return "I am here. I listen."

    return " ".join(simple)


def _build_messages(
    user_text: str,
    screen_text: Optional[str],
    history: Optional[Sequence[dict]] = None,
    *,
    allow_elaboration: bool = False,
    memory_summary: Optional[str] = None,
) -> List[dict]:
    base_prompt = (
        "You are Nova, an AI roommate who is casual but intelligent. "
        "Speak naturally, like a friendly human roommate. "
        "Keep every reply short—ideally one or two crisp sentences unless the user explicitly asks for detail. "
        "Focus on the most recent user message and only mention older context when it clearly strengthens the answer. "
        "If the user asks for help, stay concise and witty when appropriate."
    )
    if allow_elaboration:
        base_prompt += " The user asked you to 'dive deeper', so provide a thorough answer while staying clear."
    if memory_summary:
        base_prompt += " Background memory (use only if relevant): " + memory_summary
    system_prompt = {"role": "system", "content": base_prompt}
    messages: List[dict] = [system_prompt]
    if history:
        for item in history:
            role = item.get("role") if isinstance(item, dict) else None
            content = item.get("content") if isinstance(item, dict) else None
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            messages.append({"role": role, "content": content.strip()})
    messages.append({"role": "user", "content": user_text})
    if screen_text:
        messages.append({"role": "system", "content": f"Screen context (for reference only): {screen_text[:320]}"})
    return messages


def _messages_to_prompt(messages: List[dict]) -> str:
    rendered: List[str] = []
    for message in messages:
        role = message.get("role", "user").capitalize()
        content = message.get("content", "")
        rendered.append(f"{role}: {content}")
    rendered.append("Assistant:")
    return "\n".join(rendered)


def _fallback_summary(history: Sequence[dict]) -> Optional[str]:
    if not history:
        return None
    snippets: List[str] = []
    for entry in history[-6:]:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        prefix = "You" if role == "user" else "Nova"
        snippet = content.strip()
        if len(snippet) > 140:
            snippet = snippet[:137].rstrip() + "…"
        snippets.append(f"- {prefix}: {snippet}")
    if not snippets:
        return None
    return "Recent highlights:\n" + "\n".join(snippets)


def summarize_history(history: Sequence[dict]) -> Optional[str]:
    if not history:
        return None

    messages = _build_messages(
        "Summarize our recent conversation into at most three concise bullet points focusing on decisions, tasks, or important facts.",
        None,
        history,
    )

    if _OFFLINE or _CLIENT is None:
        return _fallback_summary(history)

    try:
        prompt = _messages_to_prompt(messages)
        response = _CLIENT.responses.create(model=DEFAULT_MODEL, input=prompt)
        text = getattr(response, "output_text", None)
        if text:
            return text.strip()

        output = getattr(response, "output", None)
        if output:
            chunks = []
            for item in output:
                content_items = getattr(item, "content", None)
                if not content_items:
                    continue
                for block in content_items:
                    block_text = getattr(block, "text", None) or getattr(block, "value", None)
                    if block_text:
                        chunks.append(str(block_text))
            if chunks:
                return "".join(chunks).strip()
    except Exception:
        pass

    return _fallback_summary(history)


def generate_reply(
    user_text: str,
    screen_text: Optional[str] = None,
    history: Optional[Sequence[dict]] = None,
    *,
    memory_summary: Optional[str] = None,
) -> str:
    """Get a reply from OpenAI when available; otherwise return a friendly local stub.

    This lets the app run in demo/offline mode without an API key.
    """
    allow_elaboration = "dive deeper" in user_text.lower()
    if history:
        filtered_history: List[dict] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if memory_summary and content.strip() == memory_summary.strip():
                continue
            filtered_history.append({"role": role, "content": content.strip()})
        recent_history: Optional[Sequence[dict]] = filtered_history[-8:]
    else:
        recent_history = None

    messages = _build_messages(
        user_text,
        screen_text,
        recent_history,
        allow_elaboration=allow_elaboration,
        memory_summary=memory_summary,
    )

    if _OFFLINE or _CLIENT is None:
        if screen_text:
            offline = f"I am offline right now. You said: {user_text}, and I saw your screen."
        else:
            offline = f"I am offline right now. You said: {user_text}, and I did not see your screen."
        return _enforce_simple_sentences(offline)

    try:
        prompt = _messages_to_prompt(messages)
        response = _CLIENT.responses.create(model=DEFAULT_MODEL, input=prompt)
        text = getattr(response, "output_text", None)
        if text:
            return _enforce_simple_sentences(text.strip())

        output = getattr(response, "output", None)
        if output:
            chunks = []
            for item in output:
                content_items = getattr(item, "content", None)
                if not content_items:
                    continue
                for block in content_items:
                    block_text = getattr(block, "text", None) or getattr(block, "value", None)
                    if block_text:
                        chunks.append(str(block_text))
            if chunks:
                return _enforce_simple_sentences("".join(chunks).strip())

        # Fallback to legacy completions if the Responses API call shape isn't supported yet
        legacy = _CLIENT.chat.completions.create(model=DEFAULT_MODEL, messages=messages)
        return _enforce_simple_sentences(legacy.choices[0].message.content.strip())
    except Exception as exc:
        # Network, auth, or model errors -> degrade gracefully
        message = str(exc)
        if "Incorrect API key" in message or "invalid_api_key" in message:
            warning = (
                "OpenAI rejected the API key. Make a new key and update your settings."
            )
            return _enforce_simple_sentences(warning)
        if "project" in message and "missing" in message and not _PROJECT_ID:
            warning = (
                "OpenAI needs a project ID with the key. Set OPENAI_PROJECT or OPENAI_PROJECT_ID."
            )
            return _enforce_simple_sentences(warning)
        fallback = f"I cannot reach the AI service. You said: {user_text}."
        return _enforce_simple_sentences(fallback)

    # Ensure API success responses pass through the simplifier
    return _enforce_simple_sentences("I am not sure what to say.")