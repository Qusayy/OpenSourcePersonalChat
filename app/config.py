"""Environment-driven settings. Defaults target a 2 vCPU / 4 GB VPS."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # protected_namespaces=() because pydantic v2 reserves the "model_" field prefix
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AURORA_",
        extra="ignore",
        protected_namespaces=(),
    )

    # model
    model_path: str = str(BASE_DIR / "models" / "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf")
    model_label: str = "Qwen2.5-1.5B-Instruct"
    model_quant: str = "Q4_K_M"
    model_params: str = "1.5B"

    # llama.cpp runtime
    n_ctx: int = 4096
    n_threads: int = 2
    n_batch: int = 256
    use_mmap: bool = True
    use_mlock: bool = False
    chat_format: str = "auto"

    # generation ceilings
    max_tokens: int = 512
    reply_headroom: int = 64  # tokens kept free above prompt + max_tokens

    # app
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = str(BASE_DIR / "data" / "aurora.db")
    hardware_label: str = "2 vCPU · 4 GB VPS"
    mock: bool = False

    @property
    def resolved_model_path(self) -> Path:
        p = Path(self.model_path)
        return p if p.is_absolute() else (BASE_DIR / p).resolve()

    @property
    def resolved_db_path(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (BASE_DIR / p).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
