def web_search(query: str, max_results: int = 5) -> str:
    """Search the web (DuckDuckGo) for docs, error explanations or current info. Returns titles, URLs and snippets.

    Args:
        query: The search query.
        max_results: How many results to return.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    results = DDGS().text(query, max_results=max_results)
    if not results:
        return "no results"
    return "\n\n".join(f"{r.get('title')}\n{r.get('href')}\n{r.get('body')}" for r in results)
