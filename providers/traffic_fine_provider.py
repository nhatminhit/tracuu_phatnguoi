from __future__ import annotations

from dataclasses import dataclass
import re

import requests
from bs4 import BeautifulSoup


class TrafficFineProviderError(Exception):
    pass


@dataclass
class ProviderResult:
    source: str
    violations: list[dict[str, str]]
    raw_message: str | None = None


class TrafficFineProvider:
    CSGT_LOOKUP_PAGE_URL = "https://www.csgt.vn/tra-cuu-phat-nguoi"
    CSGT_LOOKUP_ACTION_URL = "https://www.csgt.vn/tra-cuu-vi-pham-qua-hinh-anh"
    PHATNGUOI_APP_URL = "https://phatnguoi.app/"
    PHATNGUOI_APP_AJAX_URL = "https://phatnguoi.app/wp-admin/admin-ajax.php"
    PHATNGUOI_VN_URL = "https://phatnguoi.vn/"
    VNTRAFFIC_URL = "https://vntraffic.org/"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            }
        )

    def lookup(self, plate_number: str, vehicle_type: str) -> ProviderResult:
        notes: list[str] = []

        try:
            return self._lookup_via_csgt(plate_number, vehicle_type)
        except TrafficFineProviderError as exc:
            notes.append(f"CSGT: {exc}")

        try:
            fallback_result = self._lookup_via_phatnguoi_app(plate_number, vehicle_type)
            fallback_result.raw_message = None
            return fallback_result
        except TrafficFineProviderError as exc:
            notes.append(f"phatnguoi.app: {exc}")

        notes.extend(self._probe_other_sources())
        raise TrafficFineProviderError(" | ".join(notes))

    def _lookup_via_csgt(self, plate_number: str, vehicle_type: str) -> ProviderResult:
        page_html = self._fetch_csgt_lookup_page()
        token = self._extract_csgt_csrf_token(page_html)
        if not token:
            raise TrafficFineProviderError("Không lấy được mã CSRF từ nguồn tra cứu CSGT.")

        response = self._submit_csgt_lookup(token, plate_number, vehicle_type)
        if response.status_code == 419 or "CSRF token mismatch" in response.text:
            raise TrafficFineProviderError(
                "Nguồn CSGT chặn yêu cầu máy-chủ tới máy-chủ bằng CSRF token."
            )
        if response.status_code == 422:
            raise TrafficFineProviderError("Nguồn CSGT yêu cầu reCAPTCHA hợp lệ trước khi trả kết quả tra cứu.")
        if response.status_code == 429:
            raise TrafficFineProviderError("Nguồn CSGT đang giới hạn tần suất tra cứu.")
        if response.status_code >= 400:
            raise TrafficFineProviderError(f"Nguồn CSGT trả lỗi HTTP {response.status_code}.")

        payload = self._parse_json_payload(response, "CSGT")
        if payload.get("show_recaptcha_v2"):
            raise TrafficFineProviderError("Nguồn CSGT yêu cầu xác minh reCAPTCHA bổ sung trước khi tra cứu.")

        result_html = payload.get("resultHtml", "")
        violations = self._parse_csgt_result_html(result_html, plate_number)
        return ProviderResult(
            source=self.CSGT_LOOKUP_PAGE_URL,
            violations=violations,
            raw_message=self._extract_message(result_html),
        )

    def _lookup_via_phatnguoi_app(self, plate_number: str, vehicle_type: str) -> ProviderResult:
        nonce = self._fetch_phatnguoi_app_nonce()
        mapped_vehicle_type = "moto" if vehicle_type == "motorbike" else "car"
        payload = {
            "action": "phatnguoi_search",
            "nonce": nonce,
            "license_plate": plate_number,
            "vehicle_type": mapped_vehicle_type,
        }
        try:
            response = self.session.post(
                self.PHATNGUOI_APP_AJAX_URL,
                data=payload,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": self.PHATNGUOI_APP_URL,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TrafficFineProviderError("Không gọi được AJAX của phatnguoi.app.") from exc

        payload = self._parse_json_payload(response, "phatnguoi.app")
        if not payload.get("success"):
            data = payload.get("data") or {}
            message = data.get("message") if isinstance(data, dict) else None
            raise TrafficFineProviderError(message or "phatnguoi.app trả lỗi tra cứu.")

        data = payload.get("data") or {}
        violations = [self._map_phatnguoi_app_violation(item) for item in data.get("violations", [])]
        summary = (
            f"Fallback qua phatnguoi.app lúc {data.get('last_updated')}"
            if data.get("last_updated")
            else "Fallback qua phatnguoi.app"
        )
        return ProviderResult(
            source=self.PHATNGUOI_APP_URL,
            violations=violations,
            raw_message=summary,
        )

    def _probe_other_sources(self) -> list[str]:
        notes = []
        try:
            html = self.session.get(self.PHATNGUOI_VN_URL, timeout=self.timeout).text
            if all(marker in html for marker in ["name=\"tracuu_nonce\"", "name=\"BienKS\"", "turnstile"]):
                notes.append("phatnguoi.vn sẵn form nhưng đang chặn bằng Cloudflare Turnstile")
        except requests.RequestException:
            pass

        try:
            html = self.session.get(self.VNTRAFFIC_URL, timeout=self.timeout).text
            if "Không yêu cầu captcha" in html or "không yêu cầu captcha" in html:
                notes.append("vntraffic.org là nguồn tham chiếu nội dung, chưa thấy endpoint tra cứu công khai")
        except requests.RequestException:
            pass
        return notes

    def _fetch_csgt_lookup_page(self) -> str:
        try:
            response = self.session.get(self.CSGT_LOOKUP_PAGE_URL, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TrafficFineProviderError("Không thể kết nối trang tra cứu CSGT.") from exc
        if not response.text.strip():
            raise TrafficFineProviderError("Trang tra cứu CSGT không trả dữ liệu.")
        return response.text

    def _extract_csgt_csrf_token(self, html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        token_input = soup.select_one("form#violationsForm input[name='_token']")
        return token_input.get("value") if token_input else None

    def _submit_csgt_lookup(self, token: str, plate_number: str, vehicle_type: str) -> requests.Response:
        payload = {
            "_token": token,
            "vehicle_type": vehicle_type,
            "plate_number": plate_number,
            "g-recaptcha-response": "",
        }
        try:
            return self.session.post(
                self.CSGT_LOOKUP_ACTION_URL,
                data=payload,
                headers={
                    "Referer": self.CSGT_LOOKUP_PAGE_URL,
                    "Origin": "https://www.csgt.vn",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TrafficFineProviderError("Không gửi được yêu cầu tra cứu tới CSGT.") from exc

    def _fetch_phatnguoi_app_nonce(self) -> str:
        try:
            response = self.session.get(self.PHATNGUOI_APP_URL, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TrafficFineProviderError("Không tải được trang phatnguoi.app.") from exc
        match = re.search(r"nonce:\s*'([^']+)'", response.text)
        if not match:
            raise TrafficFineProviderError("Không lấy được nonce từ phatnguoi.app.")
        return match.group(1)

    def _parse_json_payload(self, response: requests.Response, source_name: str) -> dict:
        try:
            return response.json()
        except ValueError as exc:
            raise TrafficFineProviderError(f"{source_name} trả dữ liệu không đúng định dạng JSON mong đợi.") from exc

    def _extract_message(self, result_html: str) -> str | None:
        if not result_html:
            return None
        soup = BeautifulSoup(result_html, "html.parser")
        alert = soup.select_one(".alert")
        if alert:
            return " ".join(alert.stripped_strings)
        text = " ".join(soup.stripped_strings)
        return text or None

    def _parse_csgt_result_html(self, result_html: str, plate_number: str) -> list[dict[str, str]]:
        if not result_html:
            return []
        soup = BeautifulSoup(result_html, "html.parser")
        rows = []
        for table_row in soup.select("tr"):
            cells = [" ".join(cell.stripped_strings) for cell in table_row.select("th,td")]
            if len(cells) < 2:
                continue
            if any(keyword in cells[0].lower() for keyword in ["biển số", "thời gian", "địa điểm"]):
                continue
            rows.append(cells)

        return [
            {
                "plate_number": plate_number,
                "violation_time": cells[0] if len(cells) > 0 else "",
                "location": cells[1] if len(cells) > 1 else "",
                "behavior": cells[2] if len(cells) > 2 else "",
                "handling_unit": cells[3] if len(cells) > 3 else "",
                "status": cells[4] if len(cells) > 4 else "",
            }
            for cells in rows
        ]

    def _map_phatnguoi_app_violation(self, item: dict) -> dict[str, str]:
        status = item.get("status_text") or ("Đã xử phạt" if item.get("status") == "paid" else "Chưa xử phạt")
        resolution_location = item.get("resolution_location")
        if isinstance(resolution_location, list):
            handling_unit = " | ".join(str(part) for part in resolution_location if part)
        else:
            handling_unit = str(resolution_location or item.get("unit") or "")
        return {
            "plate_number": item.get("plate") or "",
            "violation_time": item.get("time") or "",
            "location": item.get("location") or "",
            "behavior": item.get("title") or "",
            "handling_unit": handling_unit,
            "status": status,
        }
