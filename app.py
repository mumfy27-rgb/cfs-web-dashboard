import time
import threading
from datetime import datetime

import requests
from flask import Flask, render_template, jsonify, request

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By


app = Flask(__name__)

CFS_INCIDENTS_URL = "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_current_incidents.json"
PAGER_URL = "http://paging1.sacfs.org/cfs.php"
PAGER_CACHE_SECONDS = 300

last_pager_message = "No pager message loaded yet."
last_pager_fetch_time = 0
pager_lock = threading.Lock()


def empty_resources():
    return {
        "raw_resources": "No resources found.",
        "appliances": [],
        "bulk_water_carriers": [],
        "officers": []
    }


def get_status_priority(status):
    status = str(status).upper()

    if status == "GOING":
        return 0
    if status == "RESPONDING":
        return 1
    if status == "MONITOR":
        return 2
    if status == "CONTROLLED":
        return 3

    return 4


def get_status_colour_name(status):
    status = str(status).upper()

    if status == "GOING":
        return "lime"
    if status == "RESPONDING":
        return "orange"
    if status == "MONITOR":
        return "yellow"
    if status == "COMPLETE":
        return "white"
    if status == "CONTROLLED":
        return "white"

    return "white"


def get_incident_card_colour(incident_type):
    incident_type = str(incident_type).upper()

    if "FIRE" in incident_type:
        return "#8B0000"
    if "VEHICLE ACCIDENT" in incident_type:
        return "#B8860B"
    if "MVA" in incident_type:
        return "#B8860B"
    if "RESCUE" in incident_type:
        return "#4682B4"
    if "TREE" in incident_type:
        return "#006400"
    if "BURN" in incident_type:
        return "#CC6600"
    if "ASSIST" in incident_type:
        return "#6A0DAD"

    return "#333333"


def fetch_pager_message():
    global last_pager_message, last_pager_fetch_time

    now = time.time()

    if now - last_pager_fetch_time < PAGER_CACHE_SECONDS:
        print("Pager: returning cached result")
        return last_pager_message

    if not pager_lock.acquire(blocking=False):
        print("Pager: lock busy, returning cached result")
        return last_pager_message

    driver = None

    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--log-level=3")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        driver = webdriver.Chrome(
            service=Service("/usr/bin/chromedriver"),
            options=options
        )

        driver.set_page_load_timeout(20)
        driver.get(PAGER_URL)

        time.sleep(5)

        body = driver.find_element(By.TAG_NAME, "body").text

        last_pager_message = body
        last_pager_fetch_time = time.time()

        print("Pager: scrape successful")
        return body

    except Exception as error:
        print(f"Pager scrape failed: {error}")
        return last_pager_message

    finally:
        if driver is not None:
            driver.quit()
        pager_lock.release()


def build_pager_messages(pager_text):
    messages = []
    current_message = ""

    for line in pager_text.split("\n"):
        line = line.strip()

        if not line:
            continue

        line_upper = line.upper()

        starts_new_message = (
            "MFS:" in line_upper
            or "NOTIFICATION" in line_upper
            or "*CFSRES" in line_upper
            or "SPRG" in line_upper
        )

        if starts_new_message:
            if current_message:
                messages.append(current_message.strip())

            current_message = line

        else:
            if current_message:
                current_message += " " + line

    if current_message:
        messages.append(current_message.strip())

    return messages

def find_matching_pager_message(pager_text, incident):
    location = str(incident.get("Location_name", "")).upper()
    incident_type = str(incident.get("Type", "")).upper()

    incident_date = str(incident.get("Date", ""))
    incident_time = str(incident.get("Time", ""))

    pager_date = incident_date.replace("/20", "/")

    ignored_words = [
        "PAGER TEST",
        "TEST ONLY",
        "TRAINING",
        "REMINDER",
        "NOTIFICATION"
    ]

    messages = build_pager_messages(pager_text)

    best_message = "No matching pager message found."
    best_score = 0

    location_words = (
        location
        .replace(",", " ")
        .replace("/", " ")
        .split()
    )

    for message in messages:
        msg = message.upper()
        score = 0

        if any(word in msg for word in ignored_words):
            continue

        if pager_date in msg:
            score += 100

        if incident_time in msg:
            score += 100

        for word in location_words:
            if len(word) <= 3:
                continue

            if word in msg:
                score += 40

        if "BUILDING FIRE" in incident_type and "STRUCTURE FIRE" in msg:
            score += 100

        elif "BUILDING FIRE" in incident_type and "FIRE" in msg:
            score += 50

        elif "FIRE" in incident_type and "FIRE" in msg:
            score += 20

        if "RUBBISH" in incident_type and "RUBBISH" in msg:
            score += 60

        if "WASTE" in incident_type and "WASTE" in msg:
            score += 60

        if "VEHICLE ACCIDENT" in incident_type and "VEHICLE ACCIDENT" in msg:
            score += 60

        if "HAZMAT" in incident_type and "HAZMAT" in msg:
            score += 15

        if "SMOKE" in incident_type and "SMOKE" in msg:
            score += 15

        if "RESCUE" in incident_type and "RESCUE" in msg:
            score += 15

        if "ASSIST" in incident_type and "ASSIST" in msg:
            score += 15

        if score > best_score:
            best_score = score
            best_message = message

    if best_score >= 40:
        return best_message

    return "No matching pager message found."

def extract_resources_from_pager(pager_message):
    if pager_message == "No matching pager message found.":
        return empty_resources()

    message = str(pager_message)

    if ": -" in message:
        message = message.split(": -")[0]
    elif ":-" in message:
        message = message.split(":-")[0]

    if ":" not in message:
        return empty_resources()

    raw_resources = message.split(":")[-1].strip()

    if not raw_resources:
        return empty_resources()

    tokens = raw_resources.replace(",", " ").replace(";", " ").split()

    appliances = []
    bulk_water_carriers = []
    officers = []

    for token in tokens:
        resource = token.strip(" ,.;:-()").upper()

        if not resource:
            continue

        # Ignore dispatch desk
        if resource.startswith("AIRDESK"):
            continue

        # Ignore bare numbers
        if resource.isdigit():
            continue

        # Accept real-looking resource codes:
        # WAIKURP, TLEM44, GLWAURP_R, RIDG_BW13, R1_GREEN
        looks_like_resource = (
            any(char.isdigit() for char in resource)
            or "_" in resource
            or resource.endswith("URP")
            or resource.endswith("QRV")
        )

        if not looks_like_resource:
            continue

        if "_BW" in resource:
            bulk_water_carriers.append(resource)

        elif "_GREEN" in resource:
            officers.append(resource)

        else:
            appliances.append(resource)

    if not appliances and not bulk_water_carriers and not officers:
        return empty_resources()

    return {
        "raw_resources": raw_resources,
        "appliances": appliances,
        "bulk_water_carriers": bulk_water_carriers,
        "officers": officers
    }
def get_incident_age(incident):
    try:
        incident_type = str(incident.get("Type", "")).upper()

        if "PRESCRIBED" in incident_type or "BURN OFF" in incident_type:
            return ""

        date_text = incident.get("Date", "")
        time_text = incident.get("Time", "")

        incident_datetime = datetime.strptime(
            f"{date_text} {time_text}",
            "%d/%m/%Y %H:%M"
        )

        age_minutes = int(
            (datetime.now() - incident_datetime).total_seconds() / 60
        )

        if age_minutes < 60:
            return f"{age_minutes}m"

        hours = age_minutes // 60
        minutes = age_minutes % 60

        return f"{hours}h {minutes}m"

    except Exception:
        return "Unknown"


def get_incident_age_colour(incident):
    try:
        incident_type = str(incident.get("Type", "")).upper()

        if "PRESCRIBED" in incident_type or "BURN OFF" in incident_type:
            return "grey"

        date_text = incident.get("Date", "")
        time_text = incident.get("Time", "")

        incident_datetime = datetime.strptime(
            f"{date_text} {time_text}",
            "%d/%m/%Y %H:%M"
        )

        age_minutes = int(
            (datetime.now() - incident_datetime).total_seconds() / 60
        )

        if age_minutes < 30:
            return "lime"

        if age_minutes < 120:
            return "orange"

        return "red"

    except Exception:
        return "grey"


@app.route("/pager-match", methods=["POST"])
def pager_match():
    incident = request.get_json()

    incident_type = str(incident.get("Type", "")).upper()

    if "PRESCRIBED" in incident_type or "BURN OFF" in incident_type:
        return jsonify({
            "pager_message": "No Pager Message available",
            "resources": empty_resources()
        })

    pager_text = fetch_pager_message()

    pager_message = find_matching_pager_message(
        pager_text,
        incident
    )

    resources = extract_resources_from_pager(
        pager_message
    )

    return jsonify({
        "pager_message": pager_message,
        "resources": resources
    })


@app.route("/")
def home():
    selected_region = request.args.get("region", "STATEWIDE")

    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    response = requests.get(
        CFS_INCIDENTS_URL,
        timeout=10
    )

    incidents = response.json()

    if selected_region != "STATEWIDE":
        incidents = [
            incident for incident in incidents
            if str(incident.get("Region", "")) == selected_region
        ]

    incidents.sort(
        key=lambda incident: get_status_priority(
            incident.get("Status")
        )
    )

    for incident in incidents:
        incident["Age"] = get_incident_age(incident)
        incident["AgeColour"] = get_incident_age_colour(incident)

        incident["StatusColour"] = get_status_colour_name(
            incident.get("Status")
        )

        incident["CardColour"] = get_incident_card_colour(
            incident.get("Type")
        )

    return render_template(
        "index.html",
        incidents=incidents,
        now=now,
        selected_region=selected_region
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )