"""Discover provider models and normalize their usable capabilities."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..branding import USER_AGENT

GRSAI_MODEL_PAGE_URLS = (
    "https://grsai.com/zh/dashboard/models",
    "https://grsai.ai/dashboard/models",
)
SILICONFLOW_MODELS_URL = "https://api.siliconflow.cn/v1/models"
MODEL_CATALOG_TIMEOUT_SECONDS = 10
MAX_CATALOG_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProviderModel:
    provider: str
    id: str
    capabilities: tuple[str, ...]
    description: str = ""
    streaming: bool = True
    context_length: int | None = None
    sampling_parameters: tuple[str, ...] = ()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def label(self) -> str:
        badges: list[str] = []
        if self.supports("vision"):
            badges.append("多模态")
        if self.supports("reasoning"):
            badges.append("推理")
        suffix = f"  [{' · '.join(badges)}]" if badges else ""
        return f"{self.id}{suffix}"

    @property
    def capability_summary(self) -> str:
        parts = ["流式" if self.streaming else "非流式"]
        if self.context_length:
            parts.append(f"上下文 {self.context_length:,}")
        else:
            parts.append("上下文长度未声明")
        sampling = "/".join(self.sampling_parameters)
        parts.append(f"采样 {sampling}" if sampling else "平台默认采样")
        return " · ".join(parts)


class ModelDiscoveryError(RuntimeError):
    pass


def serialize_models(models: tuple[ProviderModel, ...]) -> str:
    return json.dumps(
        [asdict(model) for model in models],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def deserialize_models(value: str) -> tuple[ProviderModel, ...]:
    try:
        raw = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(raw, list):
        return ()
    models: list[ProviderModel] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip()
        model_id = str(item.get("id") or "").strip()
        capabilities = item.get("capabilities")
        if not provider or not model_id or not isinstance(capabilities, list):
            continue
        models.append(
            ProviderModel(
                provider,
                model_id,
                tuple(
                    str(capability)
                    for capability in capabilities
                    if str(capability).strip()
                ),
                str(item.get("description") or "").strip(),
                bool(item.get("streaming", True)),
                (
                    int(item["context_length"])
                    if isinstance(item.get("context_length"), int)
                    else None
                ),
                tuple(
                    str(parameter)
                    for parameter in item.get("sampling_parameters", [])
                    if str(parameter).strip()
                )
                if isinstance(item.get("sampling_parameters", []), list)
                else (),
            )
        )
    return tuple(models)


class ProviderModelCatalog:
    def __init__(self, *, opener=urlopen) -> None:
        self._opener = opener

    def fetch(
        self,
        provider: str,
        *,
        api_key: str = "",
    ) -> tuple[ProviderModel, ...]:
        normalized = provider.strip().lower()
        if normalized == "grsai":
            return self._fetch_grsai()
        if normalized == "siliconflow":
            if not api_key.strip():
                raise ModelDiscoveryError("请先保存硅基流动 API Key。")
            return self._fetch_siliconflow(api_key.strip())
        raise ValueError(f"Unsupported model provider: {provider}")

    def _fetch_grsai(self) -> tuple[ProviderModel, ...]:
        last_error: Exception | None = None
        for url in GRSAI_MODEL_PAGE_URLS:
            try:
                source = self._read_text(url)
                records = self._parse_grsai_records(source)
                models = tuple(
                    self._grsai_model(record) for record in records
                )
                usable = tuple(model for model in models if model.id)
                if usable:
                    return usable
            except Exception as exc:
                last_error = exc
        raise ModelDiscoveryError(
            f"无法读取 GRS AI 官方模型页：{last_error or '未返回模型'}"
        )

    def _fetch_siliconflow(
        self, api_key: str
    ) -> tuple[ProviderModel, ...]:
        models: list[ProviderModel] = []
        seen: set[str] = set()
        for model_type in ("text", "image", "audio"):
            payload = self._read_json(
                f"{SILICONFLOW_MODELS_URL}?{urlencode({'type': model_type})}",
                api_key=api_key,
            )
            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ModelDiscoveryError("硅基流动模型列表响应格式无效。")
            for item in items:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or "").strip()
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                models.append(self._siliconflow_model(model_id, model_type))
        if not models:
            raise ModelDiscoveryError("硅基流动没有返回可用模型。")
        return tuple(models)

    def _read_text(self, url: str, *, api_key: str = "") -> str:
        headers = {
            "Accept": "text/html,application/json",
            "User-Agent": f"Mozilla/5.0 {USER_AGENT}",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(url, headers=headers)
        try:
            with self._opener(
                request, timeout=MODEL_CATALOG_TIMEOUT_SECONDS
            ) as response:
                return response.read(MAX_CATALOG_BYTES).decode(
                    "utf-8", errors="replace"
                )
        except HTTPError as exc:
            raise ModelDiscoveryError(
                f"模型列表请求失败（HTTP {exc.code}）。"
            ) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise ModelDiscoveryError(f"模型列表网络错误：{exc}") from exc

    def _read_json(self, url: str, *, api_key: str) -> dict[str, Any]:
        text = self._read_text(url, api_key=api_key)
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ModelDiscoveryError("模型列表返回了无效 JSON。") from exc
        if not isinstance(payload, dict):
            raise ModelDiscoveryError("模型列表响应格式无效。")
        return payload

    @staticmethod
    def _parse_grsai_records(source: str) -> list[dict[str, Any]]:
        marker = '\\"models\\":'
        start = source.find(marker)
        if start < 0:
            raise ModelDiscoveryError("GRS AI 官方模型页缺少模型数据。")
        start += len(marker)
        depth = 0
        in_string = False
        end = -1
        index = start
        while index < len(source):
            if source.startswith('\\"', index):
                in_string = not in_string
                index += 2
                continue
            character = source[index]
            if not in_string:
                if character == "[":
                    depth += 1
                elif character == "]":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            index += 1
        if end < 0:
            raise ModelDiscoveryError("GRS AI 模型数据不完整。")
        escaped_array = source[start:end]
        try:
            decoded_array = json.loads(f'"{escaped_array}"')
            records = json.loads(decoded_array)
        except (TypeError, ValueError) as exc:
            raise ModelDiscoveryError("GRS AI 模型数据无法解析。") from exc
        if not isinstance(records, list):
            raise ModelDiscoveryError("GRS AI 模型数据格式无效。")
        return [record for record in records if isinstance(record, dict)]

    @staticmethod
    def _grsai_model(record: dict[str, Any]) -> ProviderModel:
        model_id = str(record.get("name") or "").strip()
        feature = str(record.get("feature") or "")
        description = str(record.get("desc") or "").strip()
        document = str(record.get("document") or "").lower()
        lowered = model_id.lower()
        capabilities: list[str] = []
        if "对话" in feature:
            capabilities.append("chat")
        if "识图" in feature:
            capabilities.append("vision")
        if "推理" in feature:
            capabilities.append("reasoning")
        if "文生图" in feature or "图生图" in feature:
            capabilities.append("image_generation")
        if not capabilities:
            is_image = (
                "image" in lowered
                or "nano-banana" in lowered
                or "gpt-image" in document
                or "nano-banana" in document
            )
            if is_image:
                capabilities.append("image_generation")
            elif (
                "documents/chat" in document
                or "452418916e0" in document
                or lowered.startswith(("gpt-", "gemini-"))
            ):
                capabilities.append("chat")
                if lowered.startswith("gemini-"):
                    capabilities.append("vision")
        context_length = None
        for key in ("context_length", "contextLength", "max_context_tokens"):
            value = record.get(key)
            if isinstance(value, int) and value > 0:
                context_length = value
                break
        sampling_parameters = (
            () if "reasoning" in capabilities else ("temperature",)
        )
        return ProviderModel(
            "grsai",
            model_id,
            tuple(capabilities),
            description,
            True,
            context_length,
            sampling_parameters,
        )

    @staticmethod
    def _siliconflow_model(model_id: str, model_type: str) -> ProviderModel:
        lowered = model_id.lower()
        capabilities: list[str] = []
        if model_type == "image":
            capabilities.append("image_generation")
        elif model_type == "audio":
            if any(
                hint in lowered
                for hint in ("tts", "cosyvoice", "fish-speech", "ttsd")
            ):
                capabilities.append("tts")
            if any(hint in lowered for hint in ("asr", "sensevoice")):
                capabilities.append("speech_recognition")
        else:
            unsuitable = any(
                hint in lowered
                for hint in (
                    "embedding",
                    "reranker",
                    "ocr",
                    "captioner",
                )
            )
            if not unsuitable:
                capabilities.append("chat")
                vision = any(
                    hint in lowered
                    for hint in (
                        "-vl-",
                        "/glm-4.5v",
                        "/glm-4.6v",
                        "vision",
                        "internvl",
                        "minicpm-v",
                        "qvq",
                        "omni",
                    )
                )
                if vision:
                    capabilities.append("vision")
            if "thinking" in lowered or "reason" in lowered:
                capabilities.append("reasoning")
        return ProviderModel(
            "siliconflow", model_id, tuple(capabilities)
        )


def models_for_capability(
    models: tuple[ProviderModel, ...], capability: str
) -> tuple[ProviderModel, ...]:
    return tuple(model for model in models if model.supports(capability))
