#!/usr/bin/env python3
"""
TFRRS GLVC Rankings Scraper
Fetches current GLVC Indoor Performance List rankings from TFRRS.
Used to add conference ranking context to UIS athlete results.
"""

import requests
from bs4 import BeautifulSoup
import re
from typing import Dict, List, Optional, Tuple


# URL for GLVC Indoor Performance List (2025-26 season)
TFRRS_GLVC_URL = "https://www.tfrrs.org/lists/4940/GLVC_Indoor_Performance_List"

# Event name mapping: Athletic.net variations -> TFRRS canonical name
EVENT_NAME_MAP = {
    # Sprints
    '60 Meter': '60 Meters',
    '60 Meters': '60 Meters',
    '60m': '60 Meters',
    '200 Meter': '200 Meters',
    '200 Meters': '200 Meters',
    '200m': '200 Meters',
    '400 Meter': '400 Meters',
    '400 Meters': '400 Meters',
    '400m': '400 Meters',

    # Middle/Long Distance
    '800 Meter': '800 Meters',
    '800 Meters': '800 Meters',
    '800m': '800 Meters',
    '1 Mile': 'Mile',
    'Mile': 'Mile',
    '3000 Meter': '3000 Meters',
    '3000 Meters': '3000 Meters',
    '3000m': '3000 Meters',
    '5000 Meter': '5000 Meters',
    '5000 Meters': '5000 Meters',
    '5000m': '5000 Meters',
    '5K': '5000 Meters',

    # Hurdles
    '60 Hurdles': '60 Hurdles',
    '60m Hurdles': '60 Hurdles',
    '60H': '60 Hurdles',

    # Field Events
    'High Jump': 'High Jump',
    'HJ': 'High Jump',
    'Pole Vault': 'Pole Vault',
    'PV': 'Pole Vault',
    'Long Jump': 'Long Jump',
    'LJ': 'Long Jump',
    'Triple Jump': 'Triple Jump',
    'TJ': 'Triple Jump',
    'Shot Put': 'Shot Put',
    'SP': 'Shot Put',
    'Weight Throw': 'Weight Throw',
    'WT': 'Weight Throw',
}

# Field events where higher mark is better
FIELD_EVENTS = {'High Jump', 'Pole Vault', 'Long Jump', 'Triple Jump',
                'Shot Put', 'Weight Throw', 'Discus', 'Hammer', 'Javelin'}

# Top N athletes qualify for conference championship
QUALIFYING_SPOTS = 16


class GLVCRankings:
    """
    Fetches and caches GLVC rankings from TFRRS.
    Instantiate once per scraper run, call fetch_rankings() first.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        # Cache: key = "{event}_{gender}" e.g. "Mile_M", value = list of mark values (sorted)
        self._rankings_cache: Dict[str, List[float]] = {}
        self._fetched = False

    def fetch_rankings(self) -> bool:
        """
        Fetch all GLVC rankings from TFRRS.
        Returns True on success, False on failure.
        Call this once at the start of scraper run.
        """
        try:
            print("  Fetching GLVC Indoor Performance List from TFRRS...")
            resp = self.session.get(TFRRS_GLVC_URL, timeout=30)
            resp.raise_for_status()
            self._parse_rankings_page(resp.text)
            self._fetched = True
            print(f"  Loaded rankings for {len(self._rankings_cache)} event/gender combinations")
            return True
        except Exception as e:
            print(f"  Warning: Could not fetch GLVC rankings: {e}")
            return False

    def _parse_rankings_page(self, html: str):
        """Parse the TFRRS page and extract rankings by event/gender."""
        soup = BeautifulSoup(html, 'html.parser')

        # Find all event sections - they have class pattern "gender_X standard_event_hnd_##"
        event_sections = soup.find_all('div', class_=re.compile(r'gender_[mf]\s+standard_event'))

        for section in event_sections:
            # Determine gender from class
            classes = section.get('class', [])
            gender = None
            for cls in classes:
                if 'gender_m' in cls:
                    gender = 'M'
                    break
                elif 'gender_f' in cls:
                    gender = 'W'
                    break

            if not gender:
                continue

            # Find event name - it's in a div with class containing the event name
            # Look for text like "60 Meters", "Mile", "800 Meters", etc.
            event_name = None
            header = section.find(['h3', 'h4', 'div'], class_=re.compile(r'panel-title|event-title'))
            if header:
                text = header.get_text(strip=True)
                # Extract event name (remove "(Men)" or "(Women)")
                text = re.sub(r'\s*\((Men|Women)\)\s*', '', text).strip()
                event_name = text
            else:
                # Try to find event name from the section text
                section_text = section.get_text()
                for evt in ['60 Meters', '200 Meters', '400 Meters', '800 Meters', 'Mile',
                           '3000 Meters', '5000 Meters', '60 Hurdles', 'High Jump',
                           'Pole Vault', 'Long Jump', 'Triple Jump', 'Shot Put', 'Weight Throw']:
                    if evt in section_text:
                        event_name = evt
                        break

            if not event_name:
                continue

            # Extract performance marks from links
            # Times are in <a> tags that link to results pages
            marks = []
            for link in section.find_all('a', href=re.compile(r'/results/')):
                text = link.get_text(strip=True)
                # Check if this looks like a time or mark
                if re.match(r'^\d+[:.]\d+', text) or re.match(r'^\d+\.\d+m?$', text):
                    mark_value = self._parse_mark_to_value(text, event_name in FIELD_EVENTS)
                    if mark_value and mark_value != float('inf'):
                        marks.append(mark_value)

            if marks:
                cache_key = f"{event_name}_{gender}"
                # Sort: lower is better for time, higher for field
                is_field = event_name in FIELD_EVENTS
                marks.sort(reverse=is_field)
                self._rankings_cache[cache_key] = marks

    def _parse_mark_to_value(self, mark_str: str, is_field: bool) -> Optional[float]:
        """
        Convert time/mark string to numeric value.
        For time events: returns seconds
        For field events: returns meters
        """
        if not mark_str:
            return None

        # Clean the string
        mark_str = mark_str.strip()
        mark_str = re.sub(r'[*#a-zA-Z]+$', '', mark_str)  # Remove trailing letters/symbols
        mark_str = mark_str.replace('m', '')  # Remove 'm' suffix from field events

        try:
            if ':' in mark_str:
                # Time format: MM:SS.ss or H:MM:SS.ss
                parts = mark_str.split(':')
                if len(parts) == 2:
                    return int(parts[0]) * 60 + float(parts[1])
                elif len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                # Direct numeric value (seconds or meters)
                return float(mark_str)
        except (ValueError, IndexError):
            return None

    def get_ranking(self, event: str, gender: str,
                    athlete_mark: float) -> Tuple[Optional[int], Optional[float], Optional[float]]:
        """
        Get GLVC ranking info for an athlete's performance.

        Args:
            event: Event name from Athletic.net
            gender: 'M' or 'W'
            athlete_mark: Athlete's mark in seconds (time) or meters (field)

        Returns:
            Tuple of (rank, gap_ahead, gap_behind)
            - rank: 1-16 if ranked, None if not in top 16
            - gap_ahead: gap to next person (positive = ahead of them)
            - gap_behind: gap to previous person, or gap to 16th place if unranked
        """
        if not self._fetched or not gender:
            return None, None, None

        # Normalize event name
        tfrrs_event = EVENT_NAME_MAP.get(event, event)
        cache_key = f"{tfrrs_event}_{gender}"

        if cache_key not in self._rankings_cache:
            # Try case-insensitive match
            for key in self._rankings_cache:
                if key.lower() == cache_key.lower():
                    cache_key = key
                    break
            else:
                return None, None, None

        rankings = self._rankings_cache[cache_key]
        if not rankings:
            return None, None, None

        is_field = tfrrs_event in FIELD_EVENTS

        # Find where this athlete would rank
        # Rankings are already sorted (best first)
        rank = None
        for i, mark in enumerate(rankings):
            if is_field:
                # Field: athlete beats this mark if their mark is higher or equal
                if athlete_mark >= mark:
                    rank = i + 1
                    break
            else:
                # Time: athlete beats this mark if their time is lower or equal
                if athlete_mark <= mark:
                    rank = i + 1
                    break

        if rank is None:
            # Athlete didn't beat anyone in the list
            rank = len(rankings) + 1

        # Calculate gaps
        gap_ahead = None  # Gap to person ranked below (who they're ahead of)
        gap_behind = None  # Gap to person ranked above (who they're behind)

        if rank <= len(rankings):
            # There's someone at or below this rank - gap to the person just below
            if rank < len(rankings):
                mark_below = rankings[rank]  # Person ranked just below (index = rank since 0-indexed)
                if is_field:
                    gap_ahead = athlete_mark - mark_below
                else:
                    gap_ahead = mark_below - athlete_mark

        if rank > 1:
            # There's someone above this rank
            mark_above = rankings[rank - 2]  # Person ranked just above
            if is_field:
                gap_behind = mark_above - athlete_mark
            else:
                gap_behind = athlete_mark - mark_above

        # Check if ranked in top 16
        if rank > QUALIFYING_SPOTS:
            # Not ranked - calculate gap to qualifying (16th place)
            if len(rankings) >= QUALIFYING_SPOTS:
                mark_16th = rankings[QUALIFYING_SPOTS - 1]
                if is_field:
                    gap_to_qualify = mark_16th - athlete_mark
                else:
                    gap_to_qualify = athlete_mark - mark_16th
                return None, None, gap_to_qualify
            return None, None, None

        return rank, gap_ahead, gap_behind

    def is_field_event(self, event: str) -> bool:
        """Check if an event is a field event (higher is better)."""
        tfrrs_event = EVENT_NAME_MAP.get(event, event)
        return tfrrs_event in FIELD_EVENTS


def format_gap(gap: Optional[float], is_field: bool) -> str:
    """Format a gap value for display."""
    if gap is None:
        return '-'

    if is_field:
        # Show in meters with 2 decimal places
        return f"{gap:.2f}m"
    else:
        # Show in seconds with 2 decimal places
        return f"{gap:.2f}"


# Test the scraper if run directly
if __name__ == "__main__":
    glvc = GLVCRankings()
    if glvc.fetch_rankings():
        print(f"\nLoaded events: {list(glvc._rankings_cache.keys())}")

        print("\nTesting rankings lookup:")
        # Test Mile for men (4:10 = 250 seconds)
        rank, ahead, behind = glvc.get_ranking("1 Mile", "M", 250.0)
        ahead_str = f"{ahead:.2f}" if ahead else "N/A"
        behind_str = f"{behind:.2f}" if behind else "N/A"
        print(f"Mile 4:10 (M): rank={rank}, ahead={ahead_str}, behind={behind_str}")

        # Test 800m for women (2:15 = 135 seconds)
        rank, ahead, behind = glvc.get_ranking("800 Meters", "W", 135.0)
        ahead_str = f"{ahead:.2f}" if ahead else "N/A"
        behind_str = f"{behind:.2f}" if behind else "N/A"
        print(f"800m 2:15 (W): rank={rank}, ahead={ahead_str}, behind={behind_str}")

        # Test what would be a good Mile time (4:08 = 248 seconds)
        rank, ahead, behind = glvc.get_ranking("Mile", "M", 248.0)
        ahead_str = f"{ahead:.2f}" if ahead else "N/A"
        behind_str = f"{behind:.2f}" if behind else "N/A"
        print(f"Mile 4:08 (M): rank={rank}, ahead={ahead_str}, behind={behind_str}")
    else:
        print("Failed to fetch rankings")
