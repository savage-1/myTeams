"""Deep-research agent: Qwen3-14B plans + synthesizes, search_client fetches.

Re-homed 2026-09-04 with the
slice-5 upgrade: the agentic loop now also fans out to the NEW research channels
(google/reddit) as ADDITIONAL signals, behind the ``LR_CHANNELS`` config flag
(comma-separated, default "" = disabled). Each channel produces a short
(source, title, url, snippet) digest that the LLM planner can consider in the
same synthesis.

Hard rules:
  * A channel missing its dep (playwright not importable) or erroring MUST NOT
    crash the loop and MUST NOT produce an empty synth — the loop keeps going
    with the remaining channels (graceful degradation).
  * ``max_rounds=0`` disables the LLM loop entirely (offline deterministic path
    used by L1/L2).
"""
from __future__ import annotations

import asyncio
import json
import re
import time

import httpx

from . import config as cfg
from .search_client import web_search, fetch_url, github_search, hf_search, run_channels

MAX_QUERIES = 3
FETCH_PER_QUERY = 2
FETCH_CHARS = 4000
MAX_ROUNDS = 2
SYNTH_MAX_TOKENS = 1800


async def llm_chat(messages: list[dict], max_tokens: int = 800,
                   thinking: bool = False, temperature: float = 0.3) -> str:
    """Call the local mlx-lm Qwen3 model server (OpenAI-compatible)."""
    body = {
        "model": cfg.LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(f"{cfg.LLM_API_BASE}/chat/completions", json=body)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content") or ""


def _extract_json_list(text: str) -> list[str]:
    """Tolerant parse of a JSON string list from the model (may be fenced)."""
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if m:
        try:
            return [str(x) for x in json.loads(m.group(0))]
        except Exception:  # noqa: BLE001
            pass
    # fallback: lines that look like queries
    return [ln.strip().lstrip("-*0123456789. )\"'") for ln in text.splitlines()
            if len(ln.strip()) > 6 and "?" not in ln[:3]][:MAX_QUERIES]


def _looks_codey(q: str) -> bool:
    qf = q.lower()
    return any(k in qf for k in ("github", "repo", "library", "package", "code ", "api ", "python", "javascript",
                                  "implementation", "mlx", "qwen", "model"))


async def research(question: str, max_rounds: int = MAX_ROUNDS,
                   channels: list[str] | None = None) -> dict:
    """Run a bounded deep-research loop. Returns report + sources.

    ``channels=None`` → use cfg.LR_CHANNELS; pass [] to disable the extra
    channels. Channel digests (google/reddit) are added to the source pool the
    LLM synthesizes from; channel errors never stop the loop.
    """
    t0 = time.time()
    sources: list[dict] = []          # {idx, title, url, snippet}
    queries_used: list[str] = []
    report = ""
    channel_errors: list[dict] = []
    round_i = 0                      # max_rounds=0  → offline deterministic path

    active_channels = list(channels) if channels is not None else None

    for round_i in range(1, max_rounds + 1):
        # 1. plan queries (round 1: from the question; later rounds: from gaps)
        if round_i == 1:
            plan_prompt = (
                f"Research question: {question}\n\n"
                f"Output {MAX_QUERIES} concise web-search queries to research this. "
                f"Return ONLY a JSON array of strings, e.g. [\"query 1\", \"query 2\"].")
            queries = _extract_json_list(await llm_chat(
                [{"role": "user", "content": plan_prompt}], max_tokens=200, thinking=False))
        else:
            gap_prompt = (
                f"Question: {question}\nCurrent findings so far:\n{report[:2500]}\n\n"
                f"What is still missing? Output 1-2 follow-up search queries as a JSON array of strings.")
            queries = _extract_json_list(await llm_chat(
                [{"role": "user", "content": gap_prompt}], max_tokens=120, thinking=False))[:2]
        queries_used.extend(queries)
        if not queries:
            queries = [question]

        # 2. run searches (web always; GH/HF if codey) + extra channels
        seen_urls = {s["url"] for s in sources}
        new_hits: list[dict] = []
        codey = _looks_codey(question)
        for q in queries:
            hits = await web_search(q, n=4)
            for h in hits:
                if h.get("url") and h["url"] not in seen_urls:
                    new_hits.append(h); seen_urls.add(h["url"])
            if codey:
                gh = await github_search(q, n=3)
                for g in gh:
                    if g.get("url") and g["url"] not in seen_urls and "error" not in g:
                        new_hits.append({"title": g.get("name", g.get("repo", "")), "url": g["url"],
                                         "snippet": g.get("desc", "")}); seen_urls.add(g["url"])
                hf = await hf_search(q, n=3)
                for h in hf:
                    if "error" not in h and h.get("id"):
                        url = f"https://huggingface.co/{h['id']}"
                        if url not in seen_urls:
                            new_hits.append({"title": h["id"], "url": url,
                                             "snippet": f"downloads={h.get('downloads')} tags={h.get('tags', [])[:4]}",
                                             "kind": "hf"}); seen_urls.add(url)

        # 2b. extra research channels (google/reddit) — graceful degradation
        chan = await run_channels(queries, channels=active_channels)
        channel_errors.extend(chan.get("errors", []))
        for d in chan.get("digests", []):
            url = d.get("url")
            if url and url not in seen_urls:
                new_hits.append({"title": d.get("title", ""), "url": url,
                                 "snippet": d.get("snippet", ""), "source": d.get("source", "")})
                seen_urls.add(url)

        # 3. fetch top hits for full text
        to_fetch = new_hits[:FETCH_PER_QUERY * len(queries)]
        fetched = await asyncio.gather(*[fetch_url(h["url"], max_chars=FETCH_CHARS) for h in to_fetch],
                                       return_exceptions=True)
        for h, f in zip(to_fetch, fetched):
            h["fulltext"] = (f.get("text", "") if isinstance(f, dict) else "")[:FETCH_CHARS]

        # index new sources
        for h in new_hits:
            h.setdefault("idx", len(sources) + 1)
            h["idx"] = len(sources) + 1
            sources.append(h)

        # 4. synthesize
        ctx_parts = []
        for s in sources:
            ctx_parts.append(f"[{s['idx']}] {s.get('title','')}\nURL: {s['url']}\n"
                             f"Snippet: {s.get('snippet','')}\nContent: {s.get('fulltext','')[:2000]}")
        ctx = "\n---\n".join(ctx_parts)[-16000:]
        synth_prompt = (
            f"Research question: {question}\n\nSources (with [idx], URL, snippet, content):\n{ctx}\n\n"
            f"Write a concise, accurate research brief answering the question. "
            f"Cite claims as [idx] matching the source numbers. Include a short "
            f"'Key sources' list at the end. If the answer is INCOMPLETE and needs "
            f"more search, end with: NEEDS_MORE: <what's missing>. Otherwise end with: DONE.")
        report = await llm_chat([{"role": "user", "content": synth_prompt}],
                                max_tokens=SYNTH_MAX_TOKENS, thinking=False, temperature=0.2)

        if "NEEDS_MORE:" not in report or round_i == max_rounds:
            break
        # else one more round targeting the gap

    return {
        "question": question,
        "report": report,
        "sources": [{"idx": s["idx"], "title": s.get("title", ""), "url": s["url"],
                     "snippet": s.get("snippet", "")[:200]} for s in sources],
        "queries": queries_used,
        "rounds": round_i,
        "elapsed_s": round(time.time() - t0, 1),
        "channel_errors": channel_errors,
        "channels": [c for c in (active_channels if active_channels is not None
                                 else [x for x in (cfg.LR_CHANNELS or "").split(",") if x.strip()])],
    }


async def _cli():
    import sys
    q = " ".join(sys.argv[1:]) or "What are the best lightweight local LLMs for tool use on Apple Silicon in 2025?"
    r = await research(q)
    print(json.dumps(r, indent=2, ensure_ascii=False)[:6000])


if __name__ == "__main__":
    asyncio.run(_cli())