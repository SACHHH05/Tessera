"""Minimal Tavily search client (used to ground the requirements brief).

Targets the hackathon's "Best Use of Tavily" bonus: the Main AI researches similar
products while drafting the brief, so requirements are grounded rather than generic.
"""

from __future__ import annotations

from typing import Any

import httpx

TAVILY_URL = "https://api.tavily.com/search"


class TavilyClient:
    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY is not set")
        resp = httpx.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "max_results": max_results},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title"), "url": r.get("url"), "content": r.get("content")}
            for r in resp.json().get("results", [])
        ]
