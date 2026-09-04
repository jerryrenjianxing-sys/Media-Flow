from __future__ import annotations

import json
import os
import re
import time
import gzip
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from comment_ai import encode_image, parse_streaming_response
from model_runtime_config import OPENROUTER_PRIMARY_MODEL
from task_store import IncidentRecord


INCIDENT_ANALYSIS_PROMPT_VERSION = "incident-advisory-v1-2026-08-27"
INCIDENT_ANALYSIS_SYSTEM_PROMPT = """You are a read-only incident analyst for an authorized isolated Android automation test.
You may analyze the supplied screenshot and error metadata, but you do not control the phone.
Return exactly one JSON object with this schema:
{"classification":"navigation_drift|empty_content|overlay|external_navigation|stale_state|network|unknown","summary":"short Chinese summary","suggested_rule":"short Chinese rule proposal","confidence":0.0,"risk":"low|medium|high"}
Do not output screen coordinates, tap sequences, shell commands, code, credentials, or instructions to replay an action whose outcome is unknown.
Suggestions must be conservative and describe only a possible future fixed-executor guard or recognition rule. Do not include Markdown or text outside JSON."""

CLASSIFICATIONS = {
    "navigation_drift",
    "empty_content",
    "overlay",
    "external_navigation",
    "stale_state",
    "network",
    "unknown",
}
RISKS = {"low", "medium", "high"}
UI_CONTROL_MARKERS = (
    "首页",
    "推荐",
    "消息",
    "互动消息",
    "赞与收藏",
    "收到的评论",
    "收到的弹幕",
    "主页访客",
    "登录",
    "验证",
)


@dataclass(frozen=True)
class IncidentAdvice:
    classification: str
    summary: str
    suggested_rule: str
    confidence: float
    risk: str
    auto_applicable: bool = False
    prompt_version: str = INCIDENT_ANALYSIS_PROMPT_VERSION

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def parse_incident_advice(content: str) -> IncidentAdvice:
    payload = _extract_json(content)
    classification = str(payload.get("classification", "unknown")).strip().lower()
    risk = str(payload.get("risk", "high")).strip().lower()
    if classification not in CLASSIFICATIONS:
        classification = "unknown"
    if risk not in RISKS:
        risk = "high"
    summary = " ".join(str(payload.get("summary", "")).split())[:180]
    suggested_rule = " ".join(str(payload.get("suggested_rule", "")).split())[:300]
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    coordinate_pattern = re.compile(
        r"(?:\bx\s*[:=]\s*\d+|\by\s*[:=]\s*\d+|\(\s*\d+\s*,\s*\d+\s*\)|坐标\s*\d+)",
        re.IGNORECASE,
    )
    if coordinate_pattern.search(suggested_rule):
        suggested_rule = "建议人工复核截图并补充语义识别规则；不采纳模型坐标。"
        risk = "high"
        classification = "unknown"
    return IncidentAdvice(
        classification=classification,
        summary=summary or "模型未给出有效摘要",
        suggested_rule=suggested_rule or "保留证据并由人工复核是否需要新增固定规则。",
        confidence=confidence,
        risk=risk,
    )


def build_incident_analysis_payload(
    incident: IncidentRecord,
    *,
    model: str,
    base_url: str,
    fallback_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    metadata = {
        "stage": incident.stage,
        "error_type": incident.error_type,
        "error_message": incident.error_message,
        "outcome": incident.outcome,
        "recovery_action": incident.recovery_action,
        "context": incident.context,
        "ui_semantics": _bounded_ui_semantics(incident.ui_tree_path),
    }
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "INCIDENT METADATA:\n" + json.dumps(metadata, ensure_ascii=False),
        }
    ]
    if incident.screenshot_path:
        image_path = Path(incident.screenshot_path)
        if image_path.is_file():
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + encode_image(image_path)
                    },
                }
            )
    payload: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": INCIDENT_ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "max_tokens": 500,
        "stream": True,
    }
    if "openrouter.ai" in base_url.lower():
        json_object_only = model.startswith("z-ai/glm-5.3-flash")
        payload["models"] = [model, *fallback_models]
        payload["provider"] = {
            "allow_fallbacks": bool(fallback_models),
            "require_parameters": not json_object_only,
            "data_collection": "deny",
        }
        payload["response_format"] = {"type": "json_object"} if json_object_only else {
            "type": "json_schema",
            "json_schema": {
                "name": "incident_advice",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "classification": {"type": "string", "enum": sorted(CLASSIFICATIONS)},
                        "summary": {"type": "string"},
                        "suggested_rule": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "risk": {"type": "string", "enum": sorted(RISKS)},
                    },
                    "required": ["classification", "summary", "suggested_rule", "confidence", "risk"],
                    "additionalProperties": False,
                },
            },
        }
        if json_object_only:
            payload.pop("temperature", None)
            payload["max_tokens"] = 1600
            payload["reasoning"] = {"effort": "high", "exclude": True}
    else:
        payload["model"] = model
        payload["response_format"] = {"type": "json_object"}
    return payload


def _bounded_ui_semantics(path_value: str | None) -> dict[str, Any] | None:
    """Extract only structural counts and allowlisted control markers."""
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_file() or path.stat().st_size > 5_000_000:
        return None
    try:
        if path.suffix.lower() == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                source = handle.read(5_000_001)
        else:
            source = path.read_text(encoding="utf-8")[:5_000_001]
        if len(source) > 5_000_000:
            return None
        root = ET.fromstring(source)
    except (OSError, UnicodeError, ET.ParseError):
        return None
    nodes = list(root.iter())
    searchable = "\n".join(
        " ".join(
            str(node.attrib.get(key) or "")
            for key in ("text", "content-desc", "resource-id")
        )
        for node in nodes
    )
    return {
        "node_count": len(nodes),
        "clickable_count": sum(
            1 for node in nodes if node.attrib.get("clickable") == "true"
        ),
        "editable_count": sum(
            1
            for node in nodes
            if node.attrib.get("class") == "android.widget.EditText"
            or node.attrib.get("editable") == "true"
        ),
        "known_markers": [marker for marker in UI_CONTROL_MARKERS if marker in searchable],
    }


def analyze_incident(
    incident: IncidentRecord,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = 90.0,
) -> IncidentAdvice:
    api_key = api_key or os.environ.get("PHONE_AGENT_API_KEY")
    if not api_key:
        raise RuntimeError("No cloud model API key is available in the environment")
    base_url = (base_url or os.environ.get("PHONE_AGENT_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    model = model or os.environ.get("PHONE_AGENT_COMMENT_MODEL") or OPENROUTER_PRIMARY_MODEL
    fallbacks = tuple(
        item.strip()
        for item in os.environ.get("PHONE_AGENT_COMMENT_FALLBACK_MODELS", "").split(",")
        if item.strip()
    )
    payload = build_incident_analysis_payload(
        incident,
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
                    detail = response.text[:400]
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                        time.sleep(2.0)
                        continue
                    raise RuntimeError(f"Cloud model HTTP {response.status_code}: {detail}")
                content = parse_streaming_response(response.iter_lines())
            break
        except requests.RequestException as exc:
            if attempt >= 2:
                raise RuntimeError(f"Cloud model request failed: {exc}") from exc
            time.sleep(2.0)
    return parse_incident_advice(content)
