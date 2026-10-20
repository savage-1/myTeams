"""CLI for the re-homed research package.

Self-invocable + pipelineable (pipeline convention):

    export PYTHONPATH=<openRig-team dir>
    <venv>/bin/python -m research.cli web "query"
    <venv>/bin/python -m research.cli fetch <url>
    <venv>/bin/python -m research.cli gh  "query" [kind]
    <venv>/bin/python -m research.cli hf  "query" [kind]
    <venv>/bin/python -m research.cli google "query"
    <venv>/bin/python -m research.cli reddit "query"
    <venv>/bin/python -m research.cli channels [google,reddit]
    <venv>/bin/python -m research.cli deep "question" [--max-rounds N]
    <venv>/bin/python -m research.cli health
    <venv>/bin/python -m research.cli config

Every subcommand prints JSON to stdout (no log noise). ``run(action, **kw)`` is
the programmatic entry point (pipelineable), returning a Python dict.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


def _ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return a running loop (new one if needed) for CLI coroutines."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _run_coro(coro):
    loop = _ensure_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        # do not close a loop that may be shared by the test harness
        pass


async def run(action: str, **kw):
    """Programmatic entry point (pipelineable). Returns a Python dict.

    Supported actions: web, fetch, gh, hf, google, reddit, channels, deep,
    health, config. Unknown action → {"error": ...}.
    """
    from . import search_client as SC
    from . import deep_research as DR
    from . import config as CFG

    a = action.lower()
    if a == "web":
        return await SC.web_search(kw.get("query", kw.get("q", "")), n=kw.get("n"))
    if a == "fetch":
        return await SC.fetch_url(kw.get("url", ""), max_chars=kw.get("max_chars"))
    if a == "gh":
        return await SC.github_search(kw.get("query", kw.get("q", "")),
                                      kind=kw.get("kind", "repositories"),
                                      n=kw.get("n"))
    if a == "hf":
        return await SC.hf_search(kw.get("query", kw.get("q", "")),
                                  kind=kw.get("kind", "models"),
                                  n=kw.get("n"))
    if a == "google":
        try:
            return await SC.google_search(kw.get("query", kw.get("q", "")), n=kw.get("n"))
        except SC.GoogleChannelError as e:
            return {"error": str(e), "channel": "google"}
    if a == "reddit":
        try:
            return await SC.reddit_search(kw.get("query", kw.get("q", "")), n=kw.get("n"))
        except SC.RedditChannelError as e:
            return {"error": str(e), "channel": "reddit"}
    if a == "channels":
        return await SC.run_channels(
            [kw.get("query", "")] if kw.get("query") else [],
            channels=kw.get("channels"),
            cap=kw.get("cap"),
        )
    if a == "deep":
        return await DR.research(kw.get("question", kw.get("query", "")),
                                 max_rounds=kw.get("max_rounds", 2),
                                 channels=kw.get("channels"))
    if a == "health":
        return await SC.health()
    if a == "config":
        return {
            "channels": CFG.LR_CHANNELS,
            "google_domains": CFG.LR_GOOGLE_DOMAINS,
            "google_state_file": CFG.LR_GOOGLE_STATE_FILE,
            "google_timeout_ms": CFG.LR_GOOGLE_TIMEOUT_MS,
            "reddit_throttle_ms": list(CFG.LR_REDDIT_THROTTLE_MS),
            "reddit_timeout": CFG.LR_REDDIT_TIMEOUT,
            "llm_api_base": CFG.LLM_API_BASE,
            "llm_model": CFG.LLM_MODEL,
            "help": CFG.cfg_help(),
        }
    if a == "report":
        import contextlib
        from gpt_researcher import GPTResearcher
        topic = kw.get("topic", kw.get("query", ""))
        rtype = kw.get("report_type", "research_report")
        researcher = GPTResearcher(query=topic, report_type=rtype)
        # gpt-researcher color-prints the report to stdout; keep stdout JSON-only.
        with contextlib.redirect_stdout(sys.stderr):
            report = await researcher.write_report()
        return {"topic": topic, "report_type": rtype, "report": report}
    if a == "yt":
        import subprocess, pathlib
        ytt = pathlib.Path(sys.executable).parent / "ytt"
        r = subprocess.run([str(ytt), "transcript", kw.get("url", "")],
                           capture_output=True, text=True, timeout=60)
        return {"url": kw.get("url", ""), "transcript": r.stdout,
                "error": r.stderr.strip() if r.returncode else None}
    return {"error": f"unknown action: {action}. try web|fetch|gh|hf|google|reddit|channels|deep|report|yt|health|config"}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="research.cli",
                                description="Local-research worker CLI (JSON stdout).")
    sub = p.add_subparsers(dest="action", required=True)

    sub.add_parser("web").add_argument("query")
    s = sub.add_parser("fetch"); s.add_argument("url"); s.add_argument("--max-chars", type=int)
    s = sub.add_parser("gh"); s.add_argument("query"); s.add_argument("kind", nargs="?", default="repositories")
    s = sub.add_parser("hf"); s.add_argument("query"); s.add_argument("kind", nargs="?", default="models")
    s = sub.add_parser("google"); s.add_argument("query")
    s = sub.add_parser("reddit"); s.add_argument("query")
    s = sub.add_parser("channels"); s.add_argument("channels", nargs="?", default=None)
    s = sub.add_parser("deep"); s.add_argument("question"); s.add_argument("--max-rounds", type=int, default=2)
    s = sub.add_parser("report"); s.add_argument("topic"); s.add_argument("--report-type", default="research_report")
    s = sub.add_parser("yt"); s.add_argument("url")
    sub.add_parser("health")
    sub.add_parser("config")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    kw = vars(args)
    action = kw.pop("action")
    try:
        result = _run_coro(run(action, **kw))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())