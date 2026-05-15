from services.telegram_bot_service import TelegramBotService


class StubLookupService:
    def lookup(self, plate_number: str, vehicle_type: str):
        return {
            "plate_number": plate_number,
            "vehicle_type": vehicle_type,
            "status": "ok",
            "message": "done",
            "searched_at": "2026-05-15 10:00:00",
            "violations": [],
        }


class StubResearchService:
    def __init__(self, result):
        self.result = result
        self.queries: list[str] = []

    def research(self, query: str):
        self.queries.append(query)
        return self.result


def make_bot(research_result: dict):
    return TelegramBotService(
        lookup_service=StubLookupService(),
        research_service=StubResearchService(research_result),
        bot_token="token",
        webhook_secret="secret",
    )


def test_research_feature_sets_state_then_answers() -> None:
    bot = make_bot(
        {
            "status": "success",
            "message": "ok",
            "query": "mức phạt vượt đèn đỏ",
            "answer_box": "Tóm tắt đủ dài để hiển thị trong Telegram.",
            "results": [],
            "top_results": [],
            "note": None,
            "engine": "google_scrape",
            "searched_at": "2026-05-15 10:00:00",
        }
    )

    bot.process_update({"message": {"chat": {"id": 1}, "text": "Tra cứu web realtime"}})
    messages = bot.process_update({"message": {"chat": {"id": 1}, "text": "mức phạt vượt đèn đỏ"}})

    assert 1 not in bot.chat_states
    assert any("Tóm tắt nhanh" in message.text for message in messages)


def test_claude_command_bypasses_state() -> None:
    research = StubResearchService(
        {
            "status": "success",
            "message": "ok",
            "query": "học phí đại học công lập 2026",
            "answer_box": None,
            "results": [],
            "top_results": [],
            "note": None,
            "engine": "google_scrape",
            "searched_at": "2026-05-15 10:00:00",
        }
    )
    bot = TelegramBotService(
        lookup_service=StubLookupService(),
        research_service=research,
        bot_token="token",
        webhook_secret="secret",
    )

    bot.process_update({"message": {"chat": {"id": 1}, "text": "/claude học phí đại học công lập 2026"}})

    assert research.queries == ["học phí đại học công lập 2026"]


def test_chunk_message_splits_long_text() -> None:
    bot = make_bot(
        {
            "status": "success",
            "message": "ok",
            "query": "x",
            "answer_box": None,
            "results": [],
            "top_results": [],
            "note": None,
            "engine": "google_scrape",
            "searched_at": "2026-05-15 10:00:00",
        }
    )

    chunks = bot._chunk_message(("A" * 3600) + "\n\n" + ("B" * 3600))

    assert len(chunks) >= 2
    assert all(len(chunk) <= bot.TELEGRAM_MESSAGE_LIMIT for chunk in chunks)
