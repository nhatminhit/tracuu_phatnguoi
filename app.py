import os

from flask import Flask, jsonify, render_template, request

from services.telegram_bot_service import TelegramBotService
from services.traffic_fine_service import TrafficFineService

app = Flask(__name__)
service = TrafficFineService()
telegram_bot = TelegramBotService(service)


@app.get("/")
def index():
    return render_template("index.html", result=None, plate_number="", vehicle_type="car")


@app.post("/")
def lookup():
    plate_number = request.form.get("plate_number", "")
    vehicle_type = request.form.get("vehicle_type", "car")
    result = service.lookup(plate_number, vehicle_type)
    return render_template(
        "index.html",
        result=result,
        plate_number=plate_number,
        vehicle_type=vehicle_type,
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "telegram_configured": telegram_bot.is_configured(),
    }


@app.post("/telegram/webhook/<secret>")
def telegram_webhook(secret: str):
    if not telegram_bot.is_valid_secret(secret):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    update = request.get_json(silent=True) or {}
    for outgoing_message in telegram_bot.process_update(update):
        telegram_bot.send_message(outgoing_message)
    return {"ok": True}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
