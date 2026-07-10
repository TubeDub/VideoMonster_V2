"""AI Sources config — Local / My API / TubeDub Cloud / Future (Production TZ).

Default is always Local AI (free). Cloud and user API are optional.
Nothing is downloaded or charged unless the user opts in.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_sources")


class AISourceMode(str, Enum):
    LOCAL = "local"
    USER_API = "user_api"
    TUBEDUB_CLOUD = "tubedub_cloud"
    FUTURE = "future"


class QualityMode(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    MAX_QUALITY = "max_quality"


# Priority chain for Maximum Quality (TZ §10). First available wins.
QUALITY_PRIORITY: tuple[str, ...] = (
    "gpt-5.5",
    "claude-sonnet-4",
    "claude-3-5-sonnet",
    "gpt-4.1",
    "gpt-4o",
    "gpt-4o-mini",
    "qwen2.5:14b",
    "qwen2.5:7b",
    "deepseek-r1:14b",
    "deepseek-r1:7b",
    "llama3.1:8b",
)

# VRAM GB → recommended local Ollama tag (TZ §5).
VRAM_MODEL_TABLE: tuple[tuple[float, str, str], ...] = (
    (24.0, "qwen2.5:32b", "GPU 24+ GB"),
    (12.0, "qwen2.5:14b", "GPU 12–24 GB"),
    (6.0, "qwen2.5:7b", "GPU 6–12 GB"),
    (4.0, "qwen2.5:3b", "GPU 4–6 GB"),
    (0.0, "qwen2.5:3b", "CPU / <4 GB VRAM"),
)


@dataclass
class UserAPIConfig:
    provider: str = "openai"  # openai | anthropic | openrouter | github | custom
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 1024
    context_tokens: int = 8192
    streaming: bool = False
    reasoning: bool = False


@dataclass
class LocalAIConfig:
    provider: str = "ollama"  # ollama | lmstudio | vllm
    model: str = ""
    base_url: str = ""
    models_dir: str = ""  # external folder — never inside TubeDub app dir
    auto_download: bool = False  # always False by default (TZ §6)


@dataclass
class TubeDubCloudConfig:
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class AISourcesConfig:
    """Persisted AI source selection. Local is always the default."""

    source_mode: str = AISourceMode.LOCAL.value
    quality_mode: str = QualityMode.MAX_QUALITY.value
    local: LocalAIConfig = field(default_factory=LocalAIConfig)
    user_api: UserAPIConfig = field(default_factory=UserAPIConfig)
    tubedub_cloud: TubeDubCloudConfig = field(default_factory=TubeDubCloudConfig)
    first_run_prompt_done: bool = False
    # User confirmed they want to continue without local models / without API.
    allow_mt_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_mode": self.source_mode,
            "quality_mode": self.quality_mode,
            "local": asdict(self.local),
            "user_api": {**asdict(self.user_api), "api_key": _mask(self.user_api.api_key)},
            "user_api_configured": bool(self.user_api.api_key or self.user_api.base_url),
            "tubedub_cloud": {
                **asdict(self.tubedub_cloud),
                "api_key": _mask(self.tubedub_cloud.api_key),
            },
            "tubedub_cloud_configured": bool(
                self.tubedub_cloud.enabled
                and (self.tubedub_cloud.api_key or self.tubedub_cloud.base_url)
            ),
            "first_run_prompt_done": self.first_run_prompt_done,
            "allow_mt_only": self.allow_mt_only,
            "policy": {
                "default_source": AISourceMode.LOCAL.value,
                "local_always_free": True,
                "no_paywall": True,
                "no_auto_download": True,
                "models_outside_app": True,
            },
        }


def _mask(key: str) -> str:
    k = str(key or "")
    if len(k) <= 8:
        return ("*" * len(k)) if k else ""
    return k[:4] + "…" + k[-4:]


def _config_path(app_dir: Path | None = None) -> Path:
    root = Path(app_dir) if app_dir else Path(__file__).resolve().parents[1]
    return root / "data" / "ai_sources.json"


def recommend_local_model(*, vram_gb: float = 0.0, has_gpu: bool = False) -> dict[str, Any]:
    """Suggest an Ollama tag from detected VRAM (TZ §5). Never downloads."""
    vram = float(vram_gb or 0.0) if has_gpu else 0.0
    for floor, model, label in VRAM_MODEL_TABLE:
        if vram >= floor:
            return {
                "model": model,
                "reason": label,
                "vram_gb": vram,
                "has_gpu": has_gpu,
                "auto_download": False,
            }
    return {
        "model": "qwen2.5:3b",
        "reason": "CPU fallback",
        "vram_gb": vram,
        "has_gpu": False,
        "auto_download": False,
    }


class AISourcesStore:
    """Load / save AI Sources without embedding secrets in env unless applied."""

    def __init__(self, app_dir: str | Path | None = None) -> None:
        self.app_dir = Path(app_dir) if app_dir else Path(__file__).resolve().parents[1]
        self.path = _config_path(self.app_dir)
        self._lock = threading.RLock()
        self._cfg = self._load()

    def _load(self) -> AISourcesConfig:
        if not self.path.is_file():
            return AISourcesConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._from_dict(raw)
        except Exception as exc:
            logger.warning("[AI_SOURCES] load failed: %s", exc)
            return AISourcesConfig()

    def _from_dict(self, raw: dict[str, Any]) -> AISourcesConfig:
        local = raw.get("local") or {}
        user = raw.get("user_api") or {}
        cloud = raw.get("tubedub_cloud") or {}
        return AISourcesConfig(
            source_mode=str(raw.get("source_mode") or AISourceMode.LOCAL.value),
            quality_mode=str(raw.get("quality_mode") or QualityMode.MAX_QUALITY.value),
            local=LocalAIConfig(
                provider=str(local.get("provider") or "ollama"),
                model=str(local.get("model") or ""),
                base_url=str(local.get("base_url") or ""),
                models_dir=str(local.get("models_dir") or ""),
                auto_download=False,
            ),
            user_api=UserAPIConfig(
                provider=str(user.get("provider") or "openai"),
                api_key=str(user.get("api_key") or ""),
                base_url=str(user.get("base_url") or ""),
                model=str(user.get("model") or "gpt-4o-mini"),
                temperature=float(user.get("temperature") or 0.2),
                max_tokens=int(user.get("max_tokens") or 1024),
                context_tokens=int(user.get("context_tokens") or 8192),
                streaming=bool(user.get("streaming")),
                reasoning=bool(user.get("reasoning")),
            ),
            tubedub_cloud=TubeDubCloudConfig(
                enabled=bool(cloud.get("enabled")),
                base_url=str(cloud.get("base_url") or os.getenv("VM_TUBEDUB_CLOUD_URL") or ""),
                api_key=str(cloud.get("api_key") or ""),
                model=str(cloud.get("model") or ""),
            ),
            first_run_prompt_done=bool(raw.get("first_run_prompt_done")),
            allow_mt_only=bool(raw.get("allow_mt_only", True)),
        )

    def save(self, cfg: AISourcesConfig | None = None) -> None:
        with self._lock:
            if cfg is not None:
                self._cfg = cfg
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "source_mode": self._cfg.source_mode,
                "quality_mode": self._cfg.quality_mode,
                "local": asdict(self._cfg.local),
                "user_api": asdict(self._cfg.user_api),
                "tubedub_cloud": asdict(self._cfg.tubedub_cloud),
                "first_run_prompt_done": self._cfg.first_run_prompt_done,
                "allow_mt_only": self._cfg.allow_mt_only,
            }
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def get(self) -> AISourcesConfig:
        with self._lock:
            return self._cfg

    def update(self, **kwargs: Any) -> AISourcesConfig:
        with self._lock:
            cfg = self._cfg
            if "source_mode" in kwargs and kwargs["source_mode"]:
                mode = str(kwargs["source_mode"])
                if mode in {m.value for m in AISourceMode}:
                    cfg.source_mode = mode
            if "quality_mode" in kwargs and kwargs["quality_mode"]:
                qm = str(kwargs["quality_mode"])
                if qm in {m.value for m in QualityMode}:
                    cfg.quality_mode = qm
            if "local" in kwargs and isinstance(kwargs["local"], dict):
                for k, v in kwargs["local"].items():
                    if hasattr(cfg.local, k) and k != "auto_download":
                        setattr(cfg.local, k, v)
                cfg.local.auto_download = False
            if "user_api" in kwargs and isinstance(kwargs["user_api"], dict):
                for k, v in kwargs["user_api"].items():
                    if hasattr(cfg.user_api, k):
                        setattr(cfg.user_api, k, v)
            if "tubedub_cloud" in kwargs and isinstance(kwargs["tubedub_cloud"], dict):
                for k, v in kwargs["tubedub_cloud"].items():
                    if hasattr(cfg.tubedub_cloud, k):
                        setattr(cfg.tubedub_cloud, k, v)
            if "first_run_prompt_done" in kwargs:
                cfg.first_run_prompt_done = bool(kwargs["first_run_prompt_done"])
            if "allow_mt_only" in kwargs:
                cfg.allow_mt_only = bool(kwargs["allow_mt_only"])
            self.save(cfg)
            return cfg

    def apply_to_env(self) -> dict[str, str]:
        """Push active source settings into process env for legacy resolvers.

        Never forces downloads. Local remains free; cloud only if configured.
        """
        applied: dict[str, str] = {}
        cfg = self.get()
        mode = cfg.source_mode

        # Quality mode always applied.
        os.environ["VM_ADAPTATION_SPEED_MODE"] = cfg.quality_mode
        os.environ["VM_LLM_SPEED_MODE"] = cfg.quality_mode
        applied["VM_ADAPTATION_SPEED_MODE"] = cfg.quality_mode

        if mode == AISourceMode.LOCAL.value:
            if cfg.local.model:
                os.environ["VM_TRANSLATE_MODEL"] = cfg.local.model
                applied["VM_TRANSLATE_MODEL"] = cfg.local.model
            if cfg.local.base_url:
                os.environ["VM_LLM_BASE_URL"] = cfg.local.base_url
                applied["VM_LLM_BASE_URL"] = cfg.local.base_url
            if cfg.local.models_dir:
                # Prefer external Ollama models folder — never under app dir.
                os.environ["OLLAMA_MODELS"] = cfg.local.models_dir
                applied["OLLAMA_MODELS"] = cfg.local.models_dir

        elif mode == AISourceMode.USER_API.value:
            ua = cfg.user_api
            if ua.model:
                os.environ["VM_TRANSLATE_MODEL"] = ua.model
                os.environ["VM_OPENAI_MODEL"] = ua.model
                applied["VM_TRANSLATE_MODEL"] = ua.model
            if ua.api_key:
                os.environ["OPENAI_API_KEY"] = ua.api_key
                os.environ["VM_LLM_API_KEY"] = ua.api_key
                if ua.provider == "anthropic":
                    os.environ["ANTHROPIC_API_KEY"] = ua.api_key
                elif ua.provider == "openrouter":
                    os.environ["OPENROUTER_API_KEY"] = ua.api_key
                elif ua.provider == "github":
                    os.environ["GITHUB_TOKEN"] = ua.api_key
                    os.environ["GITHUB_MODELS_TOKEN"] = ua.api_key
                applied["api_key"] = "set"
            base = ua.base_url
            if not base:
                base = _default_base_for_provider(ua.provider)
            if base:
                os.environ["VM_LLM_BASE_URL"] = base
                os.environ["OPENAI_BASE_URL"] = base
                applied["VM_LLM_BASE_URL"] = base

        elif mode == AISourceMode.TUBEDUB_CLOUD.value:
            tc = cfg.tubedub_cloud
            if tc.base_url:
                os.environ["VM_TUBEDUB_CLOUD_URL"] = tc.base_url
                os.environ["VM_LLM_BASE_URL"] = tc.base_url.rstrip("/") + "/v1"
                applied["VM_LLM_BASE_URL"] = os.environ["VM_LLM_BASE_URL"]
            if tc.api_key:
                os.environ["VM_LLM_API_KEY"] = tc.api_key
                applied["api_key"] = "set"
            if tc.model:
                os.environ["VM_TRANSLATE_MODEL"] = tc.model
                applied["VM_TRANSLATE_MODEL"] = tc.model

        # Future: no-op (extension point).

        os.environ["VM_AI_SOURCE_MODE"] = mode
        applied["VM_AI_SOURCE_MODE"] = mode
        return applied


def _default_base_for_provider(provider: str) -> str:
    p = (provider or "").lower()
    return {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "anthropic": "https://api.anthropic.com",
        "github": "https://models.inference.ai.azure.com",
        "deepseek": "https://api.deepseek.com/v1",
    }.get(p, "")


_store: AISourcesStore | None = None
_store_lock = threading.Lock()


def get_ai_sources(app_dir: str | Path | None = None) -> AISourcesStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AISourcesStore(app_dir=app_dir)
    return _store


def reset_ai_sources() -> None:
    global _store
    with _store_lock:
        _store = None
