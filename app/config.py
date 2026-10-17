"""Environment-driven settings. Defaults target a 2 vCPU / 4 GB VPS."""

from __future__ import annotations

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
    # Empty: the model is loaded into this process with llama-cpp-python. An
    # address (http://127.0.0.1:8081): every pass goes to the llama-server
    # there instead, which deploy/aurora-llm.service runs from this same file.
    llm_url: str = ""
    n_ctx: int = 4096
    # What the KV cache holds per token of context, in KiB, for the memory
    # estimate on the Benchmark page: Qwen2.5-1.5B's 28 (28 layers, 2 KV
    # heads of 128, f16). LFM2.5-1.2B keeps a cache in 6 of its 16 layers,
    # 8 KV heads of 64: 12.
    kv_kib_per_token: int = 28
    # 0 is every CPU this process may use, measured at startup (affinity and
    # cgroup quota). A number pins it. A server resized from 2 cores to 4 kept
    # an old "2" and ran on half the machine; the measured default cannot.
    n_threads: int = 0
    n_batch: int = 256
    use_mmap: bool = True
    use_mlock: bool = False
    chat_format: str = "auto"

    # generation ceilings
    max_tokens: int = 512
    reply_headroom: int = 64  # tokens kept free above prompt + max_tokens

    # tools
    tools_enabled: bool = True
    # One switch to make the whole install offline-only. Network tools vanish
    # from the registry, the grammar, and the UI.
    allow_outbound: bool = True
    tool_timeout: float = 10.0
    # Wikimedia's robot policy returns 403 for a User-Agent with no contact
    # information, so this must carry a URL or an email. Point it at your own
    # repository via AURORA_USER_AGENT.
    user_agent: str = (
        "Aurora/2.0 (+https://github.com/topics/llama-cpp; self-hosted llama.cpp chat)"
    )
    route_max_tokens: int = 96
    # Searching the web. "auto": the SearXNG at ``searx_url`` where there is
    # one, DuckDuckGo's HTML page where there is not. "searxng" and
    # "duckduckgo" name one; "off" takes the search off the menu.
    search: str = "auto"
    # A SearXNG this server runs for itself, on loopback, with its JSON
    # format on (deploy/searxng). The address is the operator's, never the
    # model's to choose.
    searx_url: str = ""
    # Where the headlines are for: a Google News edition, COUNTRY:language.
    news_region: str = "US:en"
    # Running Python the model wrote. "auto": wherever the sandbox can run a
    # program (bubblewrap, user namespaces, the venv below); "off": never.
    # There is no setting that runs it outside the sandbox.
    sandbox: str = "auto"
    # The sandbox's own venv, read-only to it (deploy/build-sandbox.sh).
    sandbox_venv: str = "/opt/aurora-sandbox/venv"
    # The zone the model is told a message was sent in ("Sent Sat 17 Oct
    # 2026, 14:03 BST."), an IANA name.
    timezone: str = "UTC"

    # the loop
    # Off, a message that reaches the model makes one choice of tool and then
    # answers. On, a tool the model chose earns it another choice: it may look
    # something up, read what came back, and look up something else. Every
    # choice is a pass through the model and costs seconds, so the loop is
    # bounded three ways, and a tool the visitor named themselves (a command,
    # a pattern, a skill) never starts it.
    agent_loop: bool = True
    agent_max_steps: int = 3        # tool calls for one message
    agent_max_decisions: int = 3    # passes that choose, for one message
    agent_max_seconds: float = 90.0  # for everything before the answer

    # notes
    # What the Agent may keep when asked to remember something, and who can
    # read it back. "conversation": a note belongs to the conversation it was
    # made in, is read back only there and is deleted with it. "site": every
    # conversation shares the notes, which only suits a private deployment,
    # since this site has no accounts. "off": no notes at all. A plain string,
    # like the gate, so that a mistake keeps notes to their conversation.
    notes: str = "conversation"

    # gate
    # Who is let past the cover; see app/gate.py. The site is served to
    # everyone only when this is exactly "off". It is a plain string and not
    # a boolean so that a typing mistake leaves the site closed instead of
    # stopping the server.
    gate: str = "on"
    # The line the setup page prints for the owner's pattern. It belongs in
    # the server's own .env and nowhere else: whoever has it can work the
    # pattern out. Empty or malformed, nothing opens the site.
    gate_verifier: str = ""
    # A session ends when the browser closes. These end it anyway, for the
    # phone whose browser never does.
    gate_session_hours: float = 12.0
    gate_idle_minutes: float = 60.0

    # app
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = str(BASE_DIR / "data" / "aurora.db")
    # How pages name the machine in prose ("running on a …"). Empty means
    # describe it from what is measured at runtime; see telemetry.describe().
    hardware_label: str = ""
    # Days to keep the record of what each message did step by step: which
    # tool, what it was given, what came back, what each pass cost. It is a
    # copy of what visitors typed, so it does not live for ever. Zero keeps
    # none of it.
    steps_keep_days: float = 30.0
    mock: bool = False
    # Seconds between fake tokens in mock mode. The default imitates this
    # hardware; drop it to near zero to drive the UI quickly in tests.
    mock_delay: float = 0.085

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
