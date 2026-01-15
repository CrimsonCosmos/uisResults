#!/usr/bin/env python3
"""
UIS Athletics Results Scraper
Scrapes athletic.net for Illinois-Springfield athletes' recent results.
Checks each athlete's profile for events in the last 5 days and identifies PRs, SRs.
"""

import time
import re
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd


class AthleticNetScraper:
    """Scraper for athletic.net team results."""

    TEAM_ID = 65580
    BASE_URL = "https://www.athletic.net"

    # Sport configurations
    SPORTS = {
        'xc': {
            'name': 'Cross Country',
            'url_path': 'cross-country',
            'athlete_path': 'cross-country'
        },
        'indoor': {
            'name': 'Indoor Track & Field',
            'url_path': 'track-and-field-indoor',
            'athlete_path': 'track-and-field-indoor'
        },
        'outdoor': {
            'name': 'Outdoor Track & Field',
            'url_path': 'track-and-field-outdoor',
            'athlete_path': 'track-and-field-outdoor'
        }
    }

    def __init__(self, headless=True, year=2025, sport='xc', days_back=5):
        """Initialize the scraper with Chrome webdriver."""
        self.year = year
        self.sport = sport
        self.days_back = days_back

        if sport not in self.SPORTS:
            raise ValueError(f"Invalid sport: {sport}. Choose from: {', '.join(self.SPORTS.keys())}")

        self.sport_config = self.SPORTS[sport]
        self.team_url = f"{self.BASE_URL}/team/{self.TEAM_ID}/{self.sport_config['url_path']}/{year}"

        self.options = Options()
        if headless:
            self.options.add_argument("--headless")
        self.options.add_argument("--no-sandbox")
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--window-size=1920,1080")
        self.options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

        self.driver = None
        self.cutoff_date = datetime.now() - timedelta(days=days_back)

    def start_browser(self):
        """Start the Chrome browser."""
        print("Starting browser...")
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=self.options)

    def close_browser(self):
        """Close the browser."""
        if self.driver:
            self.driver.quit()

    def get_roster(self):
        """Get the team roster with athlete IDs."""
        print(f"Fetching roster from: {self.team_url}")
        self.driver.get(self.team_url)
        time.sleep(4)

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')

        athletes = []
        seen_ids = set()

        # Find all athlete links
        athlete_links = soup.find_all('a', href=re.compile(r'/athlete/\d+'))

        for link in athlete_links:
            href = link.get('href', '')
            athlete_id_match = re.search(r'/athlete/(\d+)', href)
            if athlete_id_match:
                athlete_id = athlete_id_match.group(1)
                if athlete_id not in seen_ids:
                    seen_ids.add(athlete_id)
                    name = link.get_text(strip=True)
                    if name:  # Only add if we have a name
                        # Clean up name - remove leading initials stuck to the name
                        # Pattern: "KHKhaniya" -> "Khaniya", "EMElijah" -> "Elijah"
                        cleaned_name = re.sub(r'^[A-Z]{2,3}(?=[A-Z][a-z])', '', name)
                        athletes.append({
                            'id': athlete_id,
                            'name': cleaned_name
                        })

        print(f"Found {len(athletes)} athletes on roster")
        return athletes

    def parse_date(self, date_str):
        """Parse date string like 'Sep 5', 'Sep 27', or 'Apr 17, 2025' into datetime."""
        # Try full date format first (Apr 17, 2025)
        try:
            return datetime.strptime(date_str, "%b %d, %Y")
        except ValueError:
            pass

        try:
            return datetime.strptime(date_str, "%B %d, %Y")
        except ValueError:
            pass

        # Try short format with assumed year
        try:
            date_with_year = f"{date_str}, {self.year}"
            return datetime.strptime(date_with_year, "%b %d, %Y")
        except ValueError:
            pass

        try:
            date_with_year = f"{date_str}, {self.year}"
            return datetime.strptime(date_with_year, "%B %d, %Y")
        except ValueError:
            return None

    def time_to_seconds(self, time_str):
        """Convert time string to seconds for comparison."""
        if not time_str:
            return float('inf')

        # Remove PR/SR markers
        time_str = re.sub(r'[PRSRprsr\s\*]+', '', time_str).strip()

        try:
            # Handle MM:SS.ss format
            if ':' in time_str:
                parts = time_str.split(':')
                if len(parts) == 2:
                    minutes = int(parts[0])
                    seconds = float(parts[1])
                    return minutes * 60 + seconds
                elif len(parts) == 3:
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = float(parts[2])
                    return hours * 3600 + minutes * 60 + seconds
            else:
                # Handle SS.ss format (sprints)
                return float(time_str)
        except (ValueError, IndexError):
            return float('inf')

        return float('inf')

    def get_athlete_results_and_bests(self, athlete_id, athlete_name):
        """Get an athlete's recent results and their best times from their profile."""
        athlete_url = f"{self.BASE_URL}/athlete/{athlete_id}/{self.sport_config['athlete_path']}"
        self.driver.get(athlete_url)
        time.sleep(2)

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')

        results = []
        bests = {}  # {event: {'pr': time, 'sr': time, 'pr_seconds': float, 'sr_seconds': float}}

        # Find all tables - results are typically in tables
        tables = soup.find_all('table')

        # We need to find the table with the current season's results
        # It will have dates in format "Sep 5" and times
        for table in tables:
            table_text = table.get_text()

            # Check if this table has recent dates (month names)
            if not re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}', table_text):
                continue

            # Parse table rows
            rows = table.find_all('tr')
            current_event = None

            for row in rows:
                row_text = row.get_text(separator=' ', strip=True)

                # Check if this row defines an event (e.g., "5000 Meters", "800 Meters")
                event_match = re.match(r'^(\d+(?:,\d+)?\s*(?:Meters?|Mile|Hurdles?|Relay|Steeplechase|Jump|Put|Throw|Vault))', row_text, re.IGNORECASE)
                if event_match:
                    current_event = event_match.group(1).strip()
                    continue

                # Look for result data: place, time, date, meet name
                # Pattern: "1 18:01.1PR Sep 5 Prairie Stars Invitational"
                # Or: "8 16:27.78PR Apr 17, 2025 Bryan Clay Invitational"
                result_pattern = re.compile(
                    r'(\d+)\s+'  # Place
                    r'(\d{1,2}:\d{2}\.\d+|\d+\.\d+)'  # Time
                    r'\s*(PR|SR)?\s*'  # Optional PR/SR marker
                    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:,?\s*(\d{4}))?\s+'  # Date with optional year
                    r'(.+?)(?:\s+\d+\s+F)?$',  # Meet name (may end with division info like "1 F")
                    re.IGNORECASE
                )

                match = result_pattern.search(row_text)
                if match and current_event:
                    place = int(match.group(1))
                    time_str = match.group(2)
                    record_type = match.group(3).upper() if match.group(3) else None
                    month = match.group(4)
                    day = match.group(5)
                    year = match.group(6) if match.group(6) else str(self.year)
                    meet_name = match.group(7).strip()

                    # Parse the date
                    date_str = f"{month} {day}, {year}"
                    result_date = self.parse_date(date_str)

                    if result_date and result_date >= self.cutoff_date:
                        results.append({
                            'athlete_name': athlete_name,
                            'athlete_id': athlete_id,
                            'event': current_event,
                            'place': place,
                            'time': time_str,
                            'record_type': record_type,  # 'PR', 'SR', or None
                            'date': result_date,
                            'date_str': date_str,
                            'meet_name': meet_name
                        })

        # Now extract PR/SR bests from the page
        # Look for summary tables that show season/career bests
        # Format: "5000 Meters 2023 Indoor Fr 18:48.71 2024 Outdoor So 17:13.53 * 2025 Indoor Jr 16:37.46 *"
        for table in tables:
            table_text = table.get_text()

            # Try to extract event name at start of table
            event_match = re.match(r'^\s*(\d+(?:,\d+)?\s*(?:Meters?|Mile|Hurdles?|Relay|Steeplechase|Jump|Put|Throw|Vault))', table_text, re.IGNORECASE)
            if not event_match:
                continue

            event = event_match.group(1).strip()

            # Check if this is a summary table (has year + Indoor/Outdoor patterns)
            if not re.search(r'\d{4}\s+(?:Indoor|Outdoor)', table_text):
                continue

            # Find all times with their PR markers
            # Pattern: time followed by optional PR marker
            time_entries = re.findall(r'(\d{1,2}:\d{2}\.\d+|\d+\.\d+)\s*(PR)?', table_text)

            if time_entries:
                # Find best (PR) and current season best (SR)
                all_times = []
                for t, is_pr in time_entries:
                    secs = self.time_to_seconds(t)
                    if secs != float('inf'):
                        all_times.append((secs, t, is_pr == 'PR'))

                if all_times:
                    # Sort to get best
                    all_times.sort(key=lambda x: x[0])
                    best = all_times[0]

                    bests[event] = {
                        'pr': best[1],
                        'pr_seconds': best[0]
                    }

                    # Try to find current year's SR for this sport
                    # Pattern: "2025 Indoor Sr 16:37.46" or similar
                    sport_label = 'Indoor' if self.sport == 'indoor' else 'Outdoor'
                    sr_pattern = re.compile(rf'{self.year}\s+{sport_label}\s+\w{{2}}\s+(\d{{1,2}}:\d{{2}}\.\d+|\d+\.\d+)')
                    sr_match = sr_pattern.search(table_text)
                    if sr_match:
                        sr_time = sr_match.group(1)
                        bests[event]['sr'] = sr_time
                        bests[event]['sr_seconds'] = self.time_to_seconds(sr_time)

        return results, bests

    def get_athlete_bests(self, athlete_id):
        """Get an athlete's PR and SR for each event."""
        athlete_url = f"{self.BASE_URL}/athlete/{athlete_id}/{self.sport_config['athlete_path']}"
        self.driver.get(athlete_url)
        time.sleep(2)

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')

        bests = {}  # {event: {'pr': time, 'sr': time}}

        # Look at the season summary tables (e.g., "5000 Meters 2022 Fr 20:14.1 2023 So 18:57.6 * ...")
        tables = soup.find_all('table')

        for table in tables:
            table_text = table.get_text()

            # Look for patterns like "5000 Meters" followed by yearly results
            event_match = re.match(r'^(\d+(?:,\d+)?\s*(?:Meters?|Mile|Hurdles?|Relay|Steeplechase|Jump|Put|Throw|Vault))', table_text, re.IGNORECASE)
            if event_match:
                event = event_match.group(1).strip()

                # Find all times in this table
                times = re.findall(r'(\d{1,2}:\d{2}\.\d+|\d+\.\d+)(PR)?', table_text)

                if times:
                    # Get the best time (lowest) as PR
                    all_times = [(t[0], t[1] == 'PR') for t in times]
                    time_values = [(self.time_to_seconds(t[0]), t[0], t[1]) for t in all_times]
                    time_values.sort(key=lambda x: x[0])

                    if time_values:
                        best = time_values[0]
                        bests[event] = {
                            'pr': best[1],
                            'pr_seconds': best[0]
                        }

                        # Try to find SR (current year's best) - look for year pattern
                        current_year_pattern = re.compile(rf'{self.year}\s+\w+\s+(\d{{1,2}}:\d{{2}}\.\d+|\d+\.\d+)')
                        sr_match = current_year_pattern.search(table_text)
                        if sr_match:
                            sr_time = sr_match.group(1)
                            bests[event]['sr'] = sr_time
                            bests[event]['sr_seconds'] = self.time_to_seconds(sr_time)

        return bests

    def calculate_improvement(self, current_time_str, previous_time_str):
        """Calculate percentage improvement (lower is better for running)."""
        current = self.time_to_seconds(current_time_str)
        previous = self.time_to_seconds(previous_time_str)

        if previous == float('inf') or previous == 0:
            return 0

        # Improvement is positive when current < previous (faster)
        improvement = (previous - current) / previous * 100
        return improvement

    def run(self):
        """Main execution method."""
        try:
            self.start_browser()

            # Step 1: Get roster
            roster = self.get_roster()

            if not roster:
                print("No athletes found on roster.")
                return None

            all_results = []

            # Step 2: Visit each athlete's profile
            print(f"\nChecking {len(roster)} athletes for results in the last {self.days_back} days...")
            print(f"Cutoff date: {self.cutoff_date.strftime('%Y-%m-%d')}")

            for i, athlete in enumerate(roster):
                print(f"  [{i+1}/{len(roster)}] {athlete['name']}...", end=' ')

                # Get recent results and bests in one page load
                results, bests = self.get_athlete_results_and_bests(athlete['id'], athlete['name'])

                if results:
                    print(f"found {len(results)} recent result(s)")

                    for result in results:
                        event = result['event']
                        current_time = result['time']

                        # Calculate improvements
                        if event in bests:
                            pr_best = bests[event].get('pr')
                            sr_best = bests[event].get('sr')

                            # For PRs, calculate improvement vs old PR
                            if result['record_type'] == 'PR' and pr_best:
                                # If this IS the PR, we need the previous PR
                                # Since this result set the new PR, the old PR was slightly slower
                                # We estimate old PR as current time + small delta
                                # Or we could look at other results... for now, use stored PR
                                result['pr_improvement'] = self.calculate_improvement(current_time, pr_best)
                                result['previous_pr'] = pr_best

                            # For SRs, calculate improvement vs old SR
                            if result['record_type'] == 'SR' and sr_best:
                                result['sr_improvement'] = self.calculate_improvement(current_time, sr_best)
                                result['previous_sr'] = sr_best

                            # For non-PR/SR, calculate distance from current SR
                            if not result['record_type'] and sr_best:
                                current_seconds = self.time_to_seconds(current_time)
                                sr_seconds = bests[event].get('sr_seconds', float('inf'))
                                if sr_seconds != float('inf'):
                                    # How close (as %) to SR? Lower is closer
                                    result['sr_distance'] = (current_seconds - sr_seconds) / sr_seconds * 100
                                    result['current_sr'] = sr_best

                        all_results.append(result)
                else:
                    print("no recent results")

            if not all_results:
                print("\nNo results found in the specified time period.")
                return None

            # Step 3: Sort results
            print(f"\nFound {len(all_results)} total results. Sorting...")

            # Separate into PRs, SRs, and others
            prs = [r for r in all_results if r.get('record_type') == 'PR']
            srs = [r for r in all_results if r.get('record_type') == 'SR']
            others = [r for r in all_results if not r.get('record_type')]

            # Sort PRs by improvement (highest improvement first)
            prs.sort(key=lambda x: x.get('pr_improvement', 0), reverse=True)

            # Sort SRs by improvement (highest improvement first)
            srs.sort(key=lambda x: x.get('sr_improvement', 0), reverse=True)

            # Sort others by distance to SR (closest first)
            others.sort(key=lambda x: x.get('sr_distance', float('inf')))

            # Combine in order: PRs, SRs, Others
            sorted_results = prs + srs + others

            # Step 4: Create spreadsheet
            return self.save_to_spreadsheet(sorted_results)

        except Exception as e:
            print(f"Error during scraping: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            self.close_browser()

    def save_to_spreadsheet(self, results, filename=None):
        """Save results to an Excel spreadsheet."""
        if not results:
            print("No results to save.")
            return None

        # Prepare data for DataFrame
        data = []
        for r in results:
            row = {
                'Name': r['athlete_name'],
                'Type': r.get('record_type', '-'),
                'Event': r['event'],
                'Time/Mark': r['time'],
                'Place': r['place'],
                'Date': r['date_str'],
                'Meet': r['meet_name'],
            }

            # Add context columns based on record type
            if r.get('record_type') == 'PR':
                row['Previous Best'] = r.get('previous_pr', 'N/A')
                row['Improvement %'] = f"{r.get('pr_improvement', 0):.2f}%"
            elif r.get('record_type') == 'SR':
                row['Previous Best'] = r.get('previous_sr', 'N/A')
                row['Improvement %'] = f"{r.get('sr_improvement', 0):.2f}%"
            else:
                row['Previous Best'] = r.get('current_sr', 'N/A')
                if r.get('sr_distance') is not None:
                    row['Improvement %'] = f"+{r.get('sr_distance', 0):.2f}% from SR"
                else:
                    row['Improvement %'] = 'N/A'

            data.append(row)

        df = pd.DataFrame(data)

        columns = ['Name', 'Type', 'Event', 'Time/Mark', 'Place', 'Date', 'Meet', 'Previous Best', 'Improvement %']
        df = df[columns]

        if filename is None:
            sport_name = self.sport_config['name'].replace(' ', '_').replace('&', 'and')
            today = datetime.now().strftime('%Y%m%d')
            filename = f"results_{sport_name}_{self.year}_{today}.xlsx"

        filepath = f"/Users/dylangehl/uisResults/{filename}"
        df.to_excel(filepath, index=False, sheet_name='Results')

        print(f"\nResults saved to: {filepath}")
        print(f"  PRs: {len([r for r in results if r.get('record_type') == 'PR'])}")
        print(f"  SRs: {len([r for r in results if r.get('record_type') == 'SR'])}")
        print(f"  Others: {len([r for r in results if not r.get('record_type')])}")

        return filepath


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='UIS Athletics Results Tracker')
    parser.add_argument('--days', type=int, default=5,
                        help='Number of days back to check (default: 5)')
    parser.add_argument('--visible', action='store_true',
                        help='Run browser in visible mode')
    args = parser.parse_args()

    print("=" * 70)
    print("UIS Athletics Results Tracker")
    print(f"Checking all sports for results in the last {args.days} days")
    print("=" * 70)
    print()

    all_results = []

    # Determine which year/season to check for each sport based on current date
    now = datetime.now()

    # XC season: Aug-Nov
    # Indoor: Dec-Mar (spans two calendar years)
    # Outdoor: Mar-Jun

    sports_to_check = []

    # Figure out relevant seasons based on current month
    if now.month in [8, 9, 10, 11]:  # Aug-Nov: XC season
        sports_to_check.append(('xc', now.year))
    if now.month in [12, 1, 2, 3]:  # Dec-Mar: Indoor season
        # Indoor season year is the year it ends (e.g., Dec 2025 = 2026 indoor)
        indoor_year = now.year if now.month <= 3 else now.year + 1
        sports_to_check.append(('indoor', indoor_year))
    if now.month in [3, 4, 5, 6]:  # Mar-Jun: Outdoor season
        sports_to_check.append(('outdoor', now.year))

    # Also check adjacent seasons in case of overlap or recent meets
    # Always check all three to be safe
    current_year = now.year
    all_sport_years = [
        ('xc', current_year),
        ('indoor', current_year),
        ('outdoor', current_year),
    ]

    # Use a set to avoid duplicates
    sports_to_check = list(set(sports_to_check + all_sport_years))

    for sport, year in sports_to_check:
        sport_name = {
            'xc': 'Cross Country',
            'indoor': 'Indoor Track & Field',
            'outdoor': 'Outdoor Track & Field'
        }[sport]

        print(f"\n{'='*50}")
        print(f"Checking {sport_name} {year}...")
        print('='*50)

        try:
            scraper = AthleticNetScraper(
                headless=not args.visible,
                year=year,
                sport=sport,
                days_back=args.days
            )
            scraper.start_browser()

            # Get roster
            roster = scraper.get_roster()

            if not roster:
                print(f"No roster found for {sport_name} {year}")
                scraper.close_browser()
                continue

            print(f"Checking {len(roster)} athletes...")

            for i, athlete in enumerate(roster):
                print(f"  [{i+1}/{len(roster)}] {athlete['name']}...", end=' ', flush=True)

                results, bests = scraper.get_athlete_results_and_bests(athlete['id'], athlete['name'])

                if results:
                    print(f"found {len(results)} recent result(s)")

                    for result in results:
                        event = result['event']
                        current_time = result['time']
                        result['sport'] = sport_name

                        # Calculate improvements
                        if event in bests:
                            pr_best = bests[event].get('pr')
                            sr_best = bests[event].get('sr')

                            if result['record_type'] == 'PR' and pr_best:
                                result['pr_improvement'] = scraper.calculate_improvement(current_time, pr_best)
                                result['previous_pr'] = pr_best

                            if result['record_type'] == 'SR' and sr_best:
                                result['sr_improvement'] = scraper.calculate_improvement(current_time, sr_best)
                                result['previous_sr'] = sr_best

                            if not result['record_type'] and sr_best:
                                current_seconds = scraper.time_to_seconds(current_time)
                                sr_seconds = bests[event].get('sr_seconds', float('inf'))
                                if sr_seconds != float('inf'):
                                    result['sr_distance'] = (current_seconds - sr_seconds) / sr_seconds * 100
                                    result['current_sr'] = sr_best

                        all_results.append(result)
                else:
                    print("no recent results")

            scraper.close_browser()

        except Exception as e:
            print(f"Error checking {sport_name}: {e}")
            continue

    if not all_results:
        print("\n" + "=" * 70)
        print("No results found in the specified time period.")
        print("=" * 70)
        return

    # Sort results: PRs by improvement, SRs by improvement, others by closeness to SR
    print(f"\nFound {len(all_results)} total results. Sorting...")

    prs = [r for r in all_results if r.get('record_type') == 'PR']
    srs = [r for r in all_results if r.get('record_type') == 'SR']
    others = [r for r in all_results if not r.get('record_type')]

    prs.sort(key=lambda x: x.get('pr_improvement', 0), reverse=True)
    srs.sort(key=lambda x: x.get('sr_improvement', 0), reverse=True)
    others.sort(key=lambda x: x.get('sr_distance', float('inf')))

    sorted_results = prs + srs + others

    # Save to spreadsheet
    data = []
    for r in sorted_results:
        row = {
            'Name': r['athlete_name'],
            'Type': r.get('record_type', '-'),
            'Sport': r.get('sport', ''),
            'Event': r['event'],
            'Time/Mark': r['time'],
            'Place': r['place'],
            'Date': r['date_str'],
            'Meet': r['meet_name'],
        }

        if r.get('record_type') == 'PR':
            row['Previous Best'] = r.get('previous_pr', 'N/A')
            row['Improvement %'] = f"{r.get('pr_improvement', 0):.2f}%"
        elif r.get('record_type') == 'SR':
            row['Previous Best'] = r.get('previous_sr', 'N/A')
            row['Improvement %'] = f"{r.get('sr_improvement', 0):.2f}%"
        else:
            row['Previous Best'] = r.get('current_sr', 'N/A')
            if r.get('sr_distance') is not None:
                row['Improvement %'] = f"+{r.get('sr_distance', 0):.2f}% from SR"
            else:
                row['Improvement %'] = 'N/A'

        data.append(row)

    df = pd.DataFrame(data)
    columns = ['Name', 'Type', 'Sport', 'Event', 'Time/Mark', 'Place', 'Date', 'Meet', 'Previous Best', 'Improvement %']
    df = df[columns]

    today = datetime.now().strftime('%Y%m%d')
    filename = f"results_{today}.xlsx"
    filepath = f"/Users/dylangehl/uisResults/{filename}"
    df.to_excel(filepath, index=False, sheet_name='Results')

    print(f"\nResults saved to: {filepath}")
    print(f"  PRs: {len(prs)}")
    print(f"  SRs: {len(srs)}")
    print(f"  Others: {len(others)}")

    print("\n" + "=" * 70)
    print("SUCCESS! Check the spreadsheet for results.")
    print("=" * 70)


if __name__ == "__main__":
    main()
