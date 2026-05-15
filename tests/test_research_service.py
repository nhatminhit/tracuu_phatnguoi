from providers.research_provider import ResearchProviderError, ResearchProviderResult
from services.research_service import ResearchService


class StubProvider:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def research(self, query: str):
        if self.error:
            raise self.error
        return self.result


def test_research_service_rejects_short_query() -> None:
    service = ResearchService(provider=StubProvider())

    result = service.research("abc")

    assert result["status"] == "error"
    assert "quá ngắn" in str(result["message"])


def test_research_service_returns_partial_without_answer_box() -> None:
    provider_result = ResearchProviderResult(
        engine="google_scrape",
        answer_box=None,
        results=[{"rank": 1, "title": "A", "url": "https://a.com", "source_type": "other"}],
        top_results=[{"rank": 1, "title": "A", "url": "https://a.com", "source_type": "other"}],
        note="n",
    )
    service = ResearchService(provider=StubProvider(result=provider_result))

    result = service.research("học phí đại học")

    assert result["status"] == "partial"
    assert result["top_results"]


def test_research_service_maps_provider_error() -> None:
    service = ResearchService(provider=StubProvider(error=ResearchProviderError("boom")))

    result = service.research("mức phạt vượt đèn đỏ")

    assert result["status"] == "error"
    assert result["message"] == "boom"
