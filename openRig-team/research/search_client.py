"""Async search client: ddgs (web) + GitHub REST API + HuggingFace API.

Re-homed 2026-09-04, with the
following upgrades:

  * HF bugfix (slice 2): huggingface_hub 1.26 removed the ``direction`` kwarg
    from ``HfApi.list_models/…list_datasets``. We now version-guard via
    ``inspect.signature`` and only pass ``direction`` when the installed hub
    supports it (fail-closed: never pass it when unknown).
  * Google channel (slice 3): a native Google-SERP port (playwright chromium,
    anti-detect fingerprint, persistent storage-state, random google domain per
    run, CAPTCHA → headful relaunch + human-solve wait, four-selector cascade +
    alternative snippet selectors + anchor fallback). Emits ``GoogleChannelError``
    on hard failure — callers degrade gracefully.
  * Reddit channel (slice 4): lazy no-auth httpx against www.reddit.com JSON
    endpoints (search/top/hot/new + permalink comments) with throttle, custom
    browser-like UA. Emits ``RedditChannelError`` on hard failure (403/429/
    RATE_LIMITED are graceful, never crash).

Hard rules:
  * Google primary domain: google.com selectors are US-valid ONLY (ground-truth
    port).
  * ``google.com`` channel requires playwright installed; if playwright is
    missing at import time the channel is reported "unavailable" by callers
    (never raises at import in this module — import guard is lazy).
  * All network calls are async (httpx) or run sync libs via asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import json
import re
import random
from typing import Iterable
from urllib.parse import quote_plus

import httpx

from . import config as cfg


# ------------------------------------------------------------------ errors
class ChannelError(Exception):
    """Base class for optional research-channel hard failures (raise → caller
    degrades gracefully; the loop keeps going with remaining channels)."""


class GoogleChannelError(ChannelError):
    """Google channel hard failure (playwright missing, no search box, etc)."""


class RedditChannelError(ChannelError):
    """Reddit channel hard failure (network/403/429/rate-limit)."""


# ----------------------------------------------------------------- web (ddgs)
async def web_search(query: str, n: int | None = None) -> list[dict]:
    """DuckDuckGo text search via the `ddgs` lib. Returns [{title, url, snippet}]."""
    n = n or cfg.WEB_MAX_RESULTS

    def _sync():
        from ddgs import DDGS
        out = []
        # ddgs is sync; region=wt-wt = worldwide, safearch=off (we need raw results)
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=n, safesearch="off", region="wt-wt"):
                out.append({
                    "title": r.get("title", ""),
                    "url": r.get("href") or r.get("url") or r.get("link", ""),
                    "snippet": r.get("body") or r.get("snippet", ""),
                })
        return out
    return await asyncio.to_thread(_sync)


# ------------------------------------------------------------- fetch a URL
async def fetch_url(url: str, max_chars: int | None = None) -> dict:
    """Fetch a URL and return readable text (HTML stripped to text). Caps size."""
    max_chars = max_chars or 8000
    headers = {"User-Agent": "Mozilla/5.0 (research-bot; local)"}
    try:
        async with httpx.AsyncClient(timeout=cfg.FETCH_TIMEOUT, follow_redirects=True, headers=headers) as c:
            r = await c.get(url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            body = r.text[: cfg.FETCH_MAX_BYTES]
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": f"{type(e).__name__}: {e}"}

    text = _html_to_text(body) if "html" in ctype or body.lstrip().startswith("<") else body
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"url": url, "content_type": ctype, "text": text[:max_chars], "truncated": len(text) > max_chars}


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
            tag.decompose()
        # preserve some structure
        for br in soup.find_all("br"):
            br.replace_with("\n")
        return soup.get_text("\n", strip=True)
    except Exception:  # noqa: BLE001
        # fallback: crude tag strip
        return re.sub(r"<[^>]+>", " ", html)


# ------------------------------------------------------------- GitHub search
async def github_search(query: str, kind: str = "repositories", n: int | None = None) -> list[dict]:
    """GitHub REST search. kind in {repositories, code, issues, users}. Token optional."""
    n = n or cfg.GH_MAX_RESULTS
    kind = kind if kind in ("repositories", "code", "issues", "users") else "repositories"
    headers = {"Accept": "application/vnd.github+json"}
    if cfg.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {cfg.GITHUB_TOKEN}"
    url = f"https://api.github.com/search/{kind}?q={quote_plus(query)}&per_page={n}"
    try:
        async with httpx.AsyncClient(timeout=cfg.FETCH_TIMEOUT, headers=headers) as c:
            r = await c.get(url)
            if r.status_code == 403:
                return [{"error": "rate-limited (60/hr unauth; set GITHUB_TOKEN for 5000/hr)",
                         "remaining": r.headers.get("X-RateLimit-Remaining", "?")}]
            r.raise_for_status()
            items = r.json().get("items", [])
    except Exception as e:  # noqa: BLE001
        return [{"error": f"{type(e).__name__}: {e}"}]

    out = []
    for it in items:
        if kind == "repositories":
            out.append({"name": it.get("full_name", ""), "url": it.get("html_url", ""),
                        "stars": it.get("stargazers_count", 0), "desc": it.get("description", "") or "",
                        "language": it.get("language") or ""})
        elif kind == "code":
            out.append({"repo": it.get("repository", {}).get("full_name", ""),
                        "path": it.get("path", ""), "url": it.get("html_url", "")})
        elif kind == "issues":
            out.append({"title": it.get("title", ""), "url": it.get("html_url", ""),
                        "state": it.get("state", ""), "comments": it.get("comments", 0)})
        else:  # users
            out.append({"login": it.get("login", ""), "url": it.get("html_url", ""),
                        "type": it.get("type", "")})
    return out


# ----------------------------------------------------------- HuggingFace search
_HF_DIRECTION_SUPPORTED: bool | None = None  # cache of the version probe


def _hf_supports_direction() -> bool:
    """Version-guard: huggingface_hub 1.26 REMOVED ``direction`` from
    list_models/list_datasets. Probe the installed signature ONCE and cache;
    fail-closed to False (never pass the kwarg) on any uncertainty."""
    global _HF_DIRECTION_SUPPORTED
    if _HF_DIRECTION_SUPPORTED is None:
        try:
            import inspect
            from huggingface_hub import HfApi
            _HF_DIRECTION_SUPPORTED = (
                "direction" in inspect.signature(HfApi.list_models).parameters
                and "direction" in inspect.signature(HfApi.list_datasets).parameters
            )
        except Exception:  # noqa: BLE001 — import absent → channel unavailable
            _HF_DIRECTION_SUPPORTED = False
    return _HF_DIRECTION_SUPPORTED


async def hf_search(query: str, kind: str = "models", n: int | None = None) -> list[dict]:
    """HuggingFace search via huggingface_hub. kind in {models, datasets, spaces}.

    Bugfix (slice 2): ``sort="downloads"`` is kept; ``direction`` is only passed
    when the installed huggingface_hub supports it (1.25 and earlier). On 1.26+
    (where it was removed) sorting falls back to the API default (downloads
    desc) — matching the original intent.
    """
    n = n or cfg.HF_MAX_RESULTS
    kind = kind if kind in ("models", "datasets", "spaces") else "models"

    def _sync():
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=cfg.HF_TOKEN or None)
            if kind == "models":
                kwargs = {"search": query, "limit": n, "sort": "downloads"}
                if _hf_supports_direction():
                    kwargs["direction"] = -1
                res = api.list_models(**kwargs)
                return [{"id": m.id, "downloads": getattr(m, "downloads", 0),
                         "likes": getattr(m, "likes", 0), "tags": list(getattr(m, "tags", []))[:8]}
                        for m in res]
            if kind == "datasets":
                kwargs = {"search": query, "limit": n, "sort": "downloads"}
                if _hf_supports_direction():
                    kwargs["direction"] = -1
                res = api.list_datasets(**kwargs)
                return [{"id": d.id, "downloads": getattr(d, "downloads", 0),
                         "likes": getattr(d, "likes", 0)} for d in res]
            res = api.list_spaces(search=query, limit=n)
            return [{"id": s.id, "likes": getattr(s, "likes", 0)} for s in res]
        except Exception as e:  # noqa: BLE001
            return [{"error": f"{type(e).__name__}: {e}"}]
    return await asyncio.to_thread(_sync)


# ------------------------------------------------------------- Google channel
_GOOGLE_SORRY_PATTERNS = ("google.com/sorry", "recaptcha", "captcha", "unusual traffic")
_GOOGLE_SEARCH_BOX_SELECTORS = (
    "textarea[name='q']",
    "input[name='q']",
    "textarea[title='Search']",
    "input[title='Search']",
    "textarea[aria-label='Search']",
    "input[aria-label='Search']",
    "textarea",
)
# Ground-truth Google-SERP extraction selectors. The
# deprecated `.result` / `.result__title` / `.result__snippet` selectors are
# WRONG (CR hallucination) — never use them.
_GOOGLE_RESULT_SELECTOR_SETS = (
    ("#search div[data-hveid]", "h3", ".VwiC3b"),
    ("#rso div[data-hveid]", "h3", "[data-sncf='1']"),
    (".g", "h3", "div[style*='webkit-line-clamp']"),
    ("div[jscontroller][data-hveid]", "h3", "div[role='text']"),
)
_GOOGLE_ALT_SNIPPET_SELECTORS = (".VwiC3b", "[data-sncf='1']",
                                 "div[style*='webkit-line-clamp']", "div[role='text']")
# Persistent storage-state file (keeps signed-in/consent cookies).
_GOOGLE_STATE_FILE = cfg.LR_GOOGLE_STATE_FILE

# Anti-detect chromium arg list (mcp-server.ts:115-145 ground truth).
_GOOGLE_ANTIDETECT_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
    "--disable-web-security",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-accelerated-2d-canvas",
    "--no-first-run",
    "--no-zygote",
    "--disable-gpu",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-extensions",
    "--disable-features=TranslateUI",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
]

# The page.evaluate extraction body (fixed; mirrors search.ts:793-923).
_GOOGLE_EXTRACT_JS = r"""
(maxResults) => {
  const results = [];
  const seenUrls = new Set();
  const selectorSets = [
    { container: '#search div[data-hveid]', title: 'h3', snippet: '.VwiC3b' },
    { container: '#rso div[data-hveid]', title: 'h3', snippet: '[data-sncf="1"]' },
    { container: '.g', title: 'h3', snippet: 'div[style*="webkit-line-clamp"]' },
    { container: 'div[jscontroller][data-hveid]', title: 'h3', snippet: 'div[role="text"]' }
  ];
  const alternativeSnippetSelectors = ['.VwiC3b', '[data-sncf="1"]', 'div[style*="webkit-line-clamp"]', 'div[role="text"]'];
  for (const selectors of selectorSets) {
    if (results.length >= maxResults) break;
    const containers = document.querySelectorAll(selectors.container);
    for (const container of containers) {
      if (results.length >= maxResults) break;
      const titleElement = container.querySelector(selectors.title);
      if (!titleElement) continue;
      const title = (titleElement.textContent || '').trim();
      let link = '';
      const linkInTitle = titleElement.querySelector('a');
      if (linkInTitle) {
        link = linkInTitle.href;
      } else {
        let current = titleElement;
        while (current && current.tagName !== 'A') current = current.parentElement;
        if (current && current.href !== undefined) link = current.href;
        else {
          const containerLink = container.querySelector('a');
          if (containerLink) link = containerLink.href;
        }
      }
      if (!link || !link.startsWith('http') || seenUrls.has(link)) continue;
      let snippet = '';
      const snippetElement = container.querySelector(selectors.snippet);
      if (snippetElement) {
        snippet = (snippetElement.textContent || '').trim();
      } else {
        for (const altSelector of alternativeSnippetSelectors) {
          const element = container.querySelector(altSelector);
          if (element) { snippet = (element.textContent || '').trim(); break; }
        }
        if (!snippet) {
          const textNodes = Array.from(container.querySelectorAll('div')).filter(el =>
            !el.querySelector('h3') && (el.textContent || '').trim().length > 20
          );
          if (textNodes.length > 0) snippet = (textNodes[0].textContent || '').trim();
        }
      }
      if (title && link) { results.push({ title, link, snippet }); seenUrls.add(link); }
    }
  }
  if (results.length < maxResults) {
    const anchorElements = Array.from(document.querySelectorAll("a[href^='http']"));
    for (const el of anchorElements) {
      if (results.length >= maxResults) break;
      const link = el.href;
      if (!link || seenUrls.has(link) || link.includes('google.com/') || link.includes('accounts.google') || link.includes('support.google')) continue;
      const title = (el.textContent || '').trim();
      if (!title) continue;
      let snippet = '';
      let parent = el.parentElement;
      for (let i = 0; i < 3 && parent; i++) {
        const text = (parent.textContent || '').trim();
        if (text.length > 20 && text !== title) { snippet = text; break; }
        parent = parent.parentElement;
      }
      results.push({ title, link, snippet });
      seenUrls.add(link);
    }
  }
  return results.slice(0, maxResults);
}
"""


def _google_available() -> bool:
    """Lazy import-probe for the playwright dependency (channel availability)."""
    try:
        import playwright  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _is_sorry_url(url: str) -> bool:
    return any(p in (url or "") for p in _GOOGLE_SORRY_PATTERNS)


def _random_google_domain() -> str:
    """Pick a random google domain and return a navigation-ready URL.

    Config values may be either bare hosts (``www.google.com``) or fully
    schemed (``https://www.google.com``) — normalise so we never double-scheme.
    """
    domain = random.choice(cfg.LR_GOOGLE_DOMAINS).strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    return domain


async def google_search(query: str, n: int | None = None,
                        _launch=None) -> list[dict]:
    """Native Google search channel (Google-SERP port). Anti-detect chromium,
    persistent storage-state, random google domain per run, CAPTCHA → headful
    relaunch + human-solve wait, four-selector cascade extraction.

    Args:
        query: the search query.
        n: max results (default LR_GOOGLE_MAX_RESULTS).
        _launch: internal; injectable launcher for deterministic offline L1.

    Raises:
        GoogleChannelError: on hard failure (playwright missing, no search
        box, no results container). Callers MUST catch and degrade gracefully.
    """
    n = n or cfg.LR_GOOGLE_MAX_RESULTS
    if not _google_available():
        raise GoogleChannelError("channel unavailable — install playwright")

    try:
        from playwright.async_api import async_playwright
    except Exception as e:  # noqa: BLE001
        raise GoogleChannelError(f"channel unavailable — install playwright: {type(e).__name__}") from e

    launch_fn = _launch or (lambda p, headless: p.chromium.launch(
        headless=headless,
        args=list(_GOOGLE_ANTIDETECT_ARGS),
        ignore_default_args=["--enable-automation"],
    ))

    async def perform(headless: bool) -> list[dict]:
        pw = await async_playwright().start()
        browser = await launch_fn(pw, headless)
        context_opts = {}
        if _path_exists(_GOOGLE_STATE_FILE):
            context_opts["storage_state"] = _GOOGLE_STATE_FILE
        context = await browser.new_context(**context_opts)
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
            "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
            "window.chrome={runtime:{},loadTimes:function(){},csi:function(){},app:{}};"
            "if(typeof WebGLRenderingContext!=='undefined'){const gp=WebGLRenderingContext.prototype.getParameter;"
            "WebGLRenderingContext.prototype.getParameter=function(p){if(p===37445)return'Intel Inc.';"
            "if(p===37446)return'Intel Iris OpenGL Engine';return gp.call(this,p);};}"
        )
        await context.add_init_script(
            "Object.defineProperty(window.screen,'width',{get:()=>1920});"
            "Object.defineProperty(window.screen,'height',{get:()=>1080});"
            "Object.defineProperty(window.screen,'colorDepth',{get:()=>24});"
            "Object.defineProperty(window.screen,'pixelDepth',{get:()=>24});"
        )
        page = await context.new_page()
        timeout = cfg.LR_GOOGLE_TIMEOUT_MS
        try:
            domain = _random_google_domain()
            await page.goto(domain, timeout=timeout, wait_until="domcontentloaded")
            if _is_sorry_url(page.url):
                # CAPTCHA: headless → relaunch headful; headful → wait for human solve
                if headless:
                    await browser.close(); await pw.stop()
                    return await perform(False)
                try:
                    await page.wait_for_url(
                        lambda u: not _is_sorry_url(u),
                        timeout=timeout * 2,
                        wait_until="domcontentloaded",
                    )
                except Exception:  # noqa: BLE001
                    raise GoogleChannelError("CAPTCHA not solved in time")

            search_input = None
            for selector in _GOOGLE_SEARCH_BOX_SELECTORS:
                search_input = await page.query_selector(selector)
                if search_input:
                    break
            if not search_input:
                raise GoogleChannelError("search box not found (consent/geo gate?)")
            await search_input.click()
            await page.keyboard.type(query, delay=random.randint(10, 30))
            await page.wait_for_timeout(random.randint(100, 300))
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)

            if _is_sorry_url(page.url):
                if headless:
                    await browser.close(); await pw.stop()
                    return await perform(False)
                try:
                    await page.wait_for_url(
                        lambda u: not _is_sorry_url(u),
                        timeout=timeout * 2,
                        wait_until="domcontentloaded",
                    )
                except Exception:  # noqa: BLE001
                    raise GoogleChannelError("CAPTCHA (after search) not solved in time")

            # wait for a results container then extract
            found_container = False
            for selector in ("#search", "#rso", ".g", "div[role='main']"):
                try:
                    await page.wait_for_selector(selector, timeout=timeout // 2)
                    found_container = True
                    break
                except Exception:  # noqa: BLE001
                    continue
            if not found_container:
                raise GoogleChannelError("no results container found")
            await page.wait_for_timeout(random.randint(200, 500))
            extracted = await page.evaluate(_GOOGLE_EXTRACT_JS, n)
            results = []
            for r in extracted:
                results.append({"title": r.get("title", ""), "url": r.get("link", ""),
                                "snippet": r.get("snippet", ""), "channel": "google"})
            # persist state for next run
            try:
                await context.storage_state(path=_GOOGLE_STATE_FILE)
            except Exception:  # noqa: BLE001
                pass
            return results[:n]
        finally:
            await browser.close()
            await pw.stop()

    try:
        return await perform(True)
    except GoogleChannelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise GoogleChannelError(f"{type(e).__name__}: {e}") from e


def _path_exists(path: str) -> bool:
    try:
        import os
        return os.path.exists(path)
    except Exception:  # noqa: BLE001
        return False


def parse_google_serp(html: str, n: int = 10) -> list[dict]:
    """Pure-Python (bs4/lxml) mirror of ``_GOOGLE_EXTRACT_JS`` — the offline
    seam used by L1. Walks the same four-selector cascade + alternative snippet
    order + anchor fallback over canned SERP HTML, so offline fixtures verify
    the extraction semantics WITHOUT a browser.

    Returns [{title, url, snippet}] up to ``n``. Argument names follow the JS:
    each set is (container, title, snippet) selector.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        return []
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen_urls: set[str] = set()
    selector_sets = [
        ("#search div[data-hveid]", "h3", ".VwiC3b"),
        ("#rso div[data-hveid]", "h3", "[data-sncf='1']"),
        (".g", "h3", "div[style*='webkit-line-clamp']"),
        ("div[jscontroller][data-hveid]", "h3", "div[role='text']"),
    ]
    alt_snippets = [".VwiC3b", "[data-sncf='1']",
                    "div[style*='webkit-line-clamp']", "div[role='text']"]

    def _link_for(title_el, container):
        """Mirror of the JS link resolution: inner <a> first, then climb to an
        enclosing <a>, then container-level <a> fallback."""
        inner = title_el.find("a")
        if inner is not None and inner.get("href"):
            return inner["href"]
        node = title_el
        while node is not None and node.name != "a":
            node = node.parent
        if node is not None and node.get("href"):
            return node["href"]
        container_link = container.select_one("a")
        if container_link is not None and container_link.get("href"):
            return container_link["href"]
        return ""

    def _snippet_for(container):
        for sel in selector_sets:
            sn = container.select_one(sel[2])
            if sn:
                text = sn.get_text(" ", strip=True)
                if text:
                    return text
        for sel in alt_snippets:
            el = container.select_one(sel)
            if el:
                text = el.get_text(" ", strip=True)
                if text:
                    return text
        # text-node fallback: first div without h3 and len>20
        for div in container.find_all("div"):
            if div.find("h3") is None:
                text = div.get_text(" ", strip=True)
                if len(text) > 20:
                    return text
        return ""

    for container_sel, title_sel, _snippet_sel in selector_sets:
        if len(results) >= n:
            break
        for container in soup.select(container_sel):
            if len(results) >= n:
                break
            title_el = container.select_one(title_sel)
            if title_el is None:
                continue
            title = title_el.get_text(" ", strip=True).strip()
            link = _link_for(title_el, container)
            if not link or not link.startswith("http") or link in seen_urls:
                continue
            snippet = _snippet_for(container)
            if title and link:
                results.append({"title": title, "url": link, "snippet": snippet})
                seen_urls.add(link)

    # anchor fallback (exclude google/accounts/support links)
    if len(results) < n:
        for a in soup.select("a[href^='http']"):
            if len(results) >= n:
                break
            link = a.get("href", "")
            if link in seen_urls or "google.com/" in link or "accounts.google" in link or "support.google" in link:
                continue
            title = a.get_text(" ", strip=True).strip()
            if not title:
                continue
            snippet = ""
            parent = a.parent
            for _i in range(3):
                if parent is None:
                    break
                text = parent.get_text(" ", strip=True).strip() if isinstance(parent, object) else ""
                if len(text) > 20 and text != title:
                    snippet = text
                    break
                parent = parent.parent if parent is not None else None
            if title and link:
                results.append({"title": title, "url": link, "snippet": snippet})
                seen_urls.add(link)
    return results[:n]


# -------------------------------------------------------------- Reddit channel
_REDDIT_BASE = "https://www.reddit.com"
_REDDIT_HEADERS = {"User-Agent": cfg.LR_REDDIT_UA, "Accept": "application/json"}


async def _reddit_get(client: httpx.AsyncClient, url: str) -> dict:
    """GET a Reddit JSON endpoint; 403/429/RATE_LIMITED → RedditChannelError."""
    await asyncio.sleep(random.uniform(*cfg.LR_REDDIT_THROTTLE_MS) / 1000.0)
    try:
        r = await client.get(url)
    except Exception as e:  # noqa: BLE001
        raise RedditChannelError(f"{type(e).__name__}: {e}") from e
    if r.status_code in (403, 429) or "RATE_LIMITED" in r.text[:200].upper():
        raise RedditChannelError(f"http {r.status_code} / rate-limited")
    r.raise_for_status()
    return r.json()


def _reddit_post_to_dict(post: dict) -> dict:
    d = post.get("data", {}) if isinstance(post, dict) else {}
    return {
        "title": d.get("title", ""),
        "url": d.get("url", ""),
        "permalink": d.get("permalink", ""),
        "selftext": (d.get("selftext") or "")[:1000],
        "subreddit": d.get("subreddit", ""),
        "score": d.get("score", 0),
        "author": d.get("author", ""),
        "total_awards_received": d.get("total_awards_received", 0),
    }


def _reddit_listing_items(payload: dict) -> list[dict]:
    """Pull the t3 CHILD WRAPPERS ({kind, data}) from a Reddit listing JSON, so
    callers pass them to ``_reddit_post_to_dict`` (which expects the wrapper and
    unwraps ``data``). Non-t3 children (t1 comments, "more") are skipped."""
    out = []
    try:
        children = payload["data"]["children"]
    except Exception:  # noqa: BLE001
        return out
    for child in children or []:
        if isinstance(child, dict) and child.get("kind") == "t3" and isinstance(child.get("data"), dict):
            out.append(child)
    return out


async def reddit_search(query: str, n: int | None = None) -> list[dict]:
    """Reddit search (lazy, no auth): GET /search.json?q=..&limit=n (raw JSON).

    Returns [{title, url, permalink, subreddit, score, author, ...}]. Raises
    RedditChannelError on hard failure (403/429/rate-limit/network).
    """
    n = n or cfg.LR_REDDIT_MAX_RESULTS
    async with httpx.AsyncClient(timeout=cfg.LR_REDDIT_TIMEOUT,
                                 headers=_REDDIT_HEADERS, follow_redirects=True) as c:
        payload = await _reddit_get(c, f"{_REDDIT_BASE}/search.json?q={quote_plus(query)}&limit={n}")
    out = []
    for post in _reddit_listing_items(payload)[:n]:
        d = _reddit_post_to_dict(post)
        d["channel"] = "reddit"
        out.append(d)
    return out


async def reddit_subreddit_posts(subreddit: str, category: str = "top",
                                 time_filter: str = "week", n: int | None = None) -> list[dict]:
    """Subreddit listing (top/hot/new) with optional time_filter (t=)."""
    n = n or cfg.LR_REDDIT_MAX_RESULTS
    category = category if category in ("top", "hot", "new") else "top"
    url = f"{_REDDIT_BASE}/r/{subreddit}/{category}.json?limit={n}"
    if category == "top":
        url += f"&t={time_filter}"
    async with httpx.AsyncClient(timeout=cfg.LR_REDDIT_TIMEOUT,
                                 headers=_REDDIT_HEADERS, follow_redirects=True) as c:
        payload = await _reddit_get(c, url)
    out = []
    for post in _reddit_listing_items(payload)[:n]:
        d = _reddit_post_to_dict(post)
        d["channel"] = "reddit"
        out.append(d)
    return out


def _first_listing_post(payload: list) -> dict:
    """[0] of ``{permalink}.json`` is a Listing whose first t3 child is the post."""
    try:
        children = payload[0]["data"]["children"]
    except Exception:  # noqa: BLE001
        return {}
    for child in children or []:
        if isinstance(child, dict) and child.get("kind") == "t3" and isinstance(child.get("data"), dict):
            return _reddit_post_to_dict(child)
    return {}


def _parse_post_comments(payload: list, n_comments: int = 20) -> dict:
    """Pure offline mirror of reddit_post_comments' parse: payload = the JSON of
    ``{permalink}.json`` → [0] post (Listing), [1] comments Listing. Returns
    {post: {...}, comments: [{author, body, score}]} with NO network (used by
    the live fn AND by offline L1 fixtures)."""
    post = _first_listing_post(payload)
    comments = []
    if len(payload) > 1 and isinstance(payload[1], dict):
        try:
            children = payload[1]["data"]["children"]
        except Exception:  # noqa: BLE001
            children = []
        for child in children or []:
            d = child.get("data", {}) if isinstance(child, dict) else {}
            if child.get("kind") != "t1":
                continue
            comments.append({"author": d.get("author", ""),
                             "body": (d.get("body") or "")[:1000],
                             "score": d.get("score", 0)})
            if len(comments) >= n_comments:
                break
    return {"post": post, "comments": comments}


async def reddit_post_comments(permalink: str, n_comments: int = 20) -> dict:
    """Post + comments: GET {permalink}.json → [0] post, [1] comments listing.

    Returns {post: {...}, comments: [{author, body, score}], channel: 'reddit'}.
    """
    permalink = permalink.lstrip("/")
    async with httpx.AsyncClient(timeout=cfg.LR_REDDIT_TIMEOUT,
                                 headers=_REDDIT_HEADERS, follow_redirects=True) as c:
        payload = await _reddit_get(c, f"{_REDDIT_BASE}/{permalink}.json")
    parsed = _parse_post_comments(payload, n_comments)
    parsed["channel"] = "reddit"
    return parsed


# ------------------------------------------------- channel registry (slice 5)
_CHANNEL_DIGESTERS = {
    # name -> async (query) -> list[(source, title, url, snippet)]
    "google": lambda q: google_search(q),
    "reddit": lambda q: reddit_search(q),
}

_MAX_QUERIES_PER_CHANNEL = 2


async def run_channels(queries: Iterable[str], channels: Iterable[str] | None = None,
                       cap: int | None = None,
                       _digesters: dict | None = None) -> dict:
    """Run the optional research channels (google/reddit) for a batch of queries.

    Graceful-degradation contract: a missing dep or a hard channel error is
    recorded as an error entry, NEVER raised — the deep loop keeps going with the
    remaining channels. Returns {"digests": [...], "errors": [...], "count": N}.
    Each digest is a short (source, title, url, snippet) tuple-dict the LLM
    planner can consider in the same synthesis.

    This is ALSO the offline entry point: pass ``channels=[]`` to disable and
    get a deterministic empty result. ``_digesters`` lets offline L1 fixtures
    inject fake per-channel digesters (name → async (q) -> list[dict]) instead
    of launching a real browser / hitting the network; defaults to the real
    registry ``_CHANNEL_DIGESTERS``.
    """
    registry = _CHANNEL_DIGESTERS if _digesters is None else _digesters
    cap = cap or cfg.LR_CHANNEL_RUN_CAP
    selected = _resolve_channels(channels)
    if not selected:
        return {"digests": [], "errors": [], "count": 0, "channels": []}
    digests: list[dict] = []
    errors: list[dict] = []
    coros = []
    for ch in selected:
        digester = registry.get(ch)
        if digester is None:
            errors.append({"channel": ch, "error": "unknown channel"})
            continue
        for q in list(queries)[:_MAX_QUERIES_PER_CHANNEL]:
            coros.append((ch, digester(q)))
    for ch, coro in coros:
        try:
            hits = await coro
            for h in hits or []:
                if isinstance(h, dict) and h.get("url"):
                    digests.append({
                        "source": ch,
                        "title": h.get("title", ""),
                        "url": h["url"],
                        "snippet": h.get("snippet", h.get("desc", ""))[:250],
                    })
        except GoogleChannelError as e:
            errors.append({"channel": ch, "error": f"GoogleChannelError: {e}"})
        except RedditChannelError as e:
            errors.append({"channel": ch, "error": f"RedditChannelError: {e}"})
        except Exception as e:  # noqa: BLE001
            errors.append({"channel": ch, "error": f"{type(e).__name__}: {e}"})
    # dedupe by url
    seen: set[str] = set()
    dedup: list[dict] = []
    for d in digests:
        if d["url"] not in seen:
            seen.add(d["url"])
            dedup.append(d)
    return {"digests": dedup[:cap], "errors": errors, "count": len(dedup[:cap]),
            "channels": selected}


def _resolve_channels(channels: Iterable[str] | None) -> list[str]:
    if channels is None:
        raw = cfg.LR_CHANNELS
    else:
        raw = ",".join(list(channels)) if not isinstance(channels, str) else channels
    seen: set[str] = set()
    out: list[str] = []
    for ch in raw.split(","):
        ch = ch.strip().lower()
        if ch and ch in ("google", "reddit") and ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out


def channels_available() -> dict:
    """Report channel availability (import probes — used by MCP tools)."""
    return {
        "google": _google_available(),
        "reddit": True,  # httpx only; can still network-error at call time
    }


# ------------------------------------------------------------------- helpers
async def health() -> dict:
    """Quick reachability check for each backend (used by verify.sh)."""
    out = {"web": "unknown", "github": "unknown", "huggingface": "unknown",
           "llm": "unknown", "google": "unknown", "reddit": "unknown"}
    try:
        r = await web_search("test", n=1)
        out["web"] = "ok" if r and not (len(r) == 1 and "error" in r[0]) else f"warn:{r}"
    except Exception as e:  # noqa: BLE001
        out["web"] = f"err:{type(e).__name__}"
    try:
        r = await github_search("mlx-lm", n=1)
        out["github"] = "ok" if r and "error" not in r[0] else f"warn:{r[0].get('error') if r else 'empty'}"
    except Exception as e:  # noqa: BLE001
        out["github"] = f"err:{type(e).__name__}"
    try:
        r = await hf_search("qwen3", n=1)
        out["huggingface"] = "ok" if r and "error" not in r[0] else "ok-empty"
    except Exception as e:  # noqa: BLE001
        out["huggingface"] = f"err:{type(e).__name__}"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{cfg.LLM_API_BASE}/models")
            out["llm"] = "ok" if r.status_code == 200 else f"status:{r.status_code}"
    except Exception as e:  # noqa: BLE001
        out["llm"] = f"err:{type(e).__name__}"
    out["google"] = "available" if _google_available() else "unavailable"
    out["reddit"] = "available"
    return out


# CLI: python -m research.search_client <cmd> <args...>
async def _cli():
    import sys
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "web":
        print(json.dumps(await web_search(" ".join(sys.argv[2:])), indent=2, ensure_ascii=False))
    elif cmd == "fetch":
        print(json.dumps(await fetch_url(sys.argv[2]), indent=2, ensure_ascii=False)[:4000])
    elif cmd == "gh":
        q = sys.argv[2]; kind = sys.argv[3] if len(sys.argv) > 3 else "repositories"
        print(json.dumps(await github_search(q, kind), indent=2, ensure_ascii=False))
    elif cmd == "hf":
        q = sys.argv[2]; kind = sys.argv[3] if len(sys.argv) > 3 else "models"
        print(json.dumps(await hf_search(q, kind), indent=2, ensure_ascii=False))
    elif cmd == "google":
        print(json.dumps(await google_search(" ".join(sys.argv[2:])), indent=2, ensure_ascii=False))
    elif cmd == "reddit":
        print(json.dumps(await reddit_search(" ".join(sys.argv[2:])), indent=2, ensure_ascii=False))
    elif cmd == "health":
        print(json.dumps(await health(), indent=2))
    else:
        print(f"unknown cmd: {cmd}. cmds: web|fetch|gh|hf|google|reddit|health")


if __name__ == "__main__":
    asyncio.run(_cli())