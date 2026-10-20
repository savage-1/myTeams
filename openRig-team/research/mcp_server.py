"""Local-research MCP server — exposes search tools backed by ddgs + GitHub +
HuggingFace APIs (re-homed 2026-09-04) PLUS
the new google/reddit research channels (slice 5): ``google_search`` and
``reddit_search`` wrappers mirroring the existing tooling.

Hard rules:
  * If playwright is missing at import, the module STILL imports (favours a
    factory import, never a top-level ``import playwright``); the google tool
    returns a clear "channel unavailable — install playwright" message.
  * No top-level network; tools are async and return JSON strings.
"""
from __future__ import annotations

import json

from . import config as cfg
from .search_client import (web_search, fetch_url, github_search, hf_search,
                            health, google_search, reddit_search,
                            channels_available, GoogleChannelError,
                            RedditChannelError)


def _mcp_or_none():
    """Factory import: ``mcp`` may be absent from the runtime venv; the module
    must import OK and the MCP tools degrade to plain callables."""
    try:
        from mcp.server.fastmcp import FastMCP
        return FastMCP("local-research")
    except Exception:  # noqa: BLE001
        return None


mcp = _mcp_or_none()


def _register(mcp_instance, name, description):
    """Decorator-with-args: register the REAL fn on a FastMCP instance if
    present, and ALWAYS return the unchanged fn so the module-level names stay
    callable even when ``mcp`` is absent (import-safe factory seam)."""
    def wrap(fn):
        if mcp_instance is not None:
            mcp_instance.tool(name=name, description=description)(fn)
        return fn
    return wrap


@_register(mcp, "local_web_search", "Web search (DuckDuckGo)")
async def local_web_search(query: str, n: int = 8) -> str:
    """Web search (DuckDuckGo). Returns JSON: [{title, url, snippet}, ...].
    Use for general web research — models, papers, docs, repos, concepts.

    Args:
        query: the search query.
        n: max results (default 8).
    """
    return await _tool_result(web_search(query, n=n))


@_register(mcp, "local_fetch_url", "Fetch a URL and return its readable text")
async def local_fetch_url(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return its readable text (HTML stripped). Use to read a
    page/repo/docs found via search. Caps at max_chars (default 8000).

    Args:
        url: the absolute URL to fetch.
        max_chars: max text chars to return (default 8000).
    """
    return await _tool_result(fetch_url(url, max_chars=max_chars))


@_register(mcp, "local_github_search",
           "Search GitHub via the REST API")
async def local_github_search(query: str, kind: str = "repositories", n: int = 10) -> str:
    """Search GitHub via the REST API. Returns JSON results.
    kind: 'repositories' (default) | 'code' | 'issues' | 'users'.
    60/hr unauth, 5000/hr with GITHUB_TOKEN env var.

    Args:
        query: the GitHub search query.
        kind: repositories | code | issues | users.
        n: max results (default 10).
    """
    return await _tool_result(github_search(query, kind=kind, n=n))


@_register(mcp, "local_hf_search",
           "Search HuggingFace (models/datasets/spaces)")
async def local_hf_search(query: str, kind: str = "models", n: int = 10) -> str:
    """Search HuggingFace (models/datasets/spaces) via huggingface_hub.
    Returns JSON: [{id, downloads, likes, tags}, ...] sorted by downloads.

    Args:
        query: the HF search query.
        kind: models (default) | datasets | spaces.
        n: max results (default 10).
    """
    return await _tool_result(hf_search(query, kind=kind, n=n))


@_register(mcp, "local_research_health",
           "Check reachability of all backends")
async def local_research_health() -> str:
    """Check reachability of all backends (web/github/hf/llm/google/reddit).
    Use to verify the subsystem is up before relying on it."""
    return await _tool_result(health())


@_register(mcp, "local_research_deep",
           "Deep research via the local model network")
async def local_research_deep(question: str, max_rounds: int = 2) -> str:
    """Deep research: the local Qwen3 model plans search queries, runs them
    (ddgs + GitHub/HF + optional google/reddit channels via LR_CHANNELS), fetches
    pages, and synthesizes a CITED brief. Returns JSON:
    {report, sources, queries, rounds, elapsed_s, channels, channel_errors}.

    Use for multi-step research where a single search won't suffice.
    ~30-60s per call. No GLM/rate-limit involvement.

    Args:
        question: the research question.
        max_rounds: max search rounds (default 2; 1 = single pass).
    """
    return await _tool_result(_deep(question, max_rounds))


@_register(mcp, "google_search",
           "Native Google search (anti-detect chromium)")
async def google_search_tool(query: str, n: int = 10) -> str:
    """Google search via an anti-detect chromium channel.
    Returns JSON: [{title, url, snippet}, ...]. Requires playwright + chromium
    browsers installed; otherwise returns a clear 'channel unavailable' message
    (never raises).

    Args:
        query: the search query.
        n: max results (default 10).
    """
    if not channels_available().get("google"):
        return json.dumps({"error": "channel unavailable — install playwright + chromium"},
                          ensure_ascii=False)
    return await _tool_result(google_search(query, n=n))


@_register(mcp, "reddit_search",
           "Reddit search (lazy no-auth, raw JSON)")
async def reddit_search_tool(query: str, n: int = 10) -> str:
    """Reddit search (lazy, no auth) via the public JSON endpoints. Returns
    JSON: [{title, url, permalink, subreddit, score, author}, ...]. On 403/429/
    network failure returns a graceful error message (never raises).

    Args:
        query: the search query.
        n: max results (default 10).
    """
    return await _tool_result(reddit_search(query, n=n))


async def _deep(question: str, max_rounds: int) -> dict:
    from .deep_research import research
    return await research(question, max_rounds=max_rounds)


async def _tool_result(coro) -> str:
    """Await a coroutine and return it JSON-encoded; Google/Reddit channel
    errors become graceful JSON error payloads (never raise at the tool seam)."""
    try:
        res = await coro
    except (GoogleChannelError, RedditChannelError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    return json.dumps(res, ensure_ascii=False)


def main():
    # Stdio transport — Claude spawns this process and talks over stdin/stdout.
    if mcp is None:
        raise SystemExit("mcp package unavailable — install 'mcp' to run the server")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()