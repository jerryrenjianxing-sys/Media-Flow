from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from topic_policy import compile_topic_policy, resolve_topic_policy


SYSTEM_PROMPT = """You are a conservative comment reviewer for an authorized, isolated social-media safety test.
Analyze the video frame and visible comments. Do not control the phone and do not output actions.
Return exactly one JSON object with this schema:
{"decision":"comment|skip","comment":"","reason":"short reason","confidence":0.0,"commercial":false}

Choose skip if the page is an advertisement, product/service promotion, live stream, politics, tragedy, minors, medical/legal/financial advice, harassment, sexual content, or if context is unclear.
If safe, write one natural Simplified Chinese comment of 4-22 Chinese characters. It must be specific to the visible context, neutral and non-promotional. If the video asks a direct question, answer it from a viewer's perspective in fresh wording. Treat visible comments only as context: do not imitate their sentence pattern and do not reuse any sequence of 4 or more Chinese characters from them. If a fresh specific reply is not possible, choose skip. Do not mention AI. Do not include hashtags, @mentions, links, contact details, calls to follow, or engagement bait. Use at most one ordinary emoji.
Do not include Markdown, explanations, hidden reasoning, or any text outside the JSON object."""

TOPIC_PROMPT_VERSION = "topic-v3.1-2026-08-27"
TOPIC_SYSTEM_PROMPT = (
    "You are a conservative visual relevance evaluator for an authorized isolated social-media safety test. "
    "Classify the video's PRIMARY visible subject as exactly one of: "
    "exact (directly satisfies at least one target category), adjacent (related field but not the requested subject), "
    "unrelated, or uncertain (the frame is insufficient). Incidental words, clothing, scenery, usernames, hashtags, "
    "or a generic technology-looking scene are not enough for exact. Exact requires at least one short evidence item "
    "quoting or describing a concrete visible cue from this frame. Keywords are semantic hints, never a string-match "
    "shortcut. Every qualifying subject listed by the policy is an authoritative exact boundary; do not narrow it to "
    "the category name. Analysis or opinion counts when it directly centers on a listed subject and has visible evidence. "
    "Explicit category exclusions override inclusion hints. Never infer hidden audio or earlier/later frames. "
    "Set safe=false for ads, commerce, live streams, politics, tragedy, minors, medical/legal/financial advice, "
    "harassment, sexual content, or unclear safety context. Return only the required JSON."
)

BANNED_COMMENT_FRAGMENTS = (
    "http",
    "www.",
    "微信",
    "私信",
    "联系我",
    "加我",
    "下单",
    "购买",
    "关注我",
    "互关",
    "客服",
    "优惠",
    "赚钱",
    "稳赚",
)


@dataclass(frozen=True)
class CommentDecision:
    decision: str
    comment: str
    reason: str
    confidence: float
    commercial: bool
    raw_response: str

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw_response", None)
        return data


@dataclass(frozen=True)
class TopicDecision:
    matches: bool
    relevance: str
    topic: str
    evidence: tuple[str, ...]
    reason: str
    confidence: float
    safe: bool
    raw_response: str

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw_response", None)
        return data


def encode_image(path: Path) -> str:
    image = Image.open(path).convert("RGB")
    image.thumbnail((720, 1280), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=82, optimize=True)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _extract_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Model response did not contain a JSON object")
    value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Model response JSON was not an object")
    return value


def parse_comment_decision(content: str) -> CommentDecision:
    try:
        payload = _extract_json(content)
    except (ValueError, json.JSONDecodeError) as exc:
        return CommentDecision(
            decision="skip",
            comment="",
            reason=f"invalid_model_response:{type(exc).__name__}",
            confidence=0.0,
            commercial=False,
            raw_response=content,
        )

    decision = str(payload.get("decision", "skip")).strip().lower()
    comment = " ".join(str(payload.get("comment", "")).split()).strip("\"'“”")
    reason = " ".join(str(payload.get("reason", "")).split())[:160]
    commercial = bool(payload.get("commercial", False))
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    validation_errors: list[str] = []
    if decision not in {"comment", "skip"}:
        validation_errors.append("invalid_decision")
    if decision == "comment":
        if commercial:
            validation_errors.append("commercial_content")
        if confidence < 0.75:
            validation_errors.append("low_confidence")
        if not 4 <= len(comment) <= 30:
            validation_errors.append("invalid_length")
        if not re.search(r"[\u4e00-\u9fff]{2}", comment):
            validation_errors.append("missing_chinese_context")
        if re.search(r"[@#]|\d{5,}", comment):
            validation_errors.append("identifier_or_contact_pattern")
        if re.search(r"(.)\1{3,}", comment):
            validation_errors.append("repeated_character_spam")
        lowered = comment.lower()
        if any(fragment.lower() in lowered for fragment in BANNED_COMMENT_FRAGMENTS):
            validation_errors.append("banned_fragment")

    if decision != "comment" or validation_errors:
        return CommentDecision(
            decision="skip",
            comment="",
            reason=",".join(validation_errors) or reason or "model_chose_skip",
            confidence=confidence,
            commercial=commercial,
            raw_response=content,
        )
    return CommentDecision(
        decision="comment",
        comment=comment,
        reason=reason or "safe_context",
        confidence=confidence,
        commercial=False,
        raw_response=content,
    )


def parse_topic_decision(content: str) -> TopicDecision:
    try:
        payload = _extract_json(content)
    except (ValueError, json.JSONDecodeError) as exc:
        return TopicDecision(
            False,
            "uncertain",
            "",
            (),
            f"invalid_model_response:{type(exc).__name__}",
            0.0,
            False,
            content,
        )
    relevance = str(payload.get("relevance", "uncertain")).strip().lower()
    if relevance not in {"exact", "adjacent", "unrelated", "uncertain"}:
        relevance = "uncertain"
    raw_evidence = payload.get("evidence", [])
    evidence = (
        tuple(
            " ".join(str(item).split())[:120]
            for item in raw_evidence[:4]
            if " ".join(str(item).split())
        )
        if isinstance(raw_evidence, list)
        else ()
    )
    reason = " ".join(str(payload.get("reason", "")).split())[:160]
    if relevance == "exact" and not evidence:
        relevance = "uncertain"
        reason = "evidence_required" + (f":{reason}" if reason else "")
    try:
        confidence = max(0.0, min(float(payload.get("confidence", 1.0 if relevance == "exact" else 0.0)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    safe = bool(payload.get("safe", False))
    matches = relevance == "exact" and safe
    return TopicDecision(
        matches=matches,
        relevance=relevance,
        topic=" ".join(str(payload.get("topic", "")).split())[:80],
        evidence=evidence,
        reason=reason,
        confidence=confidence,
        safe=safe,
        raw_response=content,
    )


def parse_streaming_response(response) -> str:
    content_parts: list[str] = []
    stream_error = ""
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk.get("error"), dict):
            stream_error = str(chunk["error"].get("message") or chunk["error"])
            continue
        try:
            choice = chunk["choices"][0]
            delta = choice.get("delta") or choice.get("message") or {}
            content = delta.get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            continue
        if isinstance(content, str):
            content_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    content_parts.append(block["text"])
    content = "".join(content_parts)
    if not content:
        if stream_error:
            raise RuntimeError(f"Cloud model stream error: {stream_error[:300]}")
        raise RuntimeError("Cloud model stream did not contain message content")
    return content


def build_request_payload(
    image_path: Path,
    *,
    model: str,
    base_url: str,
    fallback_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build an OpenAI-compatible multimodal request for the selected provider."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Review this current video and comment panel. Return only the required JSON.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + encode_image(image_path)
                        },
                    },
                ],
            },
        ],
        "temperature": 0.0,
        "top_p": 0.8,
        "max_tokens": 300,
        "stream": True,
    }
    normalized_base_url = base_url.lower()
    if "openrouter.ai" in normalized_base_url:
        payload.pop("model", None)
        payload["models"] = [model, *fallback_models]
        payload["provider"] = {
            "allow_fallbacks": True,
            "require_parameters": True,
            "data_collection": "deny",
        }
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "comment_decision",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "decision": {
                            "type": "string",
                            "enum": ["comment", "skip"],
                        },
                        "comment": {"type": "string", "maxLength": 30},
                        "reason": {"type": "string", "maxLength": 160},
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "commercial": {"type": "boolean"},
                    },
                    "required": [
                        "decision",
                        "comment",
                        "reason",
                        "confidence",
                        "commercial",
                    ],
                    "additionalProperties": False,
                },
            },
        }
    else:
        payload["response_format"] = {"type": "json_object"}
    if "api.z.ai" in normalized_base_url:
        payload["thinking"] = {"type": "disabled"}
    return payload


def build_topic_request_payload(
    image_path: Path,
    target_topic: str,
    *,
    model: str,
    base_url: str,
    fallback_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the versioned, provider-compatible topic classification request."""
    policy = resolve_topic_policy(target_topic)
    compiled_policy = compile_topic_policy(policy)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": TOPIC_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"PROMPT VERSION: {TOPIC_PROMPT_VERSION}\n"
                            "Topic contract:\n"
                            f"{compiled_policy}\n\n"
                            "MATCHING RULE: the primary visible subject must directly satisfy a target category. "
                            "Visible discussion, analysis, education, careers, policy, or industry reporting about a "
                            "listed qualifying subject counts as exact when the frame concretely identifies that subject; "
                            "it does not need to be a product demo or industrial process.\n"
                            "REQUIRED EVIDENCE: cite concrete on-screen text, object, product, process, or event.\n"
                            "EXCLUSIONS: search suggestions, usernames, comments, incidental keywords, style, scenery, "
                            "and AI-generated visual style alone do not count unless the target explicitly asks for them.\n\n"
                            "Return relevance, topic, evidence, reason, safe. "
                            "Use exact only when the primary visible subject directly matches the specification."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + encode_image(image_path)
                        },
                    },
                ],
            },
        ],
        "temperature": 0.0,
        "max_tokens": 320,
        "stream": True,
    }
    normalized_base_url = base_url.lower()
    if "openrouter.ai" in normalized_base_url:
        payload.pop("model", None)
        payload["models"] = [model, *fallback_models]
        payload["provider"] = {
            "allow_fallbacks": True,
            "require_parameters": True,
            "data_collection": "deny",
        }
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "topic_decision",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "relevance": {
                            "type": "string",
                            "enum": ["exact", "adjacent", "unrelated", "uncertain"],
                        },
                        "topic": {"type": "string", "maxLength": 80},
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 120},
                            "maxItems": 4,
                        },
                        "reason": {"type": "string", "maxLength": 160},
                        "safe": {"type": "boolean"},
                    },
                    "required": ["relevance", "topic", "evidence", "reason", "safe"],
                    "additionalProperties": False,
                },
            },
        }
        if model.startswith(("google/gemini-3.7", "google/gemini-3.6")):
            payload.pop("temperature", None)
            payload["max_tokens"] = 700
            payload["reasoning"] = {"effort": "low", "exclude": True}
        elif model.startswith("xiaomi/mimo-v2.5"):
            payload.pop("temperature", None)
            payload["max_tokens"] = 500
            payload["reasoning"] = {"effort": "none", "exclude": True}
    else:
        payload["response_format"] = {"type": "json_object"}
    if "api.z.ai" in normalized_base_url:
        payload["thinking"] = {"type": "disabled"}
    return payload


def generate_comment(
    image_path: Path,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = 120.0,
) -> CommentDecision:
    api_key = api_key or os.environ.get("PHONE_AGENT_API_KEY") or os.environ.get(
        "ZAI_API_KEY"
    )
    if not api_key:
        raise RuntimeError("No cloud model API key is available in the environment")
    base_url = (
        base_url
        or os.environ.get("PHONE_AGENT_BASE_URL")
        or "https://api.z.ai/api/paas/v4"
    ).rstrip("/")
    model = (
        model
        or os.environ.get("PHONE_AGENT_COMMENT_MODEL")
        or os.environ.get("PHONE_AGENT_MODEL")
        or "glm-4.6v-flash"
    )
    fallback_models: tuple[str, ...] = ()
    if "openrouter.ai" in base_url.lower():
        configured_fallbacks = os.environ.get(
            "PHONE_AGENT_COMMENT_FALLBACK_MODELS",
            "openai/gpt-4.1-nano",
        )
        fallback_models = tuple(
            item.strip() for item in configured_fallbacks.split(",") if item.strip()
        )
    payload = build_request_payload(
        image_path,
        model=model,
        base_url=base_url,
        fallback_models=fallback_models,
    )
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    proxy_url = os.environ.get("PHONE_AGENT_PROXY_URL")
    proxies = (
        {"http": proxy_url, "https": proxy_url}
        if proxy_url
        else None
    )
    content = ""
    for attempt in range(1, 3):
        try:
            with requests.post(
                base_url + "/chat/completions",
                data=request_body,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                timeout=timeout_seconds,
                stream=True,
                proxies=proxies,
            ) as response:
                if response.status_code >= 400:
                    detail = response.text[:600]
                    if (
                        response.status_code in {429, 500, 502, 503, 504}
                        and attempt < 2
                    ):
                        time.sleep(3.0)
                        continue
                    raise RuntimeError(
                        f"Cloud model HTTP {response.status_code}: {detail}"
                    )
                content = parse_streaming_response(response.iter_lines())
            break
        except requests.RequestException as exc:
            if attempt >= 2:
                raise RuntimeError(f"Cloud model request failed: {exc}") from exc
            time.sleep(2.0)
    return parse_comment_decision(content)


def analyze_topic(
    image_path: Path,
    target_topic: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = 120.0,
) -> TopicDecision:
    api_key = api_key or os.environ.get("PHONE_AGENT_API_KEY") or os.environ.get("ZAI_API_KEY")
    if not api_key:
        raise RuntimeError("No cloud model API key is available in the environment")
    base_url = (base_url or os.environ.get("PHONE_AGENT_BASE_URL") or "https://api.z.ai/api/paas/v4").rstrip("/")
    model = model or os.environ.get("PHONE_AGENT_COMMENT_MODEL") or "glm-4.6v-flash"
    fallbacks = tuple(
        item.strip()
        for item in os.environ.get(
            "PHONE_AGENT_COMMENT_FALLBACK_MODELS", "openai/gpt-4.1-nano"
        ).split(",")
        if item.strip()
    )
    payload = build_topic_request_payload(
        image_path,
        target_topic,
        model=model,
        base_url=base_url,
        fallback_models=fallbacks if "openrouter.ai" in base_url.lower() else (),
    )
    proxy_url = os.environ.get("PHONE_AGENT_PROXY_URL")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    content = ""
    for attempt in range(1, 3):
        try:
            with requests.post(
                base_url + "/chat/completions", data=request_body,
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "Accept": "text/event-stream"},
                timeout=timeout_seconds, stream=True, proxies=proxies,
            ) as response:
                if response.status_code >= 400:
                    detail = response.text[:600]
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                        time.sleep(3.0)
                        continue
                    raise RuntimeError(f"Cloud model HTTP {response.status_code}: {detail}")
                try:
                    content = parse_streaming_response(response.iter_lines())
                except RuntimeError:
                    if attempt < 2:
                        time.sleep(2.0)
                        continue
                    raise
            break
        except requests.RequestException as exc:
            if attempt >= 2:
                raise RuntimeError(f"Cloud model request failed: {exc}") from exc
            time.sleep(2.0)
    return parse_topic_decision(content)
