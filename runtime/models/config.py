"""Vox Agent configuration — profiles, providers, routing, and owned data paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from runtime.data_paths import legacy_voxlog_data_dirs, resolve_vox_agent_data_dir


def resolve_vox_agent_env(canonical: Path, legacy: Path) -> Path | None:
    """Prefer Vox Agent configuration; use VoxLog only as read-only compatibility input."""
    if canonical.is_file():
        return canonical
    if legacy.is_file():
        return legacy
    return None


VOX_AGENT_DIR = resolve_vox_agent_data_dir()
LEGACY_VOXLOG_DIR = legacy_voxlog_data_dirs()[0]

_env_path = resolve_vox_agent_env(VOX_AGENT_DIR / ".env", LEGACY_VOXLOG_DIR / ".env")
if _env_path is not None:
    load_dotenv(_env_path)


def _configured_path(canonical_name: str, legacy_name: str, default: Path) -> Path:
    value = os.getenv(canonical_name, "").strip() or os.getenv(legacy_name, "").strip()
    return Path(value).expanduser() if value else default


@dataclass
class ProviderConfig:
    name: str
    api_key: str
    base_url: str = ""
    model: str = ""
    region: str = ""  # us, cn, any
    tier: str = ""    # local, cloud_cn, cloud_intl


@dataclass
class Profile:
    """Network environment profile with provider preferences."""
    name: str          # home, office, mobile
    stt_main: str      # provider name
    stt_fallback: str
    llm_main: str
    llm_fallback: str


@dataclass
class VoxAgentConfig:
    """Canonical Vox Agent runtime configuration."""

    data_dir: Path = field(default_factory=resolve_vox_agent_data_dir)

    # API keys
    dashscope_key_us: str = field(default_factory=lambda: os.getenv("DASHSCOPE_API_KEY", ""))
    dashscope_key_cn: str = field(default_factory=lambda: os.getenv("DASHSCOPE_API_KEY_CN", ""))
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    siliconflow_key: str = field(default_factory=lambda: os.getenv("SILICONFLOW_API_KEY", ""))
    doubao_key: str = field(
        default_factory=lambda: os.getenv("DOUBAO_API_KEY", "") or os.getenv("ARK_API_KEY", "")
    )
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    custom_llm_key: str = field(default_factory=lambda: os.getenv("VOXAGENT_LLM_API_KEY", ""))
    openai_llm_model: str = field(
        default_factory=lambda: os.getenv("VOXAGENT_OPENAI_MODEL", "gpt-5-mini")
    )
    qwen_llm_model: str = field(
        default_factory=lambda: os.getenv("VOXAGENT_QWEN_MODEL", "qwen3.7-plus")
    )
    doubao_model: str = field(
        default_factory=lambda: os.getenv("VOXAGENT_DOUBAO_MODEL", "doubao-seed-2-0-lite-260215")
    )
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("VOXAGENT_ANTHROPIC_MODEL", "claude-sonnet-5")
    )
    custom_llm_base_url: str = field(
        default_factory=lambda: os.getenv("VOXAGENT_LLM_BASE_URL", "")
    )
    custom_llm_model: str = field(
        default_factory=lambda: os.getenv("VOXAGENT_LLM_MODEL", "")
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("VOXAGENT_OLLAMA_MODEL", "qwen2.5:7b")
    )
    digest_enhancement_enabled: bool = field(
        default_factory=lambda: os.getenv("VOXLOG_DIGEST_ENHANCEMENT_ENABLED", "1").lower() not in {"0", "false", "off"}
    )
    digest_enhancement_provider: str = field(
        default_factory=lambda: os.getenv("VOXLOG_DIGEST_ENHANCEMENT_PROVIDER", "auto")
    )

    # Server
    host: str = field(default_factory=lambda: os.getenv("VOXLOG_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("VOXLOG_PORT", "7902")))
    api_token: str = field(default_factory=lambda: os.getenv("VOXLOG_API_TOKEN", ""))

    # Paths
    db_path: Path = field(
        default_factory=lambda: _configured_path(
            "VOXAGENT_DB_PATH",
            "VOXLOG_DB_PATH",
            resolve_vox_agent_data_dir() / "history.db",
        )
    )
    terms_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "VOXAGENT_TERMS_DIR",
                os.getenv(
                    "VOXLOG_TERMS_DIR",
                    str(Path(__file__).parent.parent.parent / "dictionaries"),
                ),
            )
        )
    )
    log_dir: Path = field(
        default_factory=lambda: _configured_path(
            "VOXAGENT_LOG_DIR",
            "VOXLOG_LOG_DIR",
            resolve_vox_agent_data_dir() / "logs",
        )
    )

    # Limits
    max_audio_seconds: int = 600  # 10 minutes

    # Active profile
    active_profile: str = "home"

    # Profiles
    profiles: dict[str, Profile] = field(default_factory=lambda: {
        "home": Profile(
            name="home",
            stt_main="qwen-us", stt_fallback="openai-whisper",
            llm_main="openai-gpt", llm_fallback="qwen-turbo",
        ),
        "office": Profile(
            name="office",
            stt_main="qwen-cn", stt_fallback="siliconflow",
            llm_main="qwen-turbo", llm_fallback="ollama",
        ),
    })

    @property
    def profile(self) -> Profile:
        return self.profiles.get(self.active_profile, self.profiles["home"])

    def switch_profile(self, name: str) -> None:
        if name in self.profiles:
            self.active_profile = name

    def get_stt_key(self, provider: str) -> str:
        if "qwen-cn" in provider:
            return self.dashscope_key_cn or self.dashscope_key_us
        if "qwen" in provider:
            return self.dashscope_key_us
        if "openai" in provider:
            return self.openai_key
        if "silicon" in provider:
            return self.siliconflow_key
        return ""

    def get_llm_key(self, provider: str) -> str:
        normalized = provider.lower()
        if "doubao" in normalized or "ark" in normalized:
            return self.doubao_key
        if "claude" in normalized or "anthropic" in normalized:
            return self.anthropic_key
        if "custom" in normalized or "openai-compatible" in normalized:
            return self.custom_llm_key
        if "qwen" in normalized:
            return self.dashscope_key_cn or self.dashscope_key_us
        if "openai" in normalized:
            return self.openai_key
        if "silicon" in normalized:
            return self.siliconflow_key
        return ""


# Import compatibility for older extensions. New code should use VoxAgentConfig.
VoxLogConfig = VoxAgentConfig


def get_config() -> VoxAgentConfig:
    return VoxAgentConfig()
