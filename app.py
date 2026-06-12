import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, render_template, jsonify, request



from bs4 import BeautifulSoup


app = Flask(__name__)

CFS_INCIDENTS_URL = "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_current_incidents.json"
PAGER_URL = "http://paging1.sacfs.org/cfs.php"
PAGER_CACHE_SECONDS = 300
SA_TZ = ZoneInfo("Australia/Adelaide")

last_pager_message = "No pager message loaded yet."
last_pager_fetch_time = 0
pager_lock = threading.Lock()


def get_sa_now_naive():
    """Return South Australian local time as a naive datetime.

    The CFS feed Date/Time values are plain local wall-clock values, not timezone-aware
    timestamps. Render runs in UTC, so always compare them to Adelaide wall-clock time.
    """
    return datetime.now(SA_TZ).replace(tzinfo=None)


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


def fetch_pager_messages():
    try:
        url = "http://urgmsg.net/livenosaas/"

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text("\n")

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        messages = []

        for line in lines:
            if (
                "MFS:" in line
                or "CFSRES" in line
                or "NOTIFICATION" in line
                or "SPRG" in line
            ):
                messages.append(line)

        return "\n".join(messages)

    except Exception as e:
        print("Pager scrape failed:", e)
        return ""


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
    if not pager_message or pager_message == "No matching pager message found.":
        return empty_resources()

    message = str(pager_message)

    # Remove trailing response text so the last colon block is the appliance/resource block.
    for marker in [" - CFS", " - MFS", " - SES"]:
        if marker in message:
            message = message.split(marker)[0]

    # The pager message often ends like:
    #   ... DETAILS :GRNP44 PTL20_09 : - CFS Lincoln Response
    # Splitting and ignoring empty colon blocks avoids grabbing the final blank bit.
    parts = [part.strip() for part in message.split(":") if part.strip()]

    if not parts:
        return empty_resources()

    raw_resources = parts[-1]

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

        # Ignore dispatch desk / non-appliance codes.
        if resource.startswith("AIRDESK"):
            continue

        # Ignore bare numbers.
        if resource.isdigit():
            continue

        # Accept real-looking resource codes:
        # WAIKURP, TLEM44, GLWAURP_R, RIDG_BW13, R1_GREEN, PTL20_09
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
            (get_sa_now_naive() - incident_datetime).total_seconds() / 60
        )

        if age_minutes < 0:
            age_minutes = 0

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
            (get_sa_now_naive() - incident_datetime).total_seconds() / 60
        )

        if age_minutes < 0:
            age_minutes = 0

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

    pager_text = fetch_pager_messages()

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
    hide_burns = request.args.get("hide_burns", "0") == "1"

    now = datetime.now(SA_TZ).strftime("%d/%m/%Y %H:%M")

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

    if hide_burns:
        incidents = [
            incident for incident in incidents
            if "BURN" not in str(incident.get("Type", "")).upper()
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
        selected_region=selected_region,
        hide_burns=hide_burns
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )