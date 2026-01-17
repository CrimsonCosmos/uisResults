#!/usr/bin/env python3
"""
UIS Athletics Results Scraper
Scrapes athletic.net for Illinois-Springfield athletes' recent results.
Checks each athlete's profile for events in the last 5 days and identifies PRs, SRs.

This scraper uses a hybrid approach:
1. First tries fast API calls (10-50x faster)
2. Falls back to Selenium scraping if API fails
"""

import time
import re
import json
import requests
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd


class AthleticNetAPI:
    """
    Fast API client for athletic.net.
    Captures tokens from browser session and makes direct API calls.
    """

    API_BASE = "https://www.athletic.net/api/v1"

    def __init__(self):
        self.session = requests.Session()
        self.tokens = {}
        self.cookies_set = False

    def init_from_browser(self, driver):
        """
        Capture API tokens from browser's network requests.
        Call this after the page has fully loaded.
        """
        # Transfer cookies
        for cookie in driver.get_cookies():
            self.session.cookies.set(cookie['name'], cookie['value'])
        self.cookies_set = True

        # Capture tokens from network logs
        try:
            logs = driver.get_log('performance')
            for log in logs:
                try:
                    message = json.loads(log['message'])['message']
                    if message['method'] == 'Network.requestWillBeSent':
                        headers = message['params']['request'].get('headers', {})
                        if headers.get('anettokens'):
                            self.tokens['anettokens'] = headers['anettokens']
                        if headers.get('anet-site-roles-token'):
                            self.tokens['anet-site-roles-token'] = headers['anet-site-roles-token']
                        if headers.get('anet-appinfo'):
                            self.tokens['anet-appinfo'] = headers['anet-appinfo']
                        if self.tokens.get('anettokens') and self.tokens.get('anet-site-roles-token'):
                            break
                except:
                    pass
        except Exception as e:
            print(f"  Warning: Could not capture API tokens: {e}")

        return bool(self.tokens.get('anettokens'))

    def _make_request(self, endpoint, params=None, referer=None):
        """Make API request with captured tokens."""
        if not self.cookies_set:
            return None

        url = f"{self.API_BASE}/{endpoint}"
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'anet-appinfo': self.tokens.get('anet-appinfo', 'web:web:0:360'),
        }

        if self.tokens.get('anettokens'):
            headers['anettokens'] = self.tokens['anettokens']
        if self.tokens.get('anet-site-roles-token'):
            headers['anet-site-roles-token'] = self.tokens['anet-site-roles-token']
        if referer:
            headers['Referer'] = referer

        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            pass
        return None

    def get_roster(self, season_id, referer=None):
        """Get team roster via API."""
        data = self._make_request(
            "TeamHome/GetAthletes",
            params={'seasonId': season_id},
            referer=referer
        )
        if data and isinstance(data, list):
            return [{'id': str(a['ID']), 'name': a['Name'], 'gender': a.get('Gender', '')} for a in data]
        return None

    def get_athlete_bio(self, athlete_id, sport='xc', referer=None):
        """Get athlete bio data including all results."""
        sport_code = 'xc' if sport == 'xc' else 'tf'
        data = self._make_request(
            "AthleteBio/GetAthleteBioData",
            params={'athleteId': athlete_id, 'sport': sport_code, 'level': 0},
            referer=referer
        )
        return data

    def parse_athlete_results(self, bio_data, athlete_id, athlete_name, cutoff_date, year):
        """
        Parse API athlete bio response into results and bests format.
        Returns (results, bests) tuple matching the Selenium scraper format.
        """
        results = []
        bests = {}

        if not bio_data:
            return results, bests

        # Parse results from bio data
        # API returns results with PersonalBest and SeasonBest flags
        results_data = bio_data.get('results', [])

        for result in results_data:
            try:
                # Parse date
                date_str = result.get('MeetDate', '')
                if date_str:
                    # API returns dates in format "2025-01-15T00:00:00"
                    result_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                else:
                    continue

                # Skip if before cutoff
                if result_date < cutoff_date:
                    continue

                event = result.get('Event', '')
                time_str = result.get('Result', '')
                place = result.get('Place', 0)
                meet_name = result.get('MeetName', '')

                # Determine record type
                record_type = None
                if result.get('PersonalBest'):
                    record_type = 'PR'
                elif result.get('SeasonBest'):
                    record_type = 'SR'

                results.append({
                    'athlete_name': athlete_name,
                    'athlete_id': athlete_id,
                    'event': event,
                    'place': place,
                    'time': time_str,
                    'record_type': record_type,
                    'date': result_date,
                    'date_str': result_date.strftime("%b %d, %Y"),
                    'meet_name': meet_name
                })

            except Exception:
                continue

        # Parse bests from bio data
        # Look for season bests and personal bests per event
        season_bests = bio_data.get('seasonBests', [])
        personal_bests = bio_data.get('personalBests', [])

        # Build bests dictionary from personal bests
        for pb in personal_bests:
            event = pb.get('Event', '')
            time_str = pb.get('Result', '')
            if event and time_str:
                time_secs = self._time_to_seconds(time_str)
                bests[event] = {
                    'pr': time_str,
                    'pr_seconds': time_secs
                }

        # Add season bests
        for sb in season_bests:
            event = sb.get('Event', '')
            time_str = sb.get('Result', '')
            sb_year = sb.get('Year', 0)

            if event and time_str and sb_year == year:
                time_secs = self._time_to_seconds(time_str)
                if event not in bests:
                    bests[event] = {}
                bests[event]['sr'] = time_str
                bests[event]['sr_seconds'] = time_secs

        return results, bests

    def _time_to_seconds(self, time_str):
        """Convert time string to seconds."""
        if not time_str:
            return float('inf')

        time_str = re.sub(r'[PRSRprsr\s\*]+', '', time_str).strip()

        try:
            if ':' in time_str:
                parts = time_str.split(':')
                if len(parts) == 2:
                    return int(parts[0]) * 60 + float(parts[1])
                elif len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                return float(time_str)
        except (ValueError, IndexError):
            return float('inf')

    # ===== NEW MEET-BASED APPROACH (MUCH FASTER) =====

    @staticmethod
    def get_season_id(sport, year):
        """
        Convert sport + year to athletic.net's seasonId format.

        athletic.net uses different seasonId formats:
        - Cross Country: year (e.g., 2025)
        - Indoor Track: year + 10000 (e.g., 12026 for 2026)
        - Outdoor Track: year (e.g., 2025)
        """
        if sport == 'indoor':
            return year + 10000
        else:
            return year

    def get_team_calendar(self, season_id, referer=None):
        """
        Get team calendar with all meets for a season.
        Returns list of meets with IDs, dates, and hasResults flags.
        """
        data = self._make_request(
            "TeamHomeCal/GetCalendar",
            params={'seasonId': season_id},
            referer=referer
        )
        if data and isinstance(data, list):
            return data
        return None

    def get_meet_data(self, meet_id, sport='xc', referer=None):
        """
        Get meet data including division IDs.
        Returns dict with xcDivisions or tfDivisions containing division info.
        """
        sport_code = 'xc' if sport == 'xc' else 'tf'
        data = self._make_request(
            "Meet/GetMeetData",
            params={'meetId': meet_id, 'sport': sport_code},
            referer=referer
        )
        return data

    def get_meet_results(self, div_id, meet_id, referer=None):
        """
        Get all results for a meet division via POST to GetResultsData3.
        This is the FAST way to get results - one call per division instead of per athlete!

        Returns dict with 'resultsXC' or 'resultsTF' list containing all athlete results
        with isPr and isSr flags.
        """
        if not self.cookies_set:
            return None

        url = f"{self.API_BASE}/Meet/GetResultsData3"
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Content-Type': 'application/json',
            'anet-appinfo': self.tokens.get('anet-appinfo', 'web:web:0:360'),
        }

        if self.tokens.get('anettokens'):
            headers['anettokens'] = self.tokens['anettokens']
        if self.tokens.get('anet-site-roles-token'):
            headers['anet-site-roles-token'] = self.tokens['anet-site-roles-token']
        if referer:
            headers['Referer'] = referer

        try:
            resp = self.session.post(url, json={'divId': div_id}, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            pass
        return None

    def get_team_results_from_meets(self, team_id, season_id, sport, cutoff_date, driver, referer=None):
        """
        FAST approach: Get all team results by checking recent meets.

        Instead of checking each athlete individually, we:
        1. Get the team calendar (list of meets)
        2. Filter to meets within date range that have results
        3. For each meet, load the page to get meet-specific tokens
        4. Get division results and filter to our team

        This is 10-50x faster than checking individual athletes!

        Args:
            driver: Selenium WebDriver instance (needed to load meet pages for tokens)

        Returns list of results in the standard format.
        """
        results = []

        # Get team calendar
        calendar = self.get_team_calendar(season_id, referer=referer)
        if not calendar:
            return None

        # Filter to meets with results within date range
        recent_meets = []
        for meet in calendar:
            if not meet.get('MeetHasResults'):
                continue

            # Parse meet date
            meet_date_str = meet.get('StartDate', '')
            if not meet_date_str:
                continue

            try:
                meet_date = datetime.strptime(meet_date_str[:10], "%Y-%m-%d")
                if meet_date >= cutoff_date:
                    recent_meets.append({
                        'id': meet['MeetID'],
                        'name': meet['Name'],
                        'date': meet_date,
                        'date_str': meet_date.strftime("%b %d, %Y")
                    })
            except:
                continue

        if not recent_meets:
            return results  # Empty list, no recent meets

        print(f"  Found {len(recent_meets)} recent meet(s) with results")

        # For each recent meet, get results
        for meet in recent_meets:
            meet_id = meet['id']
            meet_name = meet['name']
            meet_date = meet['date']
            meet_date_str = meet['date_str']

            print(f"    Checking {meet_name}...", end=' ', flush=True)

            # Build meet URL based on sport
            if sport == 'xc':
                meet_url = f"https://www.athletic.net/CrossCountry/meet/{meet_id}/results"
            else:
                meet_url = f"https://www.athletic.net/TrackAndField/meet/{meet_id}/results"

            # IMPORTANT: Load the meet page to get meet-specific tokens
            # The anettokens JWT contains the meetId and is required for GetResultsData3
            try:
                driver.get(meet_url)
                time.sleep(2)  # Wait for page and API calls

                # Capture fresh tokens for this meet
                logs = driver.get_log('performance')
                for log in logs:
                    try:
                        message = json.loads(log['message'])['message']
                        if message['method'] == 'Network.requestWillBeSent':
                            headers = message['params']['request'].get('headers', {})
                            if headers.get('anettokens'):
                                self.tokens['anettokens'] = headers['anettokens']
                            if headers.get('anet-site-roles-token'):
                                self.tokens['anet-site-roles-token'] = headers['anet-site-roles-token']
                    except:
                        pass
            except Exception as e:
                print(f"page load failed: {e}")
                continue

            # Get meet data to find divisions
            meet_data = self.get_meet_data(meet_id, sport=sport, referer=meet_url)
            if not meet_data:
                print("no data")
                continue

            # Get divisions based on sport
            divisions = meet_data.get('xcDivisions', []) if sport == 'xc' else meet_data.get('tfDivisions', [])

            if not divisions:
                print("no divisions")
                continue

            meet_results_count = 0

            # Get results for each division
            for div in divisions:
                div_id = div.get('IDMeetDiv')
                div_name = div.get('DivName', 'Unknown')
                event_name = div_name  # For XC, division name is the event

                if not div_id:
                    continue

                # Get results for this division
                div_results = self.get_meet_results(div_id, meet_id, referer=meet_url)
                if not div_results:
                    continue

                # Extract results (XC uses resultsXC, TF would use resultsTF)
                results_list = div_results.get('resultsXC', []) or div_results.get('resultsTF', [])

                # Filter to our team
                for r in results_list:
                    if r.get('IDSchool') != team_id:
                        continue

                    # Build result object
                    first_name = r.get('FirstName', '')
                    last_name = r.get('LastName', '')
                    athlete_name = f"{first_name} {last_name}".strip()

                    record_type = None
                    if r.get('isPr'):
                        record_type = 'PR'
                    elif r.get('isSr'):
                        record_type = 'SR'

                    result_obj = {
                        'athlete_name': athlete_name,
                        'athlete_id': str(r.get('AthleteID', '')),
                        'event': event_name,
                        'place': r.get('Place', 0),
                        'time': r.get('Result', ''),
                        'record_type': record_type,
                        'date': meet_date,
                        'date_str': meet_date_str,
                        'meet_name': meet_name
                    }

                    results.append(result_obj)
                    meet_results_count += 1

            if meet_results_count > 0:
                print(f"found {meet_results_count} UIS result(s)")
            else:
                print("no UIS results")

        # Now fetch previous bests for athletes with PRs/SRs
        # This adds a few extra API calls but gives us the improvement data
        pr_sr_results = [r for r in results if r.get('record_type')]
        if pr_sr_results:
            print(f"  Fetching previous bests for {len(set(r['athlete_id'] for r in pr_sr_results))} athletes with PRs/SRs...")
            self._fetch_previous_bests(pr_sr_results, sport, driver)

        return results

    def _fetch_previous_bests(self, results, sport, driver):
        """
        Fetch previous best times for athletes with PRs/SRs.
        Updates the results in place with previous_pr, previous_sr, and improvement data.
        """
        # Group results by athlete to minimize API calls
        athletes_to_fetch = {}
        for r in results:
            if r.get('record_type'):
                athlete_id = r['athlete_id']
                if athlete_id not in athletes_to_fetch:
                    athletes_to_fetch[athlete_id] = {
                        'name': r['athlete_name'],
                        'results': []
                    }
                athletes_to_fetch[athlete_id]['results'].append(r)

        # Fetch bio data for each athlete
        for athlete_id, data in athletes_to_fetch.items():
            try:
                # Load athlete page to get proper tokens
                if sport == 'xc':
                    athlete_url = f"https://www.athletic.net/athlete/{athlete_id}/cross-country"
                else:
                    athlete_url = f"https://www.athletic.net/athlete/{athlete_id}/track-and-field"

                driver.get(athlete_url)
                time.sleep(1.5)

                # Capture fresh tokens
                logs = driver.get_log('performance')
                for log in logs:
                    try:
                        message = json.loads(log['message'])['message']
                        if message['method'] == 'Network.requestWillBeSent':
                            headers = message['params']['request'].get('headers', {})
                            if headers.get('anettokens'):
                                self.tokens['anettokens'] = headers['anettokens']
                    except:
                        pass

                # Get athlete bio
                bio_data = self.get_athlete_bio(athlete_id, sport=sport, referer=athlete_url)
                if not bio_data:
                    continue

                # Extract results from bio - XC uses resultsXC, TF uses resultsTF
                all_results = bio_data.get('resultsXC', []) if sport == 'xc' else bio_data.get('resultsTF', [])
                if not all_results:
                    continue

                # Group by distance (for XC) or event
                distance_results = {}
                for br in all_results:
                    # Use Distance for XC grouping
                    distance = br.get('Distance', 0)
                    if distance not in distance_results:
                        distance_results[distance] = []
                    distance_results[distance].append({
                        'time': br.get('Result', ''),
                        'seconds': br.get('SortValue', float('inf')),  # Use SortValue directly
                        'is_pr': br.get('PersonalBest', False),
                        'is_sr': br.get('SeasonBest', False),
                        'season': br.get('SeasonID', 0)
                    })

                # For each result, find the previous best
                for r in data['results']:
                    event = r['event']
                    current_time = r['time']
                    current_seconds = self._time_to_seconds(current_time)

                    # Extract distance from event name (e.g., "8,000 Meters" -> 8000)
                    distance_match = re.search(r'(\d+,?\d*)\s*(?:meters?|m)', event.lower())
                    if distance_match:
                        target_distance = int(distance_match.group(1).replace(',', ''))
                    else:
                        continue

                    # Find matching distance results
                    if target_distance not in distance_results:
                        # Try close matches (within 100m)
                        for d in distance_results.keys():
                            if abs(d - target_distance) < 100:
                                target_distance = d
                                break

                    if target_distance not in distance_results:
                        continue

                    # Sort all times for this distance (best first)
                    times = sorted(distance_results[target_distance], key=lambda x: x['seconds'])

                    if r['record_type'] == 'PR':
                        # Find times better than or equal to current (within 1 second tolerance)
                        # The current PR should be the best, previous PR is second best
                        if len(times) >= 2:
                            # First is current PR, second is previous best
                            prev_pr = times[1]
                            r['previous_pr'] = prev_pr['time']
                            if prev_pr['seconds'] > 0 and prev_pr['seconds'] != float('inf'):
                                improvement = (prev_pr['seconds'] - current_seconds) / prev_pr['seconds'] * 100
                                r['pr_improvement'] = improvement

                    elif r['record_type'] == 'SR':
                        # For SR, find second best time overall (simplification)
                        if len(times) >= 2:
                            prev_sr = times[1]
                            r['previous_sr'] = prev_sr['time']
                            if prev_sr['seconds'] > 0 and prev_sr['seconds'] != float('inf'):
                                improvement = (prev_sr['seconds'] - current_seconds) / prev_sr['seconds'] * 100
                                r['sr_improvement'] = improvement

            except Exception as e:
                # Skip this athlete on error
                continue


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

        # Wait for athlete links to appear (max 5 seconds)
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/athlete/']"))
            )
        except:
            pass  # Continue anyway - page might have no athletes

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

        # Wait for tables to load (max 3 seconds)
        try:
            WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.TAG_NAME, "table"))
            )
        except:
            pass  # Continue anyway - might have no results

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')

        return self._parse_athlete_page(soup, athlete_id, athlete_name)

    def get_athletes_parallel(self, athletes, num_tabs=3):
        """
        Check multiple athletes in parallel using browser tabs.
        Returns list of (athlete, results, bests) tuples.
        """
        if not athletes:
            return []

        all_data = []
        original_handle = self.driver.current_window_handle

        # Process in batches
        for batch_start in range(0, len(athletes), num_tabs):
            batch = athletes[batch_start:batch_start + num_tabs]
            handles = []
            athlete_urls = []

            # Open tabs and start loading pages
            for i, athlete in enumerate(batch):
                url = f"{self.BASE_URL}/athlete/{athlete['id']}/{self.sport_config['athlete_path']}"
                athlete_urls.append(url)

                if i == 0:
                    # Use existing tab for first athlete
                    self.driver.get(url)
                    handles.append(self.driver.current_window_handle)
                else:
                    # Open new tab for subsequent athletes
                    self.driver.execute_script("window.open('');")
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                    self.driver.get(url)
                    handles.append(self.driver.current_window_handle)

            # Wait a moment for pages to load
            time.sleep(1.5)

            # Collect results from each tab
            for i, (athlete, handle) in enumerate(zip(batch, handles)):
                self.driver.switch_to.window(handle)

                # Quick wait for content
                try:
                    WebDriverWait(self.driver, 2).until(
                        EC.presence_of_element_located((By.TAG_NAME, "table"))
                    )
                except:
                    pass

                # Check for rate limiting (page shows error or unusual content)
                page_source = self.driver.page_source
                if "rate limit" in page_source.lower() or "too many requests" in page_source.lower():
                    print("\n  [!] Rate limited - waiting 30 seconds...")
                    time.sleep(30)
                    self.driver.get(athlete_urls[i])
                    time.sleep(2)
                    page_source = self.driver.page_source

                soup = BeautifulSoup(page_source, 'html.parser')
                results, bests = self._parse_athlete_page(soup, athlete['id'], athlete['name'])
                all_data.append((athlete, results, bests))

            # Close extra tabs (keep only the first one)
            for handle in handles[1:]:
                self.driver.switch_to.window(handle)
                self.driver.close()

            # Switch back to original tab
            self.driver.switch_to.window(original_handle)

        return all_data

    def _parse_athlete_page(self, soup, athlete_id, athlete_name):
        """Parse an athlete's page HTML and extract results and bests."""

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

            # Find all times with context (year, sport type)
            # Pattern: "2025 Indoor Jr 16:37.46" or "2024 Outdoor So 17:13.53PR"
            time_entries = re.findall(
                r'(\d{4})\s+(Indoor|Outdoor)\s+\w{2}\s+(\d{1,2}:\d{2}\.\d+|\d+\.\d+)\s*(PR)?',
                table_text
            )

            if time_entries:
                # Collect all times with metadata
                all_times = []
                current_season_times = []
                sport_label = 'Indoor' if self.sport == 'indoor' else 'Outdoor'

                for year_str, sport_type, time_str, is_pr in time_entries:
                    secs = self.time_to_seconds(time_str)
                    if secs != float('inf'):
                        entry = {
                            'year': int(year_str),
                            'sport': sport_type,
                            'time': time_str,
                            'seconds': secs,
                            'is_pr': is_pr == 'PR'
                        }
                        all_times.append(entry)

                        # Check if this is current season
                        if int(year_str) == self.year and sport_type == sport_label:
                            current_season_times.append(entry)

                if all_times:
                    # Sort all times to find PR and previous PR
                    all_times.sort(key=lambda x: x['seconds'])
                    best = all_times[0]

                    bests[event] = {
                        'pr': best['time'],
                        'pr_seconds': best['seconds'],
                        'all_times': [t['time'] for t in all_times]  # Keep all times for reference
                    }

                    # Previous PR is the second-best all-time
                    if len(all_times) > 1:
                        bests[event]['previous_pr'] = all_times[1]['time']
                        bests[event]['previous_pr_seconds'] = all_times[1]['seconds']

                    # Current season record (SR)
                    if current_season_times:
                        current_season_times.sort(key=lambda x: x['seconds'])
                        sr = current_season_times[0]
                        bests[event]['sr'] = sr['time']
                        bests[event]['sr_seconds'] = sr['seconds']

                        # Previous SR is second-best this season
                        if len(current_season_times) > 1:
                            bests[event]['previous_sr'] = current_season_times[1]['time']
                            bests[event]['previous_sr_seconds'] = current_season_times[1]['seconds']

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
                            # For PRs: use previous_pr (second-best all-time) since current PR IS the new time
                            previous_pr = bests[event].get('previous_pr')
                            # For SRs: use previous_sr (second-best this season) since current SR IS the new time
                            previous_sr = bests[event].get('previous_sr')
                            # Current SR for non-PR/SR results
                            sr_best = bests[event].get('sr')

                            # For PRs, calculate improvement vs old PR
                            if result['record_type'] == 'PR' and previous_pr:
                                current_seconds = self.time_to_seconds(current_time)
                                prev_pr_seconds = self.time_to_seconds(previous_pr)
                                if prev_pr_seconds != float('inf') and 0.5 < prev_pr_seconds / current_seconds < 2.0:
                                    result['pr_improvement'] = self.calculate_improvement(current_time, previous_pr)
                                    result['previous_pr'] = previous_pr

                            # For SRs, calculate improvement vs old SR
                            if result['record_type'] == 'SR' and previous_sr:
                                current_seconds = self.time_to_seconds(current_time)
                                prev_sr_seconds = self.time_to_seconds(previous_sr)
                                if prev_sr_seconds != float('inf') and 0.5 < prev_sr_seconds / current_seconds < 2.0:
                                    result['sr_improvement'] = self.calculate_improvement(current_time, previous_sr)
                                    result['previous_sr'] = previous_sr

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


def get_relevant_sports(days_back):
    """
    Determine which sport/year combos are relevant based on NCAA D2 schedule.

    NCAA D2 Championship dates (approximate):
    - Cross Country: Early November (season: Aug-Nov)
    - Indoor Track: Early March (season: Dec-Mar)
    - Outdoor Track: Late May (season: Mar-Jun)

    We only check sports where meets could have happened in the last N days.
    """
    now = datetime.now()
    current_year = now.year
    month = now.month
    lookback_start = now - timedelta(days=days_back)

    sports = []

    # Cross Country: Season Aug-Nov
    # Check current year if in season, or last year if lookback reaches into last season
    xc_current_start = datetime(current_year, 8, 1)
    xc_current_end = datetime(current_year, 11, 30)
    xc_last_start = datetime(current_year - 1, 8, 1)
    xc_last_end = datetime(current_year - 1, 11, 30)

    if xc_current_start <= now <= xc_current_end:
        # Currently in XC season
        sports.append(('xc', current_year))
    elif lookback_start <= xc_last_end and now >= xc_last_start:
        # Lookback window overlaps with last year's XC season
        sports.append(('xc', current_year - 1))

    # Indoor Track: Season Dec-Mar (spans calendar years)
    # "Indoor 2026" = Dec 2025 through Mar 2026
    if month <= 6:
        # First half of year: current indoor season is this year
        indoor_year = current_year
        indoor_start = datetime(current_year - 1, 12, 1)
        indoor_end = datetime(current_year, 3, 15)
    else:
        # Second half of year: next indoor season starts in Dec
        indoor_year = current_year + 1
        indoor_start = datetime(current_year, 12, 1)
        indoor_end = datetime(current_year + 1, 3, 15)

    if indoor_start <= now <= indoor_end:
        # Currently in indoor season
        sports.append(('indoor', indoor_year))
    elif month <= 6 and lookback_start <= indoor_end:
        # Lookback might catch end of indoor season
        sports.append(('indoor', indoor_year))

    # Outdoor Track: Season Mar-Jun
    outdoor_start = datetime(current_year, 3, 1)
    outdoor_end = datetime(current_year, 6, 15)
    outdoor_last_end = datetime(current_year - 1, 6, 15)

    if outdoor_start <= now <= outdoor_end:
        # Currently in outdoor season
        sports.append(('outdoor', current_year))
    elif lookback_start <= outdoor_last_end and month <= 2:
        # Very long lookback into last year's outdoor (unlikely but possible)
        sports.append(('outdoor', current_year - 1))

    # Remove duplicates while preserving order
    seen = set()
    unique = []
    for s in sports:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return unique


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='UIS Athletics Results Tracker')
    parser.add_argument('--days', type=int, default=5,
                        help='Number of days back to check (default: 5)')
    parser.add_argument('--visible', action='store_true',
                        help='Run browser in visible mode')

    # Sport filter options (mutually exclusive)
    sport_group = parser.add_mutually_exclusive_group()
    sport_group.add_argument('--xc', action='store_true',
                             help='Only check Cross Country')
    sport_group.add_argument('--indoor', action='store_true',
                             help='Only check Indoor Track & Field')
    sport_group.add_argument('--outdoor', action='store_true',
                             help='Only check Outdoor Track & Field')
    sport_group.add_argument('--track', action='store_true',
                             help='Only check Track & Field (both indoor and outdoor)')

    args = parser.parse_args()

    print("=" * 70)
    print("UIS Athletics Results Tracker")
    print(f"Checking for results in the last {args.days} days")
    print("=" * 70)

    all_results = []
    checked_athletes = set()  # Track athlete IDs we've already checked

    # Get relevant sports based on current date
    sports_to_check = get_relevant_sports(args.days)

    sport_names = {
        'xc': 'Cross Country',
        'indoor': 'Indoor Track & Field',
        'outdoor': 'Outdoor Track & Field'
    }

    # Apply sport filter if specified
    now = datetime.now()
    if args.xc:
        sports_to_check = [(s, y) for s, y in sports_to_check if s == 'xc']
        if not sports_to_check:
            # Force XC even if not in season
            sports_to_check = [('xc', now.year)]
    elif args.indoor:
        sports_to_check = [(s, y) for s, y in sports_to_check if s == 'indoor']
        if not sports_to_check:
            # Force indoor even if not in season
            year = now.year if now.month <= 6 else now.year + 1
            sports_to_check = [('indoor', year)]
    elif args.outdoor:
        sports_to_check = [(s, y) for s, y in sports_to_check if s == 'outdoor']
        if not sports_to_check:
            # Force outdoor even if not in season
            sports_to_check = [('outdoor', now.year)]
    elif args.track:
        sports_to_check = [(s, y) for s, y in sports_to_check if s in ('indoor', 'outdoor')]
        if not sports_to_check:
            # Force both track seasons
            indoor_year = now.year if now.month <= 6 else now.year + 1
            sports_to_check = [('indoor', indoor_year), ('outdoor', now.year)]

    if not sports_to_check:
        print("No sports to check for the specified criteria.")
        return

    print(f"Checking: {', '.join(f'{sport_names[s]} {y}' for s, y in sports_to_check)}")
    print()

    # Start browser ONCE and reuse it
    options = Options()
    if not args.visible:
        options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

    # Enable performance logging to capture API tokens
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    print("Starting browser...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # Initialize API client
    api = AthleticNetAPI()
    api_initialized = False
    use_api = True  # Will be set to False if API fails

    try:
        for sport, year in sports_to_check:
            sport_name = sport_names[sport]

            print(f"\n{'='*50}")
            print(f"Checking {sport_name} {year}...")
            print('='*50)

            scraper = AthleticNetScraper(
                headless=not args.visible,
                year=year,
                sport=sport,
                days_back=args.days
            )
            # Share the browser instead of starting a new one
            scraper.driver = driver

            # Build team URL for API referer
            team_url = f"https://www.athletic.net/team/65580/{scraper.sport_config['url_path']}/{year}"

            # First, load the team page (needed for both API token capture and Selenium fallback)
            print(f"Loading team page...")
            driver.get(team_url)

            # Wait for page to fully load (important for token capture)
            time.sleep(4)

            # Initialize API on first sport (capture tokens from network logs)
            if not api_initialized and use_api:
                print("Capturing API tokens from browser...")
                if api.init_from_browser(driver):
                    print("  API tokens captured successfully!")
                    api_initialized = True
                else:
                    print("  Could not capture API tokens - will use Selenium scraping")
                    use_api = False

            # ===== NEW: MEET-BASED APPROACH (FASTEST) =====
            # Instead of checking each athlete, we check recent meets directly
            # This is 10-50x faster!

            cutoff_date = datetime.now() - timedelta(days=args.days)
            sport_results = None

            if use_api and api_initialized:
                print("Using FAST meet-based API approach...")
                # Convert sport + year to proper seasonId (indoor uses year + 10000)
                season_id = api.get_season_id(sport, year)
                sport_results = api.get_team_results_from_meets(
                    team_id=65580,  # UIS team ID
                    season_id=season_id,
                    sport=sport,
                    cutoff_date=cutoff_date,
                    driver=driver,  # Pass driver for meet page loading
                    referer=team_url
                )

                if sport_results is not None:
                    # Success! Add results with sport name
                    for r in sport_results:
                        r['sport'] = sport_name
                    all_results.extend(sport_results)
                    print(f"  Found {len(sport_results)} total results via meet-based approach")
                    continue  # Skip to next sport - we're done!
                else:
                    print("  Meet-based approach failed, falling back to athlete-by-athlete...")

            # ===== FALLBACK: ATHLETE-BY-ATHLETE APPROACH =====
            # Only used if meet-based approach fails

            # Get roster
            roster = None
            if use_api and api_initialized:
                print("Trying API for roster...")
                roster = api.get_roster(year, referer=team_url)
                if roster:
                    print(f"  API: Found {len(roster)} athletes")
                else:
                    print("  API roster failed - falling back to Selenium")

            # Fallback to Selenium scraping for roster
            if not roster:
                roster = scraper.get_roster()

            if not roster:
                print(f"No roster found for {sport_name} {year}")
                continue

            # Filter out already-checked athletes
            new_athletes = [a for a in roster if a['id'] not in checked_athletes]
            skipped = len(roster) - len(new_athletes)

            if skipped > 0:
                print(f"Checking {len(new_athletes)} athletes ({skipped} already checked)...")
            else:
                print(f"Checking {len(new_athletes)} athletes...")

            # Mark all as checked
            for athlete in new_athletes:
                checked_athletes.add(athlete['id'])

            # Process athletes - old API approach (fallback)
            if use_api and api_initialized:
                print(f"Using athlete-by-athlete API approach...")
                api_failed = False

                for i, athlete in enumerate(new_athletes):
                    print(f"  [{i+1}/{len(new_athletes)}] {athlete['name']}...", end=' ', flush=True)

                    referer = f"https://www.athletic.net/athlete/{athlete['id']}/{scraper.sport_config['athlete_path']}"
                    bio_data = api.get_athlete_bio(athlete['id'], sport=sport, referer=referer)

                    if bio_data is None:
                        # API failed - switch to Selenium for remaining athletes
                        print("API failed!")
                        api_failed = True
                        use_api = False
                        remaining = new_athletes[i:]
                        print(f"\nFalling back to Selenium for {len(remaining)} remaining athletes...")
                        break

                    results, bests = api.parse_athlete_results(
                        bio_data, athlete['id'], athlete['name'], cutoff_date, year
                    )

                    if results:
                        print(f"found {len(results)} recent result(s)")
                        for result in results:
                            result['sport'] = sport_name
                            event = result['event']
                            current_time = result['time']

                            # Calculate improvements
                            if event in bests:
                                sr_best = bests[event].get('sr')

                                if result['record_type'] == 'PR' and bests[event].get('pr'):
                                    result['previous_pr'] = bests[event].get('pr')
                                    # Note: API doesn't give us "previous PR", just current PR

                                if result['record_type'] == 'SR' and sr_best:
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

                if api_failed:
                    # Continue with Selenium for remaining athletes
                    remaining_athletes = new_athletes[new_athletes.index(athlete):]
                else:
                    remaining_athletes = []
            else:
                remaining_athletes = new_athletes

            # Selenium fallback (or primary if API not available)
            if remaining_athletes:
                NUM_PARALLEL_TABS = 3
                for batch_start in range(0, len(remaining_athletes), NUM_PARALLEL_TABS):
                    batch = remaining_athletes[batch_start:batch_start + NUM_PARALLEL_TABS]
                    batch_names = ', '.join(a['name'] for a in batch)
                    print(f"  [{batch_start+1}-{batch_start+len(batch)}/{len(remaining_athletes)}] {batch_names}...", end=' ', flush=True)

                    batch_data = scraper.get_athletes_parallel(batch, num_tabs=NUM_PARALLEL_TABS)

                    found_count = sum(1 for _, results, _ in batch_data if results)
                    if found_count > 0:
                        print(f"found results for {found_count}")
                    else:
                        print("no recent results")

                    for athlete, results, bests in batch_data:
                        if not results:
                            continue

                        for result in results:
                            event = result['event']
                            current_time = result['time']
                            result['sport'] = sport_name

                            # Calculate improvements
                            if event in bests:
                                # For PRs: use previous_pr (second-best all-time) since current PR IS the new time
                                previous_pr = bests[event].get('previous_pr')
                                # For SRs: use previous_sr (second-best this season) since current SR IS the new time
                                previous_sr = bests[event].get('previous_sr')
                                # Current SR for non-PR/SR results
                                sr_best = bests[event].get('sr')

                                if result['record_type'] == 'PR' and previous_pr:
                                    # Validate that previous best is reasonable (similar magnitude to current)
                                    current_secs = scraper.time_to_seconds(current_time)
                                    prev_pr_secs = scraper.time_to_seconds(previous_pr)
                                    # Previous best should be within 50% of current time to be valid
                                    if prev_pr_secs != float('inf') and 0.5 < prev_pr_secs / current_secs < 2.0:
                                        result['pr_improvement'] = scraper.calculate_improvement(current_time, previous_pr)
                                        result['previous_pr'] = previous_pr

                                if result['record_type'] == 'SR' and previous_sr:
                                    # Validate that previous SR is reasonable
                                    current_secs = scraper.time_to_seconds(current_time)
                                    prev_sr_secs = scraper.time_to_seconds(previous_sr)
                                    if prev_sr_secs != float('inf') and 0.5 < prev_sr_secs / current_secs < 2.0:
                                        result['sr_improvement'] = scraper.calculate_improvement(current_time, previous_sr)
                                        result['previous_sr'] = previous_sr

                                if not result['record_type'] and sr_best:
                                    current_seconds = scraper.time_to_seconds(current_time)
                                    sr_seconds = bests[event].get('sr_seconds', float('inf'))
                                    if sr_seconds != float('inf'):
                                        result['sr_distance'] = (current_seconds - sr_seconds) / sr_seconds * 100
                                        result['current_sr'] = sr_best

                            all_results.append(result)

    finally:
        driver.quit()

    if not all_results:
        print("\n" + "=" * 70)
        print("No results found in the specified time period.")
        print("=" * 70)
        return

    # Deduplicate results (same athlete, event, date, time can appear in multiple sports)
    seen = set()
    unique_results = []
    for r in all_results:
        key = (r['athlete_name'], r['event'], r['date_str'], r['time'])
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    all_results = unique_results
    print(f"After deduplication: {len(all_results)} unique results")

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
