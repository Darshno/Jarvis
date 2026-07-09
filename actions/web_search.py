"""
web_search.py — local-only version.
Uses DuckDuckGo for all modes. No cloud API key needed.
"""


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title", ""),
                "snippet": r.get("body", ""),
                "url":     r.get("href", ""),
            })
    return results


def _ddg_news(query: str, max_results: int = 8) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        try:
            for r in ddgs.news(query or "world news", max_results=max_results):
                results.append({
                    "title":   r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url":     r.get("url", r.get("href", "")),
                    "date":    r.get("date", ""),
                })
        except Exception:
            # Some ddgs versions don't support .news() — fall back to text search
            return _ddg_search(f"latest news {query}".strip(), max_results)
    return results


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):
            prefix = f"{r['date']} — " if r.get("date") else ""
            lines.append(f"{i}. {prefix}{r['title']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        if r.get("url"):
            lines.append(f"   Source: {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _compare(items: list, aspect: str) -> str:
    all_results: dict = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "-" * 40]
    for item in items:
        lines.append(f"\n> {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  - {r['snippet']}")
            if r.get("url"):
                lines.append(f"    {r['url']}")
    return "\n".join(lines)


def web_search(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode", "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query."

    if items and mode not in ("compare",):
        mode = "compare"

    if player:
        try:
            player.write_log(f"[Search:{mode}] {query or ', '.join(items)}")
        except Exception:
            pass

    print(f"[WebSearch] mode={mode!r} query={query!r}")

    try:
        if mode == "compare" and items:
            result = _compare(items, aspect)
        elif mode == "news":
            result = _format_ddg(query or "world news", _ddg_news(query, max_results=8))
        elif mode == "research":
            result = _format_ddg(query, _ddg_search(query, max_results=10))
        elif mode == "price":
            result = _format_ddg(query, _ddg_search(f"{query} price buy", max_results=6))
        else:
            result = _format_ddg(query, _ddg_search(query, max_results=6))

        if session_memory:
            try:
                session_memory.set_last_search(query=query, response=result)
            except Exception:
                pass
        return result

    except Exception as e:
        print(f"[WebSearch] Failed: {e}")
        return f"Search failed: {e}"
