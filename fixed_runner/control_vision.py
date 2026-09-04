from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from comment_ai import encode_image, parse_streaming_response
from model_runtime_config import OPENROUTER_PRIMARY_MODEL


PROMPT_VERSION = "control-candidate-v1-2026-08-31"


@dataclass(frozen=True)
class VisionCandidate:
    page_type: str
    semantic_name: str
    region: tuple[float, float, float, float]
    confidence: float
    evidence: str
    prompt_version: str = PROMPT_VERSION

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Vision model did not return JSON")
    parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Vision model response is not an object")
    return parsed


def parse_candidate(text: str, expected_semantic_name: str) -> VisionCandidate:
    value = _extract_json(text)
    semantic_name = str(value.get("semantic_name") or "").strip()
    if semantic_name != expected_semantic_name:
        raise ValueError("Vision candidate semantic name mismatch")
    raw_region = value.get("region")
    if not (
        isinstance(raw_region, list)
        and len(raw_region) == 4
        and all(isinstance(item, (int, float)) for item in raw_region)
    ):
        raise ValueError("Vision candidate region is invalid")
    region = tuple(float(item) for item in raw_region)
    left, top, right, bottom = region
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("Vision candidate region is outside the screen")
    confidence = float(value.get("confidence", 0.0))
    evidence = " ".join(str(value.get("evidence") or "").split())[:240]
    if confidence < 0.9 or not evidence:
        raise ValueError("Vision candidate confidence or evidence is insufficient")
    return VisionCandidate(
        page_type=str(value.get("page_type") or "unknown")[:60],
        semantic_name=semantic_name,
        region=region,
        confidence=min(confidence, 1.0),
        evidence=evidence,
    )


class VisionCandidateLocator:
    """Read-only model channel. It never receives a device or click function."""

    def locate(
        self,
        image_path: Path,
        *,
        semantic_name: str,
        page_hint: str,
        timeout_seconds: float = 60.0,
    ) -> VisionCandidate:
        api_key = os.environ.get("PHONE_AGENT_API_KEY")
        if not api_key:
            raise RuntimeError("云端视觉模型密钥不可用")
        base_url = os.environ.get(
            "PHONE_AGENT_BASE_URL", "https://openrouter.ai/api/v1"
        ).rstrip("/")
        model = os.environ.get("PHONE_AGENT_COMMENT_MODEL") or OPENROUTER_PRIMARY_MODEL
        system = (
            "You are a read-only Android UI locator. Return one JSON object only: "
            '{"page_type":"...","semantic_name":"...","region":[0,0,1,1],'
            '"confidence":0.0,"evidence":"visible evidence"}. '
            "Region coordinates are normalized. Do not suggest or perform an action. "
            "Return confidence below 0.9 when uncertain."
        )
        user = (
            f"Locate semantic control {semantic_name!r}. Expected page: {page_hint}. "
            "Use only visible current-frame evidence."
        )
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64," + encode_image(image_path)
                            },
                        },
                    ],
                },
            ],
            "stream": True,
            "max_tokens": 500,
            "temperature": 0.0,
        }
        if "openrouter.ai" in base_url.lower():
            payload["model"] = model
            payload["provider"] = {"data_collection": "deny", "allow_fallbacks": True}
            payload["response_format"] = {"type": "json_object"}
        else:
            payload["model"] = model
            payload["response_format"] = {"type": "json_object"}
        proxy = os.environ.get("PHONE_AGENT_PROXY_URL")
        proxies = {"http": proxy, "https": proxy} if proxy else None
        with requests.post(
            base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            stream=True,
            timeout=timeout_seconds,
            proxies=proxies,
        ) as response:
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Cloud vision HTTP {response.status_code}: {response.text[:300]}"
                )
            return parse_candidate(
                parse_streaming_response(response.iter_lines()), semantic_name
            )
