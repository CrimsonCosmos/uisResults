#!/usr/bin/env python3
"""
Athletic.net Result Notifier

Monitors specified athletes and sends email notifications when new results are posted.
Designed to run continuously or be triggered by a scheduler.

Setup:
1. Create a Gmail App Password:
   - Go to https://myaccount.google.com/apppasswords
   - Generate a new app password for "Mail"
   - Set the GMAIL_APP_PASSWORD environment variable

2. Configure watched athletes in notify_config.json

3. Run: python notify.py
   Or for one-time check: python notify.py --once
"""

import os
import sys
import json
import time
import smtplib
import argparse
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

# File paths
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "notify_config.json"
STATE_FILE = SCRIPT_DIR / "notify_state.json"


class AthleticNetNotifier:
    """Monitors athletes and sends notifications for new results."""

    API_BASE = "https://www.athletic.net/api/v1"

    def __init__(self, config_path=CONFIG_FILE):
        self.config = self._load_config(config_path)
        self.state = self._load_state()
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        })

    def _load_config(self, path):
        """Load configuration from JSON file."""
        with open(path) as f:
            return json.load(f)

    def _load_state(self):
        """Load state (seen results) from JSON file."""
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
        return {"seen_results": {}}

    def _save_state(self):
        """Save state to JSON file."""
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)

    def get_athlete_results(self, athlete_id, sport='xc'):
        """Fetch athlete's results from the API."""
        sport_code = 'xc' if sport == 'xc' else 'tf'

        try:
            resp = self.session.get(
                f"{self.API_BASE}/AthleteBio/GetAthleteBioData",
                params={'athleteId': athlete_id, 'sport': sport_code, 'level': 0},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                # XC uses resultsXC, Track uses resultsTF
                if sport == 'xc':
                    return data.get('resultsXC', [])
                else:
                    return data.get('resultsTF', [])
        except Exception as e:
            print(f"  Error fetching results for {athlete_id}: {e}")
        return []

    def check_for_new_results(self, athlete):
        """Check if an athlete has new results since last check."""
        athlete_id = athlete['id']
        athlete_name = athlete['name']
        new_results = []

        # Get seen results for this athlete
        seen_key = f"athlete_{athlete_id}"
        seen_results = set(self.state["seen_results"].get(seen_key, []))

        for sport in athlete.get('sports', ['xc']):
            results = self.get_athlete_results(athlete_id, sport)

            for result in results:
                # Create unique result ID
                result_id = f"{result.get('MeetID')}_{result.get('IDResult', result.get('Result', ''))}"

                if result_id not in seen_results:
                    # New result found!
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

        # Update state
        self.state["seen_results"][seen_key] = list(seen_results)

        return new_results

    def send_email(self, subject, body):
        """Send email notification via Gmail SMTP."""
        email_config = self.config['email']

        # Get app password from environment
        app_password = os.environ.get('GMAIL_APP_PASSWORD')
        if not app_password:
            print("ERROR: GMAIL_APP_PASSWORD environment variable not set")
            print("Create an app password at: https://myaccount.google.com/apppasswords")
            return False

        try:
            msg = MIMEMultipart()
            msg['From'] = email_config['sender']
            msg['To'] = email_config['recipient']
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'html'))

            with smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port']) as server:
                server.starttls()
                server.login(email_config['sender'], app_password)
                server.send_message(msg)

            print(f"  Email sent: {subject}")
            return True

        except Exception as e:
            print(f"  Error sending email: {e}")
            return False

    def format_result_email(self, result):
        """Format a result into an HTML email body."""
        pr_badge = " 🏆 <strong style='color: gold;'>PR!</strong>" if result['is_pr'] else ""
        sr_badge = " 🥈 <strong style='color: silver;'>SR!</strong>" if result['is_sr'] and not result['is_pr'] else ""

        html = f"""
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
        return html

    def check_all_athletes(self):
        """Check all watched athletes for new results."""
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking for new results...")

        total_new = 0

        for athlete in self.config['watched_athletes']:
            print(f"  Checking {athlete['name']}...", end=' ')
            new_results = self.check_for_new_results(athlete)

            if new_results:
                print(f"found {len(new_results)} new result(s)!")
                total_new += len(new_results)

                for result in new_results:
                    # Send email for each new result
                    pr_text = " - PR!" if result['is_pr'] else ""
                    subject = f"🏃 {result['athlete_name']}: {result['result']} in {result['event']}{pr_text}"
                    body = self.format_result_email(result)
                    self.send_email(subject, body)
            else:
                print("no new results")

        # Save state after checking all athletes
        self._save_state()

        return total_new

    def run_once(self):
        """Run a single check."""
        return self.check_all_athletes()

    def run_continuous(self):
        """Run continuously, checking at the configured interval."""
        interval = self.config.get('check_interval_seconds', 60)
        print(f"Starting continuous monitoring (checking every {interval} seconds)")
        print(f"Watching {len(self.config['watched_athletes'])} athlete(s)")
        print("Press Ctrl+C to stop\n")

        try:
            while True:
                self.check_all_athletes()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopping notifier...")

    def initialize_state(self):
        """Initialize state with current results (so we don't notify for old results)."""
        print("Initializing state with current results...")

        for athlete in self.config['watched_athletes']:
            athlete_id = athlete['id']
            seen_key = f"athlete_{athlete_id}"
            seen_results = set()

            print(f"  Loading {athlete['name']}...", end=' ')

            for sport in athlete.get('sports', ['xc']):
                results = self.get_athlete_results(athlete_id, sport)
                for result in results:
                    result_id = f"{result.get('MeetID')}_{result.get('IDResult', result.get('Result', ''))}"
                    seen_results.add(result_id)

            self.state["seen_results"][seen_key] = list(seen_results)
            print(f"found {len(seen_results)} existing results")

        self._save_state()
        print("State initialized. Future results will trigger notifications.\n")


def main():
    parser = argparse.ArgumentParser(description='Athletic.net Result Notifier')
    parser.add_argument('--once', action='store_true',
                        help='Run once and exit (instead of continuous monitoring)')
    parser.add_argument('--init', action='store_true',
                        help='Initialize state with current results (run this first!)')
    parser.add_argument('--test-email', action='store_true',
                        help='Send a test email to verify configuration')
    args = parser.parse_args()

    notifier = AthleticNetNotifier()

    if args.test_email:
        print("Sending test email...")
        success = notifier.send_email(
            "🧪 Test Email from UIS Athletics Notifier",
            "<h1>Test Email</h1><p>If you receive this, email notifications are working!</p>"
        )
        if success:
            print("Test email sent successfully!")
        else:
            print("Failed to send test email. Check your GMAIL_APP_PASSWORD.")
        return

    if args.init:
        notifier.initialize_state()
        return

    if args.once:
        new_count = notifier.run_once()
        print(f"\nFound {new_count} new result(s)")
    else:
        notifier.run_continuous()


if __name__ == "__main__":
    main()
