from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


class ResearchProviderError(Exception):
    pass


@dataclass
class ResearchProviderResult:
    engine: str
    answer_box: str | None
    results: list[dict[str, str | int]]
    top_results: list[dict[str, str | int]]
    note: str | None = None
    source_priority: str = "balanced"


class ResearchProvider:
    GOOGLE_SEARCH_URL = "https://www.google.com/search"
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    OFFICIAL_DOMAINS = (
        ".gov.vn",
        ".gov",
        ".gso.gov.vn",
        ".moj.gov.vn",
        ".chinhphu.vn",
        ".quochoi.vn",
    )
    REPUTABLE_NEWS_DOMAINS = {
        "vnexpress.net",
        "tuoitre.vn",
        "thanhnien.vn",
        "nhandan.vn",
        "vietnamplus.vn",
        "dantri.com.vn",
        "laodong.vn",
    }
    COMMUNITY_DOMAINS = {
        "wikipedia.org",
        "wiktionary.org",
        "reddit.com",
        "quora.com",
        "voz.vn",
        "stackexchange.com",
        "facebook.com",
        "youtube.com",
        "tiktok.com",
    }
    AGGREGATOR_HINTS = ("search", "toplist", "review", "forum", "wiki")
    PRIORITY_ORDER = {
        "official": 0,
        "primary": 1,
        "reputable": 2,
        "other": 3,
        "community": 4,
    }
    PRIMARY_HINTS = ("docs.", "developer.", "support.", "help.", "learn.", "edu")
    OFFICIAL_KEYWORDS = ("bo ", "ubnd", "quoc hoi", "chinh phu", "nghi dinh", "thong tu")
    LOW_CONFIDENCE_HINTS = ("có thể", "thường", "tham khảo", "cân nhắc")
    ANSWER_BOX_MIN_LENGTH = 60
    TOP_RESULTS_LIMIT = 4

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache",
            }
        )

    def research(self, query: str) -> ResearchProviderResult:
        response = self._search_google(query)
        self._guard_google_response(response)

        soup = BeautifulSoup(response.text, "html.parser")
        answer_box = self._extract_answer_box(soup)
        results = self._extract_results(soup)
        if not results and not answer_box:
            raise ResearchProviderError(
                "Không đọc được kết quả Google lúc này do xác minh chống bot hoặc thay đổi giao diện."
            )

        ranked_results = self._rank_results(results, query)
        top_results = ranked_results[: self.TOP_RESULTS_LIMIT]
        note = self._build_note(answer_box, top_results)

        return ResearchProviderResult(
            engine="google_scrape",
            answer_box=answer_box,
            results=ranked_results,
            top_results=top_results,
            note=note,
        )

    def _search_google(self, query: str) -> requests.Response:
        try:
            response = self.session.get(
                self.GOOGLE_SEARCH_URL,
                params={
                    "q": query,
                    "hl": "vi",
                    "gl": "vn",
                    "num": "5",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ResearchProviderError("Không kết nối được Google Search.") from exc
        return response

    def _guard_google_response(self, response: requests.Response) -> None:
        if response.status_code == 429:
            raise ResearchProviderError("Google đang giới hạn truy vấn tự động. Vui lòng thử lại sau.")
        if response.status_code >= 400:
            raise ResearchProviderError(f"Google Search trả lỗi HTTP {response.status_code}.")

        html = response.text or ""
        lowered = html.lower()
        if "unusual traffic" in lowered or "detected unusual traffic" in lowered or "/sorry/" in lowered:
            raise ResearchProviderError("Google đang yêu cầu xác minh chống bot. Vui lòng thử lại sau.")
        if "captcha" in lowered and "g-recaptcha" in lowered:
            raise ResearchProviderError("Google đang yêu cầu CAPTCHA nên chưa thể tra cứu realtime.")
        if not html.strip():
            raise ResearchProviderError("Google Search không trả dữ liệu.")

    def _extract_answer_box(self, soup: BeautifulSoup) -> str | None:
        selectors = [
            '[data-attrid="wa:/description"]',
            '[data-attrid="title"]',
            '.kno-rdesc span',
            '.hgKElc',
            '.IZ6rdc',
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            text = self._clean_text(node.get_text(" ", strip=True) if node else "")
            if text:
                return self._truncate(text, 280)
        return None

    def _extract_results(self, soup: BeautifulSoup) -> list[dict[str, str | int]]:
        results: list[dict[str, str | int]] = []
        seen_urls: set[str] = set()

        for anchor in soup.select('a[href]'):
            normalized_url = self._normalize_google_url(anchor.get('href', ''))
            if not normalized_url or normalized_url in seen_urls:
                continue

            container = self._result_container(anchor)
            title = self._extract_title(anchor, container)
            if not title:
                continue

            snippet = self._extract_snippet(container, title)
            parsed = urlparse(normalized_url)
            source = parsed.netloc.replace('www.', '')
            display_url = source + parsed.path if parsed.path and parsed.path != '/' else source

            results.append(
                {
                    "rank": len(results) + 1,
                    "title": self._truncate(title, 120),
                    "url": normalized_url,
                    "display_url": self._truncate(display_url, 80),
                    "snippet": self._truncate(snippet, 240),
                    "source": source,
                    "source_type": self._classify_source(normalized_url, title, snippet),
                }
            )
            seen_urls.add(normalized_url)

            if len(results) >= 5:
                break

        return results

    def _normalize_google_url(self, href: str) -> str | None:
        if not href:
            return None
        if href.startswith('/url?'):
            query = parse_qs(urlparse(href).query)
            href = query.get('q', [''])[0]
        href = unescape(href.strip())
        parsed = urlparse(href)
        if parsed.scheme not in {'http', 'https'}:
            return None
        if 'google.' in parsed.netloc:
            return None
        return href

    def _result_container(self, anchor) -> BeautifulSoup:
        for levels in range(5):
            node = anchor if levels == 0 else anchor.find_parent('div')
            if node is None:
                break
            if levels > 0:
                anchor = node
            title = node.find(['h3', 'h2'])
            if title:
                return node
        return anchor

    def _extract_title(self, anchor, container) -> str:
        for node in [anchor.find(['h3', 'h2']), container.find(['h3', 'h2']) if hasattr(container, 'find') else None, anchor]:
            if node is None:
                continue
            text = self._clean_text(node.get_text(' ', strip=True))
            if text and len(text) > 3:
                return text
        return ''

    def _extract_snippet(self, container, title: str) -> str:
        candidates = []
        if hasattr(container, 'select'):
            for selector in ['div.VwiC3b', '.yXK7lf', '.MUxGbd', '.lyLwlc', 'span.aCOpRe', 'div[data-sncf]']:
                for node in container.select(selector):
                    candidates.append(node.get_text(' ', strip=True))
        if hasattr(container, 'stripped_strings'):
            candidates.extend(list(container.stripped_strings))

        seen: set[str] = set()
        for candidate in candidates:
            text = self._clean_text(candidate)
            if not text or text == title or text in seen:
                continue
            seen.add(text)
            if len(text) >= 30:
                return text
        return 'Không có mô tả ngắn.'

    def _rank_results(self, results: list[dict[str, str | int]], query: str) -> list[dict[str, str | int]]:
        normalized_query = query.lower()
        ranked = sorted(
            results,
            key=lambda item: (
                self.PRIORITY_ORDER.get(str(item.get("source_type") or "other"), 3),
                0 if self._query_matches_title(str(item.get("title") or ""), normalized_query) else 1,
                int(item.get("rank") or 999),
            ),
        )
        for index, item in enumerate(ranked, start=1):
            item["rank"] = index
        return ranked

    def _query_matches_title(self, title: str, normalized_query: str) -> bool:
        title_lower = title.lower()
        query_terms = [term for term in normalized_query.split() if len(term) > 2]
        return bool(query_terms) and any(term in title_lower for term in query_terms)

    def _classify_source(self, url: str, title: str, snippet: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower().replace("www.", "")
        lowered_title = title.lower()
        lowered_snippet = snippet.lower()

        if any(host.endswith(domain) for domain in self.OFFICIAL_DOMAINS):
            return "official"
        if host in self.REPUTABLE_NEWS_DOMAINS:
            return "reputable"
        if any(community in host for community in self.COMMUNITY_DOMAINS):
            return "community"
        if any(hint in host for hint in self.PRIMARY_HINTS):
            return "primary"
        if host.endswith(".edu") or ".edu." in host:
            return "primary"
        if any(keyword in lowered_title or keyword in lowered_snippet for keyword in self.OFFICIAL_KEYWORDS):
            return "official"
        if any(hint in host for hint in self.AGGREGATOR_HINTS):
            return "community"
        return "other"

    def _build_note(self, answer_box: str | None, top_results: list[dict[str, str | int]]) -> str | None:
        if not top_results:
            return None
        official_count = sum(1 for item in top_results if item.get("source_type") in {"official", "primary"})
        if not answer_box:
            if official_count:
                return "Không có kết quả nổi bật; đã ưu tiên xếp nguồn chính thống lên trước để bạn đối chiếu."
            return "Không có kết quả nổi bật; nên mở vài nguồn đầu để tự đối chiếu vì kết quả tổng hợp còn hạn chế."
        if len(answer_box) < self.ANSWER_BOX_MIN_LENGTH or any(hint in answer_box.lower() for hint in self.LOW_CONFIDENCE_HINTS):
            return "Kết quả nổi bật khá ngắn hoặc còn mơ hồ; nên đọc thêm các nguồn bên dưới để xác nhận."
        if not official_count:
            return "Chưa thấy nhiều nguồn chính thống ở đầu kết quả; bot đã mở rộng sang nguồn tham khảo rộng hơn."
        return "Bot ưu tiên nguồn chính thống trước, rồi mở rộng sang nguồn tham khảo nếu cần."

    def _clean_text(self, text: str) -> str:
        return ' '.join(unescape(text).split())

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + '...'
