from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from services.traffic_fine_service import TrafficFineService


@dataclass
class TelegramMessageRequest:
    chat_id: int
    text: str
    reply_markup: dict[str, Any] | None = None


class TelegramBotService:
    VEHICLE_TYPES = {
        "car": "Xe ô tô",
        "motorbike": "Xe máy",
        "electricbike": "Xe đạp điện",
    }
    CALLBACK_PREFIX = "lookup"

    def __init__(
        self,
        lookup_service: TrafficFineService,
        bot_token: str | None = None,
        webhook_secret: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.lookup_service = lookup_service
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.webhook_secret = webhook_secret or os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
        self.session = session or requests.Session()

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
            return [TelegramMessageRequest(chat_id=chat_id, text=self._help_text())]

        if text.lower() in {"/start", "/help"}:
            return [TelegramMessageRequest(chat_id=chat_id, text=self._help_text())]

        plate_number, vehicle_type = self._parse_lookup_text(text)
        if vehicle_type:
            return [self._lookup_message(chat_id, plate_number, vehicle_type)]

        if not plate_number:
            return [TelegramMessageRequest(chat_id=chat_id, text=self._help_text())]

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
            return [TelegramMessageRequest(chat_id=chat_id, text="Yêu cầu không hợp lệ. Hãy nhập lại biển số.")]

        plate_number, vehicle_type = parsed
        return [self._lookup_message(chat_id, plate_number, vehicle_type)]

    def _lookup_message(self, chat_id: int, plate_number: str, vehicle_type: str) -> TelegramMessageRequest:
        result = self.lookup_service.lookup(plate_number, vehicle_type)
        return TelegramMessageRequest(chat_id=chat_id, text=self._format_lookup_result(result))

    def _parse_lookup_text(self, text: str) -> tuple[str, str | None]:
        parts = text.split()
        if not parts:
            return "", None

        plate_number = parts[0]
        vehicle_type = parts[1].lower() if len(parts) > 1 else None
        if vehicle_type not in self.VEHICLE_TYPES:
            return plate_number, None
        return plate_number, vehicle_type

    def _vehicle_type_keyboard(self, plate_number: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": label,
                        "callback_data": self._callback_data(plate_number, vehicle_type),
                    }
                ]
                for vehicle_type, label in self.VEHICLE_TYPES.items()
            ]
        }

    def _callback_data(self, plate_number: str, vehicle_type: str) -> str:
        return f"{self.CALLBACK_PREFIX}|{plate_number}|{vehicle_type}"

    def _parse_callback_data(self, data: str) -> tuple[str, str] | None:
        prefix, separator, payload = data.partition("|")
        if prefix != self.CALLBACK_PREFIX or not separator:
            return None
        plate_number, separator, vehicle_type = payload.rpartition("|")
        if not separator or vehicle_type not in self.VEHICLE_TYPES:
            return None
        return plate_number, vehicle_type

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

    def _help_text(self) -> str:
        return (
            "Gửi theo 1 trong 2 cách:\n"
            "- Nhanh: 51H12345 car\n"
            "- Hoặc chỉ gửi biển số, bot sẽ cho chọn loại xe\n\n"
            "Loại xe hỗ trợ: car, motorbike, electricbike"
        )

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"
