from __future__ import annotations

from datetime import datetime

from providers.research_provider import ResearchProvider, ResearchProviderError


class ResearchService:
    def __init__(self, provider: ResearchProvider | None = None) -> None:
        self.provider = provider or ResearchProvider()

    def research(self, query: str) -> dict[str, object]:
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            return self._build_response(
                status="error",
                message="Vui lòng nhập câu hỏi hoặc từ khóa cụ thể hơn để tra cứu web realtime.",
                query="",
            )
        if len(normalized_query) < 4:
            return self._build_response(
                status="error",
                message="Câu hỏi quá ngắn. Hãy thêm chủ đề, bối cảnh hoặc mục tiêu bạn muốn tìm.",
                query=normalized_query,
            )

        try:
            provider_result = self.provider.research(normalized_query)
        except ResearchProviderError as exc:
            return self._build_response(
                status="error",
                message=str(exc),
                query=normalized_query,
            )

        status = "success"
        message = "Tra cứu web realtime hoàn tất."
        if provider_result.results and not provider_result.answer_box:
            status = "partial"
            message = "Đã tìm thấy nguồn web, nhưng chưa có phần tóm tắt nổi bật đủ rõ."
        elif not provider_result.results:
            status = "partial"
            message = "Có kết quả nổi bật, nhưng danh sách nguồn tham khảo còn hạn chế."

        return self._build_response(
            status=status,
            message=message,
            query=normalized_query,
            answer_box=provider_result.answer_box,
            results=provider_result.results,
            top_results=provider_result.top_results,
            note=provider_result.note,
            engine=provider_result.engine,
            source_priority=provider_result.source_priority,
        )

    def _build_response(
        self,
        *,
        status: str,
        message: str,
        query: str,
        answer_box: str | None = None,
        results: list[dict[str, object]] | None = None,
        top_results: list[dict[str, object]] | None = None,
        note: str | None = None,
        engine: str | None = None,
        source_priority: str | None = None,
    ) -> dict[str, object]:
        return {
            "status": status,
            "message": message,
            "query": query,
            "answer_box": answer_box,
            "results": results or [],
            "top_results": top_results or [],
            "note": note,
            "engine": engine,
            "source_priority": source_priority,
            "searched_at": self._timestamp(),
        }

    def _normalize_query(self, query: str) -> str:
        return " ".join(query.strip().split())

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
