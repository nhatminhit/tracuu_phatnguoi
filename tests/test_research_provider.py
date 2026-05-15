from providers.research_provider import ResearchProvider, ResearchProviderError


class DummyResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


def test_research_ranks_official_sources_first() -> None:
    provider = ResearchProvider()
    html = """
    <html><body>
      <div class="kno-rdesc"><span>Đây là phần tóm tắt đủ dài để không bị xem là quá ngắn khi hiển thị trong Telegram.</span></div>
      <div><a href="https://example.com/forum/post"><h3>Thảo luận cộng đồng</h3></a><div class="VwiC3b">Nguồn cộng đồng.</div></div>
      <div><a href="https://moj.gov.vn/van-ban/test"><h3>Nghị định từ Bộ Tư pháp</h3></a><div class="VwiC3b">Văn bản chính thức.</div></div>
      <div><a href="https://vnexpress.net/test"><h3>Bài viết tổng hợp</h3></a><div class="VwiC3b">Nguồn báo.</div></div>
    </body></html>
    """
    provider._search_google = lambda query: DummyResponse(html)  # type: ignore[method-assign]

    result = provider.research("nghị định giao thông")

    assert result.top_results[0]["source_type"] == "official"
    assert "moj.gov.vn" in str(result.top_results[0]["url"])
    assert result.note is not None


def test_guard_google_response_raises_on_captcha() -> None:
    provider = ResearchProvider()

    try:
        provider._guard_google_response(DummyResponse("g-recaptcha captcha", 200))
    except ResearchProviderError as exc:
        assert "CAPTCHA" in str(exc)
    else:
        raise AssertionError("Expected ResearchProviderError")
