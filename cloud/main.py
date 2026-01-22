"""
Google Cloud Function for Athletic.net Result Notifications

Triggered by Cloud Scheduler every minute.
Checks for new results and sends email notifications.
Uses Firestore for state storage.
"""

import os
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from google.cloud import firestore

# Configuration - set these as environment variables in Cloud Function
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD')
RECIPIENT_EMAIL = os.environ.get('RECIPIENT_EMAIL', 'dylangehl31@gmail.com')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'dylangehl31@gmail.com')

# Watched athletes configuration
WATCHED_ATHLETES = [
    {"id": "29640757", "name": "Chase Cooley", "sports": ["xc", "tf"]},
    {"id": "1740070", "name": "Dylan Gehl", "sports": ["xc", "tf"]},
]

# Firestore client (initialized lazily)
db = None


def get_db():
    """Get Firestore client (lazy initialization)."""
    global db
    if db is None:
        db = firestore.Client()
    return db


def get_athlete_results(athlete_id, sport='xc'):
    """Fetch athlete's results from Athletic.net API."""
    api_base = "https://www.athletic.net/api/v1"
    sport_code = 'xc' if sport == 'xc' else 'tf'

    try:
        resp = requests.get(
            f"{api_base}/AthleteBio/GetAthleteBioData",
            params={'athleteId': athlete_id, 'sport': sport_code, 'level': 0},
            headers={
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (compatible; UISNotifier/1.0)',
            },
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if sport == 'xc':
                return data.get('resultsXC', [])
            else:
                return data.get('resultsTF', [])
    except Exception as e:
        print(f"Error fetching results for {athlete_id}: {e}")
    return []


def get_seen_results(athlete_id):
    """Get set of seen result IDs from Firestore."""
    doc_ref = get_db().collection('athlete_state').document(str(athlete_id))
    doc = doc_ref.get()
    if doc.exists:
        return set(doc.to_dict().get('seen_results', []))
    return set()


def save_seen_results(athlete_id, seen_results):
    """Save seen result IDs to Firestore."""
    doc_ref = get_db().collection('athlete_state').document(str(athlete_id))
    doc_ref.set({'seen_results': list(seen_results), 'updated': datetime.utcnow()})


def send_email(subject, body):
    """Send email notification via Gmail SMTP."""
    if not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_APP_PASSWORD not set")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT_EMAIL
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        print(f"Email sent: {subject}")
        return True

    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def format_result_email(result):
    """Format a result into an HTML email body."""
    pr_badge = " 🏆 <strong style='color: gold;'>PR!</strong>" if result['is_pr'] else ""
    sr_badge = " 🥈 <strong style='color: silver;'>SR!</strong>" if result['is_sr'] and not result['is_pr'] else ""

    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #1a73e8;">New Result for {result['athlete_name']}{pr_badge}{sr_badge}</h2>

        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Event:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{result['event']}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Result:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; font-size: 1.2em;"><strong>{result['result']}</strong></td>
            </tr>
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Place:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{result['place']}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Meet:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{result['meet_name']}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;"><strong>Date:</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{result['meet_date']}</td>
            </tr>
        </table>

        <p style="color: #666; font-size: 0.9em;">
            Sent by UIS Athletics Notifier
        </p>
    </div>
    """


def check_athlete(athlete):
    """Check if an athlete has new results."""
    athlete_id = athlete['id']
    athlete_name = athlete['name']
    new_results = []

    seen_results = get_seen_results(athlete_id)
    original_count = len(seen_results)

    for sport in athlete.get('sports', ['xc']):
        results = get_athlete_results(athlete_id, sport)

        for result in results:
            result_id = f"{result.get('MeetID')}_{result.get('IDResult', result.get('Result', ''))}"

            if result_id not in seen_results:
                new_results.append({
                    'athlete_name': athlete_name,
                    'sport': sport,
                    'event': result.get('Event') or f"{result.get('Distance', '')}m",
                    'result': result.get('Result', ''),
                    'meet_name': result.get('MeetName', 'Unknown Meet'),
                    'meet_date': result.get('MeetDate', '')[:10] if result.get('MeetDate') else '',
                    'place': result.get('Place', ''),
                    'is_pr': result.get('PersonalBest', False),
                    'is_sr': result.get('SeasonBest', False),
                })
                seen_results.add(result_id)

    # Save updated state if changed
    if len(seen_results) > original_count:
        save_seen_results(athlete_id, seen_results)

    return new_results


def check_results(request):
    """
    Cloud Function entry point.
    Triggered by Cloud Scheduler HTTP request.
    """
    print(f"[{datetime.utcnow().isoformat()}] Checking for new results...")

    total_new = 0

    for athlete in WATCHED_ATHLETES:
        print(f"  Checking {athlete['name']}...", end=' ')
        new_results = check_athlete(athlete)

        if new_results:
            print(f"found {len(new_results)} new result(s)!")
            total_new += len(new_results)

            for result in new_results:
                pr_text = " - PR!" if result['is_pr'] else ""
                subject = f"🏃 {result['athlete_name']}: {result['result']} in {result['event']}{pr_text}"
                body = format_result_email(result)
                send_email(subject, body)
        else:
            print("no new results")

    return f"Checked {len(WATCHED_ATHLETES)} athletes, found {total_new} new results", 200


def initialize_state(request):
    """
    One-time function to initialize state with existing results.
    Run this BEFORE enabling the scheduler to avoid spam.
    """
    print("Initializing state with existing results...")

    for athlete in WATCHED_ATHLETES:
        athlete_id = athlete['id']
        seen_results = set()

        print(f"  Loading {athlete['name']}...")

        for sport in athlete.get('sports', ['xc']):
            results = get_athlete_results(athlete_id, sport)
            for result in results:
                result_id = f"{result.get('MeetID')}_{result.get('IDResult', result.get('Result', ''))}"
                seen_results.add(result_id)

        save_seen_results(athlete_id, seen_results)
        print(f"    Saved {len(seen_results)} existing results")

    return "State initialized successfully", 200
