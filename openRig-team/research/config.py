"""Config for the re-homed local-research subsystem (package research).

DEFAULTS-only, env-overridable knobs for the myTeams local-research subsystem.
Standalone: this package never imports or reaches outside myTeams. Env overrides,
in order of priority:
  MYTEAMS_LR_*  — preferred prefix for the knobs below.
  bare names    — WEB_MAX_RESULTS / FETCH_TIMEOUT / LLM_* / GITHUB_TOKEN /
                  HF_TOKEN (zero-behaviour-change; never hardcoded).

New knobs added by the 2026-09-04 upgrade (each env-overridable, DEFAULTS only):
  LR_CHANNELS          comma-separated extra research channels ("google,reddit"),
                       default "" = disabled. Consumed by deep_research.py.
  LR_GOOGLE_MAX_RESULTS    per-Google-SERP result cap (default 10).
  LR_GOOGLE_TIMEOUT_MS     playwright nav timeout (default 45000).
  LR_GOOGLE_DOMAINS        random-domain pool used per run; "google.com" primary
                       is US-valid ONLY per the Google-SERP selectors (hard rule).
  LR_GOOGLE_STATE_FILE     persistent storage-state path (default ~/.google-search-browser-state.json).
  LR_REDDIT_MAX_RESULTS    per-Reddit-channel result cap (default 10).
  LR_REDDIT_TIMEOUT        httpx timeout (default 15).
  LR_REDDIT_THROTTLE_MS    two-int comma "min,max" throttle between Reddit calls.
  LR_REDDIT_UA             custom browser-like UA (default Chrome/126).
  LR_CHANNEL_RUN_CAP       total channel digest cap fed to the LLM planner.
"""
from __future__ import annotations
import os, pathlib

# --- Model routing from shared/models.yaml (ONE place; env override wins) -----
_models_yaml = pathlib.Path(__file__).resolve().parents[1] / "shared" / "models.yaml"
if _models_yaml.exists():
    import yaml as _yaml
    _mcfg = _yaml.safe_load(_models_yaml.read_text()) or {}
    _provider = os.environ.get("RESEARCH_PROVIDER") or _mcfg.get("research", {}).get("provider", "local")
    _prov = _mcfg.get("providers", {}).get(_provider, {})
    _res = _mcfg.get("research", {})
    os.environ.setdefault("RESEARCH_PROVIDER", _provider)
    if _prov.get("api_base"):
        os.environ.setdefault("OPENAI_API_BASE", _prov["api_base"])
        os.environ.setdefault("LLM_API_BASE", _prov["api_base"])
    if "api_key" in _prov:
        os.environ.setdefault("OPENAI_API_KEY", _prov["api_key"])
    elif "api_key_env" in _prov:
        os.environ.setdefault("OPENAI_API_KEY", os.environ.get(_prov["api_key_env"], ""))
    if _res.get("fast_model"):
        os.environ.setdefault("FAST_LLM_MODEL", _res["fast_model"])
        os.environ.setdefault("FAST_LLM", "openai:" + _res["fast_model"])  # gpt-researcher wants provider:model
        os.environ.setdefault("LLM_MODEL", _res["fast_model"])
    if _res.get("smart_model"):
        os.environ.setdefault("SMART_LLM_MODEL", _res["smart_model"])
        os.environ.setdefault("SMART_LLM", "openai:" + _res["smart_model"])  # provider:model format
        # Qwen3-Coder is a thinking model: vLLM returns content=None, text in
        # `reasoning` -> gpt-researcher sees empty. Disable thinking server-side.
        os.environ.setdefault("LLM_KWARGS", '{"extra_body": {"chat_template_kwargs": {"enable_thinking": false}}}')

ROOT = __file__ and os.path.dirname(os.path.abspath(__file__))
STATE_FILE_DEFAULT = os.path.join(os.path.expanduser("~"), ".google-search-browser-state.json")

# --- Search backend knobs (re-homed, zero-behaviour-change) ------------------
WEB_MAX_RESULTS = int(os.environ.get("WEB_MAX_RESULTS", "8"))   # per ddgs query
GH_MAX_RESULTS = int(os.environ.get("GH_MAX_RESULTS", "10"))
HF_MAX_RESULTS = int(os.environ.get("HF_MAX_RESULTS", "10"))
FETCH_TIMEOUT = float(os.environ.get("FETCH_TIMEOUT", "20"))
FETCH_MAX_BYTES = int(os.environ.get("FETCH_MAX_BYTES", str(2 * 1024 * 1024)))  # 2MB text cap

# --- Optional tokens (raise ceilings; not required) ---------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# --- Local LLM (mlx-lm, Qwen3.6-35B-A3B thinking-OFF) -------------------------
LLM_MODEL = os.environ.get("LLM_MODEL") or os.environ.get("MYTEAMS_LLM_MODEL", "mlx-community/Qwen3.6-35B-A3B-4bit")
LLM_HOST = os.environ.get("LLM_HOST", "127.0.0.1")
LLM_PORT = int(os.environ.get("LLM_PORT", "8766"))
LLM_API_BASE = os.environ.get("LLM_API_BASE") or f"http://{LLM_HOST}:{LLM_PORT}/v1"

# --- Upgrade knobs (2026-09-04; MYTEAMS_LR_* preferred, DEFAULTS only) --------
LR_CHANNELS = os.environ.get("MYTEAMS_LR_CHANNELS", os.environ.get("LR_CHANNELS", ""))
LR_GOOGLE_MAX_RESULTS = int(os.environ.get("MYTEAMS_LR_GOOGLE_MAX_RESULTS", "10"))
LR_GOOGLE_TIMEOUT_MS = int(os.environ.get("MYTEAMS_LR_GOOGLE_TIMEOUT_MS", "45000"))
_LR_GOOGLE_DOMAINS_DEFAULT = ("https://www.google.com,https://www.google.com.au,"
                              "https://www.google.co.uk,https://www.google.ca,"
                              "https://www.google.co.in,https://www.google.co.jp,"
                              "https://www.google.de,https://www.google.fr")
LR_GOOGLE_DOMAINS = [d.strip() for d in
                     os.environ.get("MYTEAMS_LR_GOOGLE_DOMAINS", _LR_GOOGLE_DOMAINS_DEFAULT).split(",") if d.strip()]
LR_GOOGLE_STATE_FILE = os.environ.get("MYTEAMS_LR_GOOGLE_STATE_FILE", STATE_FILE_DEFAULT)
LR_REDDIT_MAX_RESULTS = int(os.environ.get("MYTEAMS_LR_REDDIT_MAX_RESULTS", "10"))
LR_REDDIT_TIMEOUT = float(os.environ.get("MYTEAMS_LR_REDDIT_TIMEOUT", "15"))
_LR_REDDIT_THROTTLE = os.environ.get("MYTEAMS_LR_REDDIT_THROTTLE_MS", "500,1500").split(",")
LR_REDDIT_THROTTLE_MS = (int(_LR_REDDIT_THROTTLE[0]), int(_LR_REDDIT_THROTTLE[1])) if len(_LR_REDDIT_THROTTLE) == 2 else (500, 1500)
LR_REDDIT_UA = os.environ.get(
    "MYTEAMS_LR_REDDIT_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
LR_CHANNEL_RUN_CAP = int(os.environ.get("MYTEAMS_LR_CHANNEL_RUN_CAP", "12"))

# --- Discover knobs (DEFAULTS only) ------------------------------------------
LR_DISCOVER_TIMEOUT = float(os.environ.get("MYTEAMS_LR_DISCOVER_TIMEOUT", "20"))
LR_DISCOVER_MAX_SOURCES = int(os.environ.get("MYTEAMS_LR_DISCOVER_MAX_SOURCES", "6"))


def cfg_help() -> str:
    return (f"research subsystem:\n"
            f"  search : ddgs + GitHub API + HF API (tokens optional: "
            f"GITHUB_TOKEN={'set' if GITHUB_TOKEN else 'unset'}, "
            f"HF_TOKEN={'set' if HF_TOKEN else 'unset'})\n"
            f"  channels : {LR_CHANNELS or '(none)'} (google,reddit — LR_CHANNELS)\n"
            f"  LLM    : {LLM_MODEL} on {LLM_API_BASE}\n")
