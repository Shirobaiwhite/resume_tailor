import html
import re
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Confirm callback: receives (text, method_label) and returns True if user accepts.
ConfirmCallback = Callable[[str, str], bool]


@dataclass
class JobDescription:
    url: str
    text: str
    slug: str


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:80] or "job"


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/").replace("/", "-")
    return _slugify(f"{host}-{path}") if path else _slugify(host)


def _fetch(url: str, ua: str = USER_AGENT, timeout: float = 20.0) -> Optional[str]:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": ua, "Accept": "text/html,*/*"},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return None


def _clean_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_default(html: str) -> str:
    """Strip site chrome, return all visible body text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    return _clean_lines(soup.get_text("\n"))


# Selectors commonly used by job boards / employers for the JD content.
# Tried in order; the first match with substantial text wins.
_STRUCTURED_SELECTORS = [
    '[itemprop="description"]',          # schema.org JobPosting
    '[data-testid="job-description"]',
    '[data-automation-id*="description"]',  # Workday
    ".job-description",
    "#job-description",
    ".posting-content",                  # Lever
    ".show-more-less-html__markup",      # LinkedIn
    "#content",                          # Greenhouse sometimes
    "main",
    "article",
]


def _extract_structured(html: str) -> str:
    """Look for the actual JD container by selector, ignoring everything else
    on the page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for sel in _STRUCTURED_SELECTORS:
        node = soup.select_one(sel)
        if node:
            text = _clean_lines(node.get_text("\n"))
            if len(text) >= 200:
                return text
    return ""


MAX_LLM_INPUT_CHARS = 15000


def _extract_with_llm(raw_text: str, client) -> str:
    """Ask the configured LLM to keep only the actual JD content, dropping
    company boilerplate, benefits lists, legal statements, navigation, etc.
    Returns empty string if extraction fails or the page has no real JD."""
    from .prompts import JD_EXTRACT_SYSTEM

    text = raw_text[:MAX_LLM_INPUT_CHARS]
    user_msg = f"Raw scraped page text:\n\n<page>\n{text}\n</page>"
    try:
        result = client.complete(
            system=JD_EXTRACT_SYSTEM,
            cached_context="",
            user=user_msg,
        ).strip()
    except Exception as e:
        print(f"  LLM extraction failed: {e}")
        return ""
    if result == "NO_JD_FOUND":
        print("  LLM says: this page doesn't contain a real job description")
        print("  (likely JS-rendered or behind a login wall — only chrome was scraped).")
        return ""
    if len(result) < 100:
        print(f"  LLM returned only {len(result)} chars — too thin, skipping.")
        return ""
    return result


# --- Known-job-board direct extraction ----------------------------------------
# Many job boards (Greenhouse, Lever, Ashby) load JD content via JavaScript, so
# scraping the HTML returns only the wrapper page. But they also expose public
# JSON APIs with clean JD content — use them directly when we detect the URL.

def _extract_greenhouse(url: str) -> str:
    """Greenhouse posts a public JSON API for every job. The site URL can be:
      - https://boards.greenhouse.io/<company>/jobs/<jid>
      - https://<anything>?gh_jid=<jid>  (Greenhouse embedded on a company site)
    For the second form we have to guess the company slug from the URL host."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    # Direct Greenhouse URL.
    m = re.match(r"^/(?:embed/)?([^/]+)/jobs/(\d+)", parsed.path)
    if "greenhouse.io" in parsed.netloc and m:
        return _fetch_greenhouse_api(m.group(1), m.group(2))

    # Greenhouse embedded on a company-branded careers site.
    jid_list = qs.get("gh_jid") or qs.get("gh_jid[]") or []
    if not jid_list:
        return ""
    jid = jid_list[0]

    host = parsed.netloc.lower().replace("www.", "")
    candidates = []
    for suffix in (".careers", ".jobs", ".com"):
        if host.endswith(suffix):
            candidates.append(host[: -len(suffix)].replace(".", "-"))
            break
    # First subdomain segment is also a common pattern.
    first = host.split(".")[0]
    if first not in candidates:
        candidates.append(first)

    for slug in candidates:
        text = _fetch_greenhouse_api(slug, jid)
        if text:
            return text
    return ""


def _fetch_greenhouse_api(company: str, jid: str) -> str:
    api = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{jid}"
    try:
        r = requests.get(api, timeout=10)
        if r.status_code != 200:
            return ""
        data = r.json()
    except Exception:
        return ""
    title = data.get("title", "")
    location = (data.get("location") or {}).get("name", "")
    content_html = html.unescape(data.get("content", ""))
    body = _clean_lines(BeautifulSoup(content_html, "html.parser").get_text("\n"))
    if not body:
        return ""
    header = title + (f" — {location}" if location else "")
    return f"{header}\n\n{body}".strip()


def _attempt_strategies(url: str, llm_client=None) -> List[Tuple[str, str]]:
    """Run each extraction strategy. Returns [(label, text), ...] for ones
    that produced enough content. Order = preferred-first."""
    results: List[Tuple[str, str]] = []

    # Strategy 0: known job-board APIs (Greenhouse, etc.) — bypasses JS issues
    # entirely when the URL is supported.
    gh = _extract_greenhouse(url)
    if gh and len(gh) >= 200:
        results.append(("Greenhouse API", gh))

    html_raw = _fetch(url)
    raw_default = _extract_default(html_raw) if html_raw else ""
    raw_structured = _extract_structured(html_raw) if html_raw else ""

    # Strategy 1: hand the raw page text to the LLM and let it pick out the
    # actual JD. Most robust across job boards / employer pages.
    if llm_client and raw_default and len(raw_default) >= 200:
        print("  Asking the LLM to extract the JD from the raw page text...")
        llm_text = _extract_with_llm(raw_default, llm_client)
        if llm_text:
            results.append(("LLM-extracted", llm_text))

    # Strategy 2: structured selectors (article/main/JD-specific containers).
    if raw_structured and len(raw_structured) >= 200 and raw_structured != raw_default:
        results.append(("article-focused", raw_structured))

    # Strategy 3: raw stripped-text extraction.
    if raw_default and len(raw_default) >= 200:
        results.append(("standard", raw_default))

    # Strategy 4: some sites serve cleaner content to crawlers. Only try
    # this if every other strategy came back empty.
    if not results:
        html_alt = _fetch(url, ua="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
        if html_alt:
            text = _extract_default(html_alt)
            if len(text) >= 200:
                results.append(("googlebot UA", text))

    return results


def _prompt_paste(url: str) -> str:
    print(file=sys.stderr)
    print(
        "  No worries — please paste the JD manually.",
        file=sys.stderr,
    )
    print(
        f"  Open {url} in your browser, copy the job description text,",
        file=sys.stderr,
    )
    print(
        "  and paste it below. When you're done, type EOF on its own line.",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    lines = []
    for line in sys.stdin:
        if line.strip() == "EOF":
            break
        lines.append(line)
    return "".join(lines).strip()


def fetch_jd(
    url: str,
    confirm: Optional[ConfirmCallback] = None,
    llm_client=None,
) -> JobDescription:
    """Fetch a JD.
    - If `llm_client` is provided, the LLM is used as the first (highest
      quality) extraction strategy, with CSS/structural strategies as backup.
    - If `confirm` is provided, show each extraction to the user and let
      them accept or reject. After all automated strategies are rejected
      or exhausted, fall back to manual paste."""
    slug = _slug_from_url(url)

    attempts = _attempt_strategies(url, llm_client=llm_client)

    if confirm is None:
        if attempts:
            return JobDescription(url=url, text=attempts[0][1], slug=slug)
        return JobDescription(url=url, text=_prompt_paste(url), slug=slug)

    for label, text in attempts:
        if confirm(text, label):
            return JobDescription(url=url, text=text, slug=slug)
        print("  Got it — trying a different extraction method...", file=sys.stderr)

    return JobDescription(url=url, text=_prompt_paste(url), slug=slug)
