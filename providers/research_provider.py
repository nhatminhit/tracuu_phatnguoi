from __future__ import annotations

from dataclasses import dataclass
import json
import os

import anthropic
import requests


@dataclass
class ResearchProviderResult:
    summary: str
    sources: list[dict[str, str]]
    raw_message: str | None
    model: str


class ResearchProviderError(Exception):
    pass


class ResearchProvider:
    SYSTEM_PROMPT = (
        "Bạn là trợ lý hỏi đáp bằng tiếng Việt. "
        "Trả lời ngắn gọn, chính xác, dễ hiểu. "
        "Nếu không chắc hoặc thông tin có thể đã cũ, nói rõ giới hạn. "
        "Không bịa nguồn. Chỉ nêu nguồn tham khảo khi thực sự có trong ngữ cảnh."
    )

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: anthropic.Anthropic | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.base_url = (os.getenv("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self.client = client or anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url or None)
        self.session = session or requests.Session()

    def research(self, query: str) -> ResearchProviderResult:
        if not self.api_key:
            raise ResearchProviderError("Chưa cấu hình ANTHROPIC_API_KEY.")

        try:
            raw_message = self._research_via_proxy(query) if self.base_url else self._research_via_sdk(query)
        except anthropic.AuthenticationError as exc:
            raise ResearchProviderError("ANTHROPIC_API_KEY không hợp lệ.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ResearchProviderError("API key không có quyền dùng Claude API.") from exc
        except anthropic.RateLimitError as exc:
            raise ResearchProviderError("Claude API đang quá tải hoặc bị giới hạn tốc độ. Hãy thử lại sau.") from exc
        except anthropic.APIConnectionError as exc:
            raise ResearchProviderError("Không kết nối được Claude API.") from exc
        except anthropic.APIStatusError as exc:
            raise ResearchProviderError(f"Claude API lỗi ({exc.status_code}).") from exc
        except anthropic.APIError as exc:
            raise ResearchProviderError("Claude API gặp lỗi không xác định.") from exc
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "?"
            raise ResearchProviderError(f"Proxy Claude API lỗi ({status_code}).") from exc
        except requests.RequestException as exc:
            raise ResearchProviderError("Không kết nối được proxy Claude API.") from exc

        if not raw_message:
            raise ResearchProviderError("Claude không trả về nội dung research.")

        summary, sources = self._split_summary_and_sources(raw_message)
        return ResearchProviderResult(
            summary=summary,
            sources=sources,
            raw_message=raw_message,
            model=self.model,
        )

    def _research_via_sdk(self, query: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            cache_control={"type": "ephemeral"},
            system=[
                {
                    "type": "text",
                    "text": self.SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": self._user_prompt(query),
                }
            ],
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        return "\n".join(text_blocks).strip()

    def _research_via_proxy(self, query: str) -> str:
        response = self.session.post(
            f"{self.base_url}/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.model,
                "max_tokens": 4000,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
                "system": [{"type": "text", "text": self.SYSTEM_PROMPT}],
                "messages": [{"role": "user", "content": self._user_prompt(query)}],
            },
            timeout=60,
            stream=True,
        )
        response.raise_for_status()

        parts: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            payload = raw_line[6:]
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "content_block_delta":
                continue
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                parts.append(delta.get("text") or "")

        return "".join(parts).strip()

    def _user_prompt(self, query: str) -> str:
        return (
            "Hãy trả lời câu hỏi sau bằng tiếng Việt:\n"
            f"{query}\n\n"
            "Yêu cầu:\n"
            "1. Trả lời ngắn gọn, có cấu trúc.\n"
            "2. Nếu nội dung có thể phụ thuộc dữ liệu thời gian thực hoặc có thể đã cũ, nói rõ điều đó.\n"
            "3. Chỉ thêm mục 'Nguồn tham khảo' nếu bạn thực sự có nguồn cụ thể trong ngữ cảnh hiện tại."
        )

    def _split_summary_and_sources(self, raw_message: str) -> tuple[str, list[dict[str, str]]]:
        marker = "Nguồn tham khảo"
        if marker not in raw_message:
            return raw_message, []

        summary, _, source_section = raw_message.partition(marker)
        sources: list[dict[str, str]] = []
        for line in source_section.splitlines():
            normalized = line.strip().lstrip("-").strip()
            if not normalized:
                continue
            title, separator, url = normalized.partition("|")
            if separator and url.strip():
                sources.append({"title": title.strip(), "url": url.strip()})
        return summary.strip(), sources
