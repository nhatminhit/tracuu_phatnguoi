from __future__ import annotations

from datetime import datetime

from providers.traffic_fine_provider import TrafficFineProvider, TrafficFineProviderError


class TrafficFineService:
    def __init__(self, provider: TrafficFineProvider | None = None) -> None:
        self.provider = provider or TrafficFineProvider()

    def lookup(self, plate_number: str, vehicle_type: str) -> dict[str, object]:
        normalized_plate = self._normalize_plate_number(plate_number)
        normalized_vehicle_type = self._normalize_vehicle_type(vehicle_type)
        if not normalized_plate:
            return {
                "status": "error",
                "message": "Vui lòng nhập biển số để tra cứu.",
                "plate_number": "",
                "vehicle_type": normalized_vehicle_type,
                "violations": [],
                "searched_at": self._timestamp(),
                "source_note": None,
            }

        try:
            provider_result = self.provider.lookup(normalized_plate, normalized_vehicle_type)
        except TrafficFineProviderError as exc:
            return {
                "status": "error",
                "message": str(exc),
                "plate_number": normalized_plate,
                "vehicle_type": normalized_vehicle_type,
                "violations": [],
                "searched_at": self._timestamp(),
                "source_note": None,
                "source": "https://www.csgt.vn/tra-cuu-phat-nguoi",
            }

        violations = provider_result.violations
        status = "success" if violations else "empty"
        message = (
            f"Tìm thấy {len(violations)} vi phạm." if violations else "Không tìm thấy vi phạm hoặc chưa có dữ liệu phù hợp."
        )

        return {
            "status": status,
            "message": message,
            "plate_number": normalized_plate,
            "vehicle_type": normalized_vehicle_type,
            "violations": violations,
            "searched_at": self._timestamp(),
            "source_note": provider_result.raw_message,
            "source": provider_result.source,
        }

    def _normalize_plate_number(self, plate_number: str) -> str:
        return "".join(plate_number.strip().upper().split())

    def _normalize_vehicle_type(self, vehicle_type: str) -> str:
        return vehicle_type if vehicle_type in {"car", "motorbike", "electricbike"} else "car"

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
