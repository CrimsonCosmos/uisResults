#!/usr/bin/env python3
"""
TFRRS Results Scraper
Fetches individual athlete results from TFRRS for UIS athletes.
Used as a supplementary data source alongside Athletic.net.
"""

import re
import time
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Tuple


# TFRRS team page URLs
TEAM_URLS = {
    'M': 'https://www.tfrrs.org/teams/IL_college_m_Illinois_Springfield.html',
    'W': 'https://www.tfrrs.org/teams/IL_college_f_Illinois_Springfield.html',
}

TFRRS_BASE = 'https://www.tfrrs.org'

# Map TFRRS short event names to Athletic.net canonical names
TFRRS_EVENT_MAP = {
    # Sprints
    '55': '55 Meters', '55m': '55 Meters',
    '60': '60 Meters', '60m': '60 Meters',
    '100': '100 Meters', '100m': '100 Meters',
    '200': '200 Meters', '200m': '200 Meters',
    '400': '400 Meters', '400m': '400 Meters',
    # Distance
    '600': '600 Meters', '600m': '600 Meters',
    '800': '800 Meters', '800m': '800 Meters',
    '1000': '1000 Meters', '1000m': '1000 Meters',
    '1500': '1500 Meters', '1500m': '1500 Meters',
    'Mile': 'Mile', '1 Mile': 'Mile',
    '3000': '3000 Meters', '3000m': '3000 Meters',
    '5000': '5000 Meters', '5000m': '5000 Meters', '5K': '5000 Meters',
    '10000': '10000 Meters', '10000m': '10000 Meters', '10K': '10000 Meters',
    # Hurdles
    '60H': '60 Hurdles', '60 Hurdles': '60 Hurdles', '60m Hurdles': '60 Hurdles',
    '100H': '100 Hurdles', '100 Hurdles': '100 Hurdles', '100m Hurdles': '100 Hurdles',
    '110H': '110 Hurdles', '110 Hurdles': '110 Hurdles', '110m Hurdles': '110 Hurdles',
    '400H': '400 Hurdles', '400 Hurdles': '400 Hurdles', '400m Hurdles': '400 Hurdles',
    # Steeplechase
    'Steeplechase': 'Steeplechase', '3000 Steeplechase': 'Steeplechase',
    '3000m Steeplechase': 'Steeplechase', 'SC': 'Steeplechase',
    # Relays
    '4x100': '4x100 Relay', '4x100 Relay': '4x100 Relay',
    '4x200': '4x200 Relay', '4x200 Relay': '4x200 Relay',
    '4x400': '4x400 Relay', '4x400 Relay': '4x400 Relay',
    '4x800': '4x800 Relay', '4x800 Relay': '4x800 Relay',
    'DMR': 'DMR', 'Distance Medley Relay': 'DMR',
    # Field events
    'High Jump': 'High Jump', 'HJ': 'High Jump',
    'Pole Vault': 'Pole Vault', 'PV': 'Pole Vault',
    'Long Jump': 'Long Jump', 'LJ': 'Long Jump',
    'Triple Jump': 'Triple Jump', 'TJ': 'Triple Jump',
    'Shot Put': 'Shot Put', 'SP': 'Shot Put',
    'Discus': 'Discus', 'Disc': 'Discus',
    'Hammer': 'Hammer Throw', 'Hammer Throw': 'Hammer Throw',
    'Javelin': 'Javelin', 'Jav': 'Javelin',
    'Weight Throw': 'Weight Throw', 'WT': 'Weight Throw',
    # Multi-events
    'Decathlon': 'Decathlon', 'Dec': 'Decathlon',
    'Heptathlon': 'Heptathlon', 'Hep': 'Heptathlon',
    'Pentathlon': 'Pentathlon', 'Pent': 'Pentathlon',
    # XC distances
    '5K XC': '5K XC', '6K XC': '6K XC', '8K XC': '8K XC', '10K XC': '10K XC',
}

# Sport name mapping
SPORT_NAMES = {
    'xc': 'Cross Country',
    'indoor': 'Indoor Track & Field',
    'outdoor': 'Outdoor Track & Field',
}


def _parse_tfrrs_date(date_str):
    """
    Parse TFRRS date strings like "Apr 9-10, 2026", "Mar 28, 2026",
    "Feb 28-Mar 1, 2026". Returns the first date as a datetime object.
    """
    if not date_str:
        return None

    date_str = date_str.strip()
    # Remove extra whitespace (TFRRS uses "Apr  9" with double space sometimes)
    date_str = re.sub(r'\s+', ' ', date_str)

    # Handle range like "Feb 28-Mar 1, 2026" (cross-month range)
    cross_month = re.match(r'(\w+ \d+)-\w+ \d+, (\d{4})', date_str)
    if cross_month:
        first_part = cross_month.group(1) + ', ' + cross_month.group(2)
        try:
            return datetime.strptime(first_part, "%b %d, %Y")
        except ValueError:
            pass

    # Handle range like "Apr 9-10, 2026" (same-month range)
    range_match = re.match(r'(\w+ \d+)-\d+, (\d{4})', date_str)
    if range_match:
        first_part = range_match.group(1) + ', ' + range_match.group(2)
        try:
            return datetime.strptime(first_part, "%b %d, %Y")
        except ValueError:
            pass

    # Single date like "Mar 28, 2026"
    try:
        return datetime.strptime(date_str, "%b %d, %Y")
    except ValueError:
        pass

    return None


def _normalize_event(raw_event):
    """Normalize a TFRRS event name to Athletic.net canonical form."""
    raw_event = raw_event.strip()
    if raw_event in TFRRS_EVENT_MAP:
        return TFRRS_EVENT_MAP[raw_event]

    # Try case-insensitive
    for key, val in TFRRS_EVENT_MAP.items():
        if key.lower() == raw_event.lower():
            return val

    # Handle XC distances like "7k", "8k", "4.97 mile"
    if re.match(r'^\d+\.?\d*\s*(k|mile|mi)$', raw_event, re.IGNORECASE):
        return raw_event

    return raw_event


def _parse_placement(place_str):
    """Extract numeric placement from '27th (F)' or '4th'."""
    if not place_str:
        return ''
    match = re.search(r'(\d+)', place_str.strip())
    return match.group(1) if match else ''


def _determine_sport(meet_date, result_url=''):
    """
    Determine sport type from meet date and result URL.
    XC results have '/results/xc/' in the URL.
    Indoor: roughly Dec-Feb. Outdoor: Mar-Jun.
    """
    if '/results/xc/' in result_url:
        return 'xc'

    if meet_date:
        month = meet_date.month
        # Indoor season: December through early March
        # GLVC Indoor Championships can be late Feb / early March
        if month == 12 or month == 1 or month == 2:
            return 'indoor'
        if month >= 3 and month <= 7:
            return 'outdoor'
        if month >= 8 and month <= 11:
            return 'xc'

    return 'outdoor'  # Default


def _time_to_seconds(time_str):
    """Convert time/mark string to seconds for dedup comparison."""
    if not time_str:
        return None
    cleaned = re.sub(r'[PRSRprsr\s\*]+$', '', str(time_str)).strip()
    cleaned = cleaned.rstrip('a')  # altitude marker
    cleaned = cleaned.rstrip('m')  # meters suffix
    try:
        if ':' in cleaned:
            parts = cleaned.split(':')
            if len(parts) == 2:
                return round(int(parts[0]) * 60 + float(parts[1]), 2)
            elif len(parts) == 3:
                return round(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]), 2)
        else:
            return round(float(cleaned), 2)
    except (ValueError, IndexError):
        return None


def normalize_for_dedup(result):
    """
    Build a normalized deduplication key from a result dict.
    Returns (name, event, date, time_seconds) tuple.
    Athletic.net and TFRRS results that represent the same performance
    should produce the same key.
    """
    name = result.get('athlete_name', '').strip().lower()

    # Normalize event
    raw_event = result.get('event', '')
    event = _normalize_event(raw_event).lower()

    # Normalize date to YYYY-MM-DD for consistent comparison
    date_str = result.get('date_str', '')
    parsed = _parse_tfrrs_date(date_str)
    if parsed:
        date_key = parsed.strftime('%Y-%m-%d')
    else:
        date_key = date_str.strip().lower()

    # Normalize time to seconds
    time_str = result.get('time', '')
    time_secs = _time_to_seconds(time_str)
    if time_secs is not None:
        time_key = f"{time_secs:.2f}"
    else:
        time_key = time_str.strip().lower()

    return (name, event, date_key, time_key)


class TFRRSResultsScraper:
    """Scrapes individual athlete results from TFRRS for UIS athletes."""

    def __init__(self, cutoff_date, sports_to_check):
        """
        Args:
            cutoff_date: datetime - only include results on or after this date
            sports_to_check: list of (sport, year) tuples, e.g. [('outdoor', 2026)]
        """
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36'
        })
        self.cutoff_date = cutoff_date
        self.sports_to_check = sports_to_check
        self._active_sports = {s for s, _ in sports_to_check}

    def scrape_all_results(self):
        """
        Main entry point. Scrapes both men's and women's TFRRS pages.
        Returns list of result dicts in scraper.py format.
        """
        all_results = []

        for gender in ['M', 'W']:
            gender_label = "Men's" if gender == 'M' else "Women's"
            print(f"\n  TFRRS: Fetching {gender_label} roster...")

            roster = self._get_roster(gender)
            if not roster:
                print(f"  TFRRS: No {gender_label} roster found")
                continue

            print(f"  TFRRS: Found {len(roster)} {gender_label} athletes")

            for i, athlete in enumerate(roster):
                print(f"    [{i+1}/{len(roster)}] {athlete['name']}...", end=' ', flush=True)

                try:
                    results = self._get_athlete_results(athlete)
                    if results:
                        all_results.extend(results)
                        print(f"{len(results)} result(s)")
                    else:
                        print("no recent results")
                except Exception as e:
                    print(f"error: {e}")

                # Rate limit
                if i < len(roster) - 1:
                    time.sleep(0.5)

        return all_results

    def _get_roster(self, gender):
        """Fetch the team roster from TFRRS team page."""
        url = TEAM_URLS.get(gender)
        if not url:
            return []

        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  TFRRS: Could not fetch {gender} team page: {e}")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        athletes = []
        seen_ids = set()

        # Find the roster section - it's in a table within the panel body
        # Look for athlete links in the roster table (not the top marks table)
        # The roster table has links with "Last, First" format
        for link in soup.find_all('a', href=re.compile(r'/athletes/\d+')):
            href = link.get('href', '')
            id_match = re.search(r'/athletes/(\d+)', href)
            if not id_match:
                continue

            tfrrs_id = id_match.group(1)
            if tfrrs_id in seen_ids:
                continue

            raw_name = link.get_text(strip=True)

            # Only take "Last, First" formatted names (roster entries)
            # Skip names without comma (relay team member lists use "First Last")
            if ',' not in raw_name:
                continue

            seen_ids.add(tfrrs_id)
            name = self._normalize_name(raw_name)

            # Build full URL
            tfrrs_url = TFRRS_BASE + href if href.startswith('/') else href
            if not tfrrs_url.endswith('.html'):
                tfrrs_url += '.html'

            athletes.append({
                'name': name,
                'tfrrs_id': tfrrs_id,
                'tfrrs_url': tfrrs_url,
                'gender': gender,
            })

        return athletes

    @staticmethod
    def _normalize_name(raw_name):
        """Convert 'Last, First' to 'First Last'."""
        if ',' not in raw_name:
            return raw_name.strip()
        parts = raw_name.split(',', 1)
        last = parts[0].strip()
        first = parts[1].strip()
        return f"{first} {last}"

    def _get_athlete_results(self, athlete):
        """Fetch and parse a single athlete's TFRRS page for recent results."""
        try:
            resp = self.session.get(athlete['tfrrs_url'], timeout=30)
            resp.raise_for_status()
        except Exception as e:
            return []

        return self._parse_athlete_page(resp.text, athlete)

    def _parse_athlete_page(self, html, athlete):
        """Parse the HTML of a TFRRS athlete page and extract recent results."""
        soup = BeautifulSoup(html, 'html.parser')
        results = []

        # Find the meet-results tab pane
        meet_results_pane = soup.find('div', id='meet-results')
        if not meet_results_pane:
            # Fallback: search the whole page
            meet_results_pane = soup

        # Each meet is a <table class="table table-hover">
        for table in meet_results_pane.find_all('table', class_=re.compile(r'table.*table-hover')):
            # Extract meet info from header
            header = table.find('th')
            if not header:
                continue

            # Meet name from <a> tag in header
            meet_link = header.find('a')
            if not meet_link:
                continue
            meet_name = meet_link.get_text(strip=True)
            meet_url = meet_link.get('href', '')

            # Date from <span> in header
            date_span = header.find('span')
            if not date_span:
                continue
            date_text = date_span.get_text(strip=True)
            meet_date = _parse_tfrrs_date(date_text)

            if not meet_date:
                continue

            # Filter by cutoff date
            if meet_date < self.cutoff_date:
                continue

            # Determine sport from date and URL
            sport_key = _determine_sport(meet_date, meet_url)
            if sport_key not in self._active_sports:
                continue

            sport_name = SPORT_NAMES.get(sport_key, 'Outdoor Track & Field')
            date_str = meet_date.strftime("%b %d, %Y")

            # Parse result rows (skip header row)
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) < 2:
                    continue

                # Cell 0: Event name
                raw_event = cells[0].get_text(strip=True)
                if not raw_event:
                    continue
                event = _normalize_event(raw_event)

                # Cell 1: Time/mark (inside <a> tag)
                time_link = cells[1].find('a')
                if not time_link:
                    continue
                time_str = time_link.get_text(strip=True)
                result_url = time_link.get('href', '')

                # Re-check sport using result URL (more reliable for XC)
                if '/results/xc/' in result_url and sport_key != 'xc':
                    sport_key = 'xc'
                    sport_name = 'Cross Country'
                    if 'xc' not in self._active_sports:
                        continue

                # Cell 2: Placement (optional)
                place = ''
                if len(cells) >= 3:
                    place = _parse_placement(cells[2].get_text(strip=True))

                results.append({
                    'athlete_name': athlete['name'],
                    'athlete_id': f"tfrrs_{athlete['tfrrs_id']}",
                    'event': event,
                    'time': time_str,
                    'place': place,
                    'date_str': date_str,
                    'date': meet_date,
                    'meet_name': meet_name,
                    'record_type': None,
                    'gender': athlete['gender'],
                    'sport': sport_name,
                    'source': 'tfrrs',
                    'previous_pr': None,
                    'previous_sr': None,
                    'pr_improvement': 0,
                    'sr_improvement': 0,
                    'ncaa_standard': None,
                    'ncaa_diff': None,
                    'ncaa_diff_pct': None,
                })

        return results


# Test the scraper if run directly
if __name__ == "__main__":
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=14)
    sports = [('outdoor', 2026)]

    print("=" * 60)
    print("TFRRS Results Scraper - Standalone Test")
    print(f"Looking for results since {cutoff.strftime('%b %d, %Y')}")
    print("=" * 60)

    scraper = TFRRSResultsScraper(cutoff, sports)
    results = scraper.scrape_all_results()

    print(f"\n{'=' * 60}")
    print(f"Total results: {len(results)}")
    print(f"{'=' * 60}")

    for r in results[:20]:
        print(f"  {r['athlete_name']:20s} {r['event']:15s} {r['time']:10s} "
              f"{r['date_str']:15s} {r['meet_name']}")

    if len(results) > 20:
        print(f"  ... and {len(results) - 20} more")

    # Test dedup key generation
    print(f"\nDedup key samples:")
    for r in results[:5]:
        key = normalize_for_dedup(r)
        print(f"  {key}")
