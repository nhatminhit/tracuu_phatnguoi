from flask import Flask, render_template, request

from services.traffic_fine_service import TrafficFineService

app = Flask(__name__)
service = TrafficFineService()


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


if __name__ == "__main__":
    app.run(debug=True)
