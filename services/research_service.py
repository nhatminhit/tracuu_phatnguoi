from __future__ import annotations

from datetime import datetime

from providers.research_provider import ResearchProvider, ResearchProviderError


class ResearchService:
    def __init__(self, provider: ResearchProvider | None = None) -> None:
        self.provider = provider or ResearchProvider()

    def research(self, query: str) -> dict[str, object]:
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            return {
                "status": "error",
                "message": "Vui lòng nhập câu hỏi cần research.",
                "query": "",
                "summary": "",
                "sources": [],
                "searched_at": self._timestamp(),
                "model": None,
            }

        try:
            provider_result = self.provider.research(normalized_query)
        except ResearchProviderError as exc:
            return {
                "status": "error",
                "message": str(exc),
                "query": normalized_query,
                "summary": "",
                "sources": [],
                "searched_at": self._timestamp(),
                "model": None,
            }

        return {
            "status": "success",
            "message": "Research hoàn tất.",
            "query": normalized_query,
            "summary": provider_result.summary,
            "sources": provider_result.sources,
            "searched_at": self._timestamp(),
            "model": provider_result.model,
        }

    def _normalize_query(self, query: str) -> str:
        return " ".join(query.strip().split())

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
