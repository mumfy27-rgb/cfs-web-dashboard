from flask import Flask
from datetime import datetime
import requests


app = Flask(__name__)





def get_status_priority(status):
    status = str(status).upper()

    if status == "GOING":
        return 0
    if status == "RESPONDING":
        return 1
    if status == "MONITOR":
        return 2
    if status == "Controled":
        return 3
    
    return 4

def get_status_colour_name(status):
    status = str(status).upper()

    if status == "GOING":
        return "red"
    if status == "RESPONDING":
        return "orange"
    if status == "MONITOR":
        return "yellow"
    if status == "COMPLETE":
        return "lime"
    if status == "CONTROLLED":
        return "lime"
    
    return "white"
    


def get_incident_age(incident):
    try:
        date_text = incident.get("Date", "")
        time_text = incident.get("Time", "")

        incident_datetime = datetime.strptime(
            f"{date_text} {time_text}",
            "%d/%m/%Y %H:%M"
        )

        age_minutes = int(
            (datetime.now() - incident_datetime).total_seconds() /60
        )

        if age_minutes < 60:
            return f"{age_minutes}m"
        
        hours = age_minutes // 60
        minutes = age_minutes % 60

        return f"{hours}h {minutes}m"
    
    except Exception as error:
        return f"AGE ERROR: {error}"
    

def get_incident_age_colour(incident):
    try:
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


 
     
    

@app.route("/")
def home():
    now = datetime.now().strftime("%d/%m/%Y %H:%M")


    response = requests.get(
        "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_current_incidents.json",
        timeout= 10
    )

    incidents = response.json()

    incidents.sort(
        key=lambda incident: get_status_priority(
            incident.get("Status")
        )
    )
    
    
    filtered_incidents = incidents

    incident_lines = ""

    
    
    for incident in filtered_incidents:
        age = get_incident_age(incident)
        age_colour = get_incident_age_colour(incident)

        status = incident.get("Status")
        colour = get_status_colour_name(status)

        incident_lines += (
            f'<div>'
            f'<span style="color:{colour};">{status}</span>'
            f' | {incident.get("Type")}'
            f' | {incident.get("Location_name")}'
            f' | <span style="color:{age_colour};">Age: {age}</span>'
            f'</div>'
        )
    incident_text = incident_lines
        

        

    incident_text = incident_lines

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="background:black; color:white; margin:20px;">

    <div style="font-family: monospace: white-space: pre;">
    CFS Dashboard Test

    Last updated: {now}

    Total incidents: {len(filtered_incidents)}

    {incident_text}

    </div>

    </body>
    </html>
    """
        


    

    
   

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
