from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from services.research_service import ResearchService
from services.traffic_fine_service import TrafficFineService


@dataclass
class TelegramMessageRequest:
    chat_id: int
    text: str
    reply_markup: dict[str, Any] | None = None


class TelegramBotService:
    TELEGRAM_MESSAGE_LIMIT = 3500
    VEHICLE_TYPES = {
        "car": "Xe ô tô",
        "motorbike": "Xe máy",
        "electricbike": "Xe đạp điện",
    }
    FEATURE_LABELS = {
        "lookup": "Tra cứu phạt nguội",
        "research": "Tra cứu web realtime",
    }
    LOOKUP_CALLBACK_PREFIX = "lookup"
    FEATURE_CALLBACK_PREFIX = "feature"
    RESEARCH_STATE = "awaiting_research_query"

    def __init__(
        self,
        lookup_service: TrafficFineService,
        research_service: ResearchService,
        bot_token: str | None = None,
        webhook_secret: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.lookup_service = lookup_service
        self.research_service = research_service
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.webhook_secret = webhook_secret or os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
        self.session = session or requests.Session()
        self.chat_states: dict[int, str] = {}

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.webhook_secret)

    def is_valid_secret(self, secret: str) -> bool:
        return bool(secret) and secret == self.webhook_secret

    def process_update(self, update: dict[str, Any]) -> list[TelegramMessageRequest]:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            return self._handle_callback_query(callback_query)

        message = update.get("message")
        if isinstance(message, dict):
            return self._handle_message(message)

        return []

    def send_message(self, message: TelegramMessageRequest) -> None:
        if not self.bot_token:
            return
        payload: dict[str, Any] = {
            "chat_id": message.chat_id,
            "text": message.text,
        }
        if message.reply_markup:
            payload["reply_markup"] = message.reply_markup
        response = self.session.post(self._api_url("sendMessage"), json=payload, timeout=20)
        response.raise_for_status()

    def _handle_message(self, message: dict[str, Any]) -> list[TelegramMessageRequest]:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return []

        text = (message.get("text") or "").strip()
        if not text:
            self._clear_state(chat_id)
            return self._menu_messages(chat_id)

        claude_query = self._parse_claude_command(text)
        if claude_query:
            self._clear_state(chat_id)
            return self._research_messages(chat_id, claude_query)

        if text.lower() in {"/start", "/help", "menu", "🏠 menu"}:
            self._clear_state(chat_id)
            return self._menu_messages(chat_id)

        selected_feature = self._feature_from_text(text)
        if selected_feature == "lookup":
            self._clear_state(chat_id)
            return self._feature_help_messages(chat_id, self._lookup_help_text())
        if selected_feature == "research":
            self.chat_states[chat_id] = self.RESEARCH_STATE
            return self._feature_help_messages(chat_id, self._research_help_text())

        if self.chat_states.get(chat_id) == self.RESEARCH_STATE:
            self._clear_state(chat_id)
            return self._research_messages(chat_id, text)

        plate_number, vehicle_type = self._parse_lookup_text(text)
        if vehicle_type:
            self._clear_state(chat_id)
            return [self._lookup_message(chat_id, plate_number, vehicle_type)]

        if not plate_number:
            self._clear_state(chat_id)
            return self._menu_messages(chat_id)

        return [
            TelegramMessageRequest(
                chat_id=chat_id,
                text=f"Chọn loại phương tiện cho biển số {plate_number}.",
                reply_markup=self._vehicle_type_keyboard(plate_number),
            )
        ]

    def _handle_callback_query(self, callback_query: dict[str, Any]) -> list[TelegramMessageRequest]:
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return []

        data = callback_query.get("data") or ""
        parsed = self._parse_callback_data(data)
        if not parsed:
            return [TelegramMessageRequest(chat_id=chat_id, text="Yêu cầu không hợp lệ.")]

        kind, *payload = parsed
        if kind == self.FEATURE_CALLBACK_PREFIX:
            feature = payload[0]
            self._clear_state(chat_id)
            if feature == "lookup":
                return self._feature_help_messages(chat_id, self._lookup_help_text())
            if feature == "research":
                self.chat_states[chat_id] = self.RESEARCH_STATE
                return self._feature_help_messages(chat_id, self._research_help_text())
            return [TelegramMessageRequest(chat_id=chat_id, text="Tính năng không hợp lệ.")]

        if kind == self.LOOKUP_CALLBACK_PREFIX:
            plate_number, vehicle_type = payload
            self._clear_state(chat_id)
            return [self._lookup_message(chat_id, plate_number, vehicle_type)]

        return [TelegramMessageRequest(chat_id=chat_id, text="Yêu cầu không hợp lệ.")]

    def _menu_messages(self, chat_id: int) -> list[TelegramMessageRequest]:
        return [
            TelegramMessageRequest(chat_id=chat_id, text="Menu nhanh đã bật dưới ô chat.", reply_markup=self._menu_keyboard()),
            TelegramMessageRequest(chat_id=chat_id, text=self._help_text(), reply_markup=self._feature_keyboard()),
        ]

    def _feature_help_messages(self, chat_id: int, text: str) -> list[TelegramMessageRequest]:
        return [
            TelegramMessageRequest(chat_id=chat_id, text="Bạn có thể dùng nút dưới ô chat hoặc bấm nút nhanh bên dưới." , reply_markup=self._menu_keyboard()),
            TelegramMessageRequest(chat_id=chat_id, text=text, reply_markup=self._feature_keyboard()),
        ]

    def _lookup_message(self, chat_id: int, plate_number: str, vehicle_type: str) -> TelegramMessageRequest:
        result = self.lookup_service.lookup(plate_number, vehicle_type)
        return TelegramMessageRequest(chat_id=chat_id, text=self._format_lookup_result(result))

    def _research_messages(self, chat_id: int, query: str) -> list[TelegramMessageRequest]:
        result = self.research_service.research(query)
        return [
            TelegramMessageRequest(chat_id=chat_id, text=chunk)
            for chunk in self._chunk_message(self._format_research_result(result))
        ]

    def _research_message(self, chat_id: int, query: str) -> TelegramMessageRequest:
        return self._research_messages(chat_id, query)[0]

    def _feature_from_text(self, text: str) -> str | None:
        normalized = text.strip().lower()
        for feature, label in self.FEATURE_LABELS.items():
            if normalized == label.lower():
                return feature
        return None

    def _parse_claude_command(self, text: str) -> str | None:
        normalized = text.strip()
        if not normalized.lower().startswith("/claude"):
            return None
        query = normalized[7:].strip()
        return query or None

    def _parse_lookup_text(self, text: str) -> tuple[str, str | None]:
        parts = text.split()
        if not parts:
            return "", None

        plate_number = parts[0]
        vehicle_type = parts[1].lower() if len(parts) > 1 else None
        if vehicle_type not in self.VEHICLE_TYPES:
            return plate_number, None
        return plate_number, vehicle_type

    def _menu_keyboard(self) -> dict[str, Any]:
        return {
            "keyboard": [
                [{"text": self.FEATURE_LABELS["lookup"]}],
                [{"text": self.FEATURE_LABELS["research"]}],
                [{"text": "🏠 Menu"}],
            ],
            "resize_keyboard": True,
            "persistent": True,
        }

    def _feature_keyboard(self) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": label,
                        "callback_data": self._feature_callback_data(feature),
                    }
                ]
                for feature, label in self.FEATURE_LABELS.items()
            ]
        }

    def _vehicle_type_keyboard(self, plate_number: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": label,
                        "callback_data": self._lookup_callback_data(plate_number, vehicle_type),
                    }
                ]
                for vehicle_type, label in self.VEHICLE_TYPES.items()
            ]
        }

    def _feature_callback_data(self, feature: str) -> str:
        return f"{self.FEATURE_CALLBACK_PREFIX}|{feature}"

    def _lookup_callback_data(self, plate_number: str, vehicle_type: str) -> str:
        return f"{self.LOOKUP_CALLBACK_PREFIX}|{plate_number}|{vehicle_type}"

    def _parse_callback_data(self, data: str) -> tuple[str, ...] | None:
        prefix, separator, payload = data.partition("|")
        if not separator:
            return None

        if prefix == self.FEATURE_CALLBACK_PREFIX:
            feature = payload.strip()
            if feature not in self.FEATURE_LABELS:
                return None
            return prefix, feature

        if prefix == self.LOOKUP_CALLBACK_PREFIX:
            plate_number, separator, vehicle_type = payload.rpartition("|")
            if not separator or vehicle_type not in self.VEHICLE_TYPES:
                return None
            return prefix, plate_number, vehicle_type

        return None

    def _format_lookup_result(self, result: dict[str, Any]) -> str:
        plate_number = str(result.get("plate_number") or "-")
        vehicle_type = self.VEHICLE_TYPES.get(str(result.get("vehicle_type") or ""), str(result.get("vehicle_type") or "-"))
        lines = [
            "Kết quả tra cứu phạt nguội",
            f"Biển số: {plate_number}",
            f"Loại xe: {vehicle_type}",
            f"Trạng thái: {result.get('status')}",
            f"Thông báo: {result.get('message')}",
            f"Thời điểm: {result.get('searched_at')}",
        ]

        source = result.get("source")
        if source:
            lines.append(f"Nguồn: {source}")

        source_note = result.get("source_note")
        if source_note:
            lines.append(f"Ghi chú: {source_note}")

        violations = result.get("violations") or []
        if violations:
            lines.append("")
            lines.append("Chi tiết vi phạm:")
            for index, violation in enumerate(violations, start=1):
                lines.append(
                    f"{index}. {violation.get('behavior') or 'Không rõ hành vi'} | "
                    f"{violation.get('violation_time') or '-'} | "
                    f"{violation.get('location') or '-'} | "
                    f"{violation.get('status') or '-'}"
                )

        return "\n".join(lines)

    def _format_research_result(self, result: dict[str, Any]) -> str:
        lines = [
            "Tra cứu web realtime",
            f"Truy vấn: {result.get('query') or '-'}",
            f"Kết quả: {result.get('message')}",
        ]

        answer_box = str(result.get("answer_box") or "").strip()
        if answer_box:
            lines.extend(["", "Tóm tắt nhanh", answer_box])

        top_results = result.get("top_results") or result.get("results") or []
        if top_results:
            lines.extend(["", "Nguồn nên xem"])
            for item in top_results[:4]:
                source_type = self._source_type_label(str(item.get("source_type") or "other"))
                lines.append(f"{item.get('rank')}. {item.get('title') or '-'} [{source_type}]")
                lines.append(str(item.get('snippet') or 'Không có mô tả ngắn.'))
                lines.append(str(item.get('url') or '-'))
                lines.append("")

        note = result.get("note")
        if note:
            lines.extend(["Lưu ý", str(note)])

        if result.get("status") == "error":
            lines.append("")
            lines.append("Hãy thử viết lại câu hỏi cụ thể hơn hoặc gửi lại sau.")

        return "\n".join(line for line in lines if line is not None).rstrip()

    def _source_type_label(self, source_type: str) -> str:
        return {
            "official": "chính thống",
            "primary": "nguồn gốc",
            "reputable": "uy tín",
            "community": "cộng đồng",
            "other": "tham khảo",
        }.get(source_type, "tham khảo")

    def _chunk_message(self, text: str) -> list[str]:
        normalized = text.strip()
        if len(normalized) <= self.TELEGRAM_MESSAGE_LIMIT:
            return [normalized]

        chunks: list[str] = []
        current = ""
        for block in normalized.split("\n\n"):
            candidate = block.strip()
            if not candidate:
                continue
            separator = "\n\n" if current else ""
            if len(current) + len(separator) + len(candidate) <= self.TELEGRAM_MESSAGE_LIMIT:
                current = f"{current}{separator}{candidate}"
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(candidate) > self.TELEGRAM_MESSAGE_LIMIT:
                split_at = candidate.rfind("\n", 0, self.TELEGRAM_MESSAGE_LIMIT)
                if split_at <= 0:
                    split_at = self.TELEGRAM_MESSAGE_LIMIT
                chunks.append(candidate[:split_at].rstrip())
                candidate = candidate[split_at:].lstrip()
            current = candidate
        if current:
            chunks.append(current)
        return chunks or [normalized]

    def _lookup_help_text(self) -> str:
        return (
            "Tra cứu phạt nguội:\n"
            "- Nhanh: 51H12345 car\n"
            "- Hoặc chỉ gửi biển số, bot sẽ cho chọn loại xe\n\n"
            "Loại xe hỗ trợ: car, motorbike, electricbike"
        )

    def _research_help_text(self) -> str:
        return (
            "Gửi câu hỏi ở tin nhắn tiếp theo để bot tra cứu web realtime.\n"
            "Bot sẽ ưu tiên nguồn chính thống trước, rồi mở rộng sang nguồn tham khảo nếu cần.\n\n"
            "Ví dụ:\n"
            "- Mức phạt vượt đèn đỏ xe máy năm 2025\n"
            "- Thủ tục cấp lại GPLX bị mất\n"
            "- /claude học phí đại học công lập 2026"
        )

    def _help_text(self) -> str:
        return (
            "Chọn tính năng cần dùng:\n"
            "- Tra cứu phạt nguội\n"
            "- Tra cứu web realtime\n\n"
            f"{self._lookup_help_text()}"
        )

    def _clear_state(self, chat_id: int) -> None:
        self.chat_states.pop(chat_id, None)

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"
