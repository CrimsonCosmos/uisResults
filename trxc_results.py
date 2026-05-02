#!/usr/bin/env python3
"""
TRXC Timing Live Results Scraper
Fetches results from liveresults.trxctiming.com for UIS athletes.
Used as a supplementary data source alongside Athletic.net and TFRRS.
"""

import re
import requests
from datetime import datetime
from typing import List, Optional


API_BASE = 'https://liveresults.trxctiming.com/api'

UIS_TEAM = 'U.I.S.'

# Map TRXC event names (with gender prefix stripped) to Athletic.net canonical names
TRXC_EVENT_MAP = {
    'High Jump': 'High Jump',
    'Pole Vault': 'Pole Vault',
    'Long Jump': 'Long Jump',
    'Triple Jump': 'Triple Jump',
    'Shot Put': 'Shot Put',
    'Discus': 'Discus',
    'Hammer': 'Hammer Throw',
    'Javelin': 'Javelin',
    '100 Meter Dash': '100 Meters',
    '200 Meter Dash': '200 Meters',
    '400 Meter Dash': '400 Meters',
    '800 Meter Run': '800 Meters',
    '1500 Meter Run': '1500 Meters',
    '5000 M. Run': '5000 Meters',
    '10000 Meter Run': '10000 Meters',
    '100 Meter Hurdles': '100 Hurdles',
    '110 Meter Hurdles': '110 Hurdles',
    '400 Meter Hurdles': '400 Hurdles',
    '3000 Meter S.C.': 'Steeplechase',
    # Multi-event sub-events
    '100 M. Dash': '100 Meters',
}

# Field events use marks (meters) rather than times
FIELD_EVENTS = {
    'High Jump', 'Pole Vault', 'Long Jump', 'Triple Jump',
    'Shot Put', 'Discus', 'Hammer Throw', 'Javelin',
}

# All event numbers to check (1-36 are standard events, 41-42 are multi-events)
ALL_EVENTS = list(range(1, 37)) + [41, 42]


def _normalize_event_name(raw_name):
    """
    Convert TRXC event name like '(W) 100 Meter Dash' or 'Hept (W) 100 Meter Hurdles'
    to canonical Athletic.net event name and gender.
    Returns (canonical_name, gender).
    """
    # Extract gender from (W) or (M) prefix
    gender = ''
    gender_match = re.search(r'\(([MW])\)', raw_name)
    if gender_match:
        gender = gender_match.group(1)

    # Strip gender prefix and multi-event prefix
    cleaned = re.sub(r'^(Hept|Dec)\s+', '', raw_name)
    cleaned = re.sub(r'\([MW]\)\s*', '', cleaned).strip()

    # Look up canonical name
    canonical = TRXC_EVENT_MAP.get(cleaned, cleaned)
    return canonical, gender


def _parse_field_best(attempts_str):
    """
    Parse field event attempts string like '6.14,+1.7,6.03,+1.6,6.27,+3.3,...'
    Returns the best valid mark as a string (e.g. '6.30m'), or None.
    """
    if not attempts_str:
        return None

    parts = attempts_str.split(',')
    best = None
    # Marks and winds alternate: mark, wind, mark, wind, ...
    for i in range(0, len(parts), 2):
        mark_str = parts[i].strip()
        if mark_str in ('F', '-', 'X', 'P', 'NH', 'DNS', 'DNF', 'FOUL', 'PASS', ''):
            continue
        try:
            val = float(mark_str)
            if best is None or val > best:
                best = val
        except ValueError:
            continue

    if best is not None:
        return f"{best:.2f}m"
    return None


def _is_dnf_time(raw_time, place):
    """Check if a result is DNS/DNF based on placeholder values."""
    try:
        secs = float(raw_time)
        if secs > 36000:  # > 10 hours = placeholder
            return True
    except (ValueError, TypeError):
        pass
    try:
        if int(place) >= 9999:
            return True
    except (ValueError, TypeError):
        pass
    return False


def _format_track_time(raw_time):
    """
    Format a track time string. The API returns times in two formats:
    - Short events: raw seconds like '11.553'
    - Longer events: pre-formatted like '1:59.736' or '35:09.555'
    Normalizes to 2 decimal places.
    """
    if not raw_time:
        return raw_time

    raw_time = str(raw_time).strip()

    # Already formatted as mm:ss.xxx or h:mm:ss.xxx - normalize decimals
    if ':' in raw_time:
        # Split off the seconds portion and truncate to 2 decimals
        parts = raw_time.rsplit(':', 1)
        prefix = parts[0]
        try:
            secs = float(parts[1])
            return f"{prefix}:{secs:05.2f}"
        except ValueError:
            return raw_time

    # Raw seconds (short events like sprints)
    try:
        secs = float(raw_time)
    except ValueError:
        return raw_time

    if secs >= 60:
        minutes = int(secs // 60)
        remainder = secs - minutes * 60
        return f"{minutes}:{remainder:05.2f}"
    else:
        return f"{secs:.2f}"


def _reverse_name(last_first):
    """Convert 'Last, First' to 'First Last'."""
    if ',' not in last_first:
        return last_first.strip()
    parts = last_first.split(',', 1)
    return f"{parts[1].strip()} {parts[0].strip()}"


def discover_uis_meets(cutoff_date):
    """
    Check all active and recent TRXC meets for UIS athletes.
    Returns list of {'meet_id': ..., 'date': ..., 'name': ..., 'sessions': [...]}
    for meets that have UIS athletes in the roster.
    """
    http = requests.Session()
    http.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
    })

    candidate_meets = []
    for endpoint in ['activeMeets', 'pastMeets']:
        try:
            resp = http.get(f"{API_BASE}/{endpoint}", timeout=15)
            if resp.status_code != 200:
                continue
            for meet in resp.json():
                meet_id = meet[0]
                date_str = meet[3]
                meet_date = None
                try:
                    meet_date = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
                except (ValueError, TypeError):
                    pass

                # Skip meets before cutoff
                if meet_date and meet_date < cutoff_date:
                    continue

                sessions_str = meet[10] or ''
                sessions = [s.strip() for s in sessions_str.split(',') if s.strip()]

                candidate_meets.append({
                    'meet_id': meet_id,
                    'date': meet_date,
                    'name': meet_id.replace('_', ' '),
                    'sessions': sessions,
                })
        except Exception:
            continue

    # Check each candidate's roster for UIS athletes
    uis_meets = []
    for meet in candidate_meets:
        try:
            resp = http.get(f"{API_BASE}/roster", params={'id': meet['meet_id']}, timeout=15)
            if resp.status_code != 200:
                continue
            roster = resp.json()
            has_uis = any(entry[3] == UIS_TEAM for entry in roster)
            if has_uis:
                uis_meets.append(meet)
        except Exception:
            continue

    return uis_meets


class TRXCResultsScraper:
    """Scrapes live results from TRXC Timing for UIS athletes at a specific meet."""

    def __init__(self, meet_id, cutoff_date):
        """
        Args:
            meet_id: str - TRXC meet identifier (e.g. 'GLVC_Outdoor_Championships_2026')
            cutoff_date: datetime - only include results on or after this date
        """
        self.meet_id = meet_id
        self.cutoff_date = cutoff_date
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36'
        })

    def scrape_all_results(self, meet_info=None):
        """
        Main entry point. Fetches all results for UIS athletes from this meet.
        Args:
            meet_info: optional dict with 'date', 'name', 'sessions' (skips meet lookup if provided)
        Returns list of result dicts in scraper.py format.
        """
        if meet_info:
            meet_date = meet_info['date']
            meet_name = meet_info['name']
            sessions = meet_info['sessions']
        else:
            info = self._get_meet_info()
            if not info:
                print("  TRXC: Meet not found in active or past meets")
                return []
            meet_date = info['date']
            meet_name = info['name']
            sessions = info['sessions']

        if meet_date and meet_date < self.cutoff_date:
            print(f"  TRXC: Meet date {meet_date.strftime('%b %d, %Y')} is before cutoff")
            return []

        # Get roster and find UIS athlete IDs
        uis_athletes = self._get_uis_roster()
        if not uis_athletes:
            print("  TRXC: No UIS athletes found in roster")
            return []

        print(f"  TRXC: Found {len(uis_athletes)} UIS athletes in roster")

        # Use first session for queries (results are the same across sessions)
        query_session = sessions[0] if sessions else 'Thursday'

        # Fetch results for all events
        all_results = []
        for event_num in ALL_EVENTS:
            results = self._fetch_event_results(event_num, query_session, uis_athletes,
                                                 meet_date, meet_name)
            all_results.extend(results)

        return all_results

    def _get_meet_info(self):
        """Fetch meet date and session info from the active/past meets API."""
        for endpoint in ['activeMeets', 'pastMeets']:
            try:
                resp = self.session.get(f"{API_BASE}/{endpoint}", timeout=15)
                if resp.status_code != 200:
                    continue
                meets = resp.json()
                for meet in meets:
                    if meet[0] == self.meet_id:
                        date_str = meet[3]
                        meet_date = None
                        try:
                            meet_date = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
                        except (ValueError, TypeError):
                            pass

                        sessions_str = meet[10] or ''
                        sessions = [s.strip() for s in sessions_str.split(',') if s.strip()]
                        name = self.meet_id.replace('_', ' ')

                        return {
                            'date': meet_date,
                            'name': name,
                            'sessions': sessions,
                        }
            except Exception:
                continue
        return None

    def _get_uis_roster(self):
        """
        Fetch the meet roster and return dict of UIS athletes.
        Returns {athlete_id: {'name': 'First Last', 'gender': 'M'/'W'}} for UIS athletes.
        """
        try:
            resp = self.session.get(f"{API_BASE}/roster", params={'id': self.meet_id}, timeout=15)
            resp.raise_for_status()
            roster = resp.json()
        except Exception as e:
            print(f"  TRXC: Could not fetch roster: {e}")
            return {}

        athletes = {}
        for entry in roster:
            # Format: [id, last, first, team, gender, events, class_year]
            # Some entries have null id (unregistered athletes listed in results)
            team = entry[3]
            if team != UIS_TEAM:
                continue

            athlete_id = entry[0]
            last_name = entry[1]
            first_name = entry[2]
            gender = entry[4]  # 'M' or 'F'

            name = f"{first_name} {last_name}"
            # Map 'F' to 'W' for consistency with Athletic.net
            gender_mapped = 'W' if gender == 'F' else gender

            if athlete_id is not None:
                athletes[athlete_id] = {'name': name, 'gender': gender_mapped}
            else:
                # For null-id entries, store by name for name-based matching
                athletes[f"name:{last_name},{first_name}"] = {'name': name, 'gender': gender_mapped}

        return athletes

    def _fetch_event_results(self, event_num, session, uis_athletes, meet_date, meet_name):
        """Fetch results for a single event and filter for UIS athletes."""
        results = []

        # Try both Track and Field types (one will have data, the other empty)
        for result_type in ['Track', 'Field']:
            # First, probe which rounds have data (up to 3: prelim, semi, final)
            rounds_with_data = []
            for rnd in [1, 2, 3]:
                try:
                    resp = self.session.get(f"{API_BASE}/results", params={
                        'id': self.meet_id,
                        'session': session,
                        'event': event_num,
                        'round': rnd,
                        'type': result_type,
                    }, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data:
                            rounds_with_data.append((rnd, data))
                except Exception:
                    continue

            if not rounds_with_data:
                continue

            total_rounds = len(rounds_with_data)

            # Use the latest round available (finals preferred)
            latest_round, latest_data = rounds_with_data[-1]

            if result_type == 'Track':
                results.extend(self._parse_track_results(latest_data, uis_athletes,
                                                          meet_date, meet_name,
                                                          total_rounds))
            else:
                results.extend(self._parse_field_results(latest_data, uis_athletes,
                                                          meet_date, meet_name))

        return results

    def _parse_track_results(self, data, uis_athletes, meet_date, meet_name,
                             total_rounds=1):
        """
        Parse track results array.
        Format: [round, event_num, ?, heat, event_name, wind, distance,
                 timestamp, place, athlete_id, lane, "Last, First", team, time, ...]

        total_rounds: how many rounds exist for this event (used to label prelim vs semi)
        """
        results = []
        seen_athletes = set()  # Avoid duplicate entries from multiple heats

        # Determine if this is a multi-heat round
        heats = set(entry[3] for entry in data)
        num_heats = len(heats)
        round_num = data[0][0] if data else 1

        for entry in data:
            athlete_id = entry[9]
            team = entry[12]
            name_raw = entry[11]  # "Last, First"

            # Check if this is a UIS athlete
            if team != UIS_TEAM:
                continue

            # Look up athlete info from roster
            athlete_info = uis_athletes.get(athlete_id)
            if not athlete_info:
                # Try name-based lookup
                athlete_info = uis_athletes.get(f"name:{name_raw.replace(', ', ',')}")
            if not athlete_info:
                # Use data from the result itself
                name = _reverse_name(name_raw)
                event_name_raw = entry[4]
                _, gender = _normalize_event_name(event_name_raw)
                athlete_info = {'name': name, 'gender': gender}

            # Dedup: only take best result per athlete per event
            event_name_raw = entry[4]
            canonical_event, gender = _normalize_event_name(event_name_raw)
            dedup_key = (athlete_info['name'], canonical_event)
            if dedup_key in seen_athletes:
                continue
            seen_athletes.add(dedup_key)

            raw_time = entry[13]
            place_raw = entry[8]
            heat = entry[3]

            # Skip DNS/DNF entries (placeholder times/places)
            if _is_dnf_time(raw_time, place_raw):
                continue

            time_str = _format_track_time(raw_time)

            # Format place with round/heat context
            # Multiple heats means this is NOT a final — it's a prelim or semi.
            # Label logic:
            #   - If 3+ rounds exist: R1=Prelim, R2=Semi
            #   - Otherwise (typical conference meet): multi-heat R1=Semi
            #   - Single heat = Final (no label needed)
            if num_heats > 1:
                if total_rounds >= 3 and round_num == 1:
                    round_label = "Prelim"
                else:
                    round_label = "Semi"
                place = f"{place_raw} (H{heat} {round_label})"
            else:
                place = str(place_raw)

            date_str = meet_date.strftime("%b %d, %Y") if meet_date else ''

            results.append({
                'athlete_name': athlete_info['name'],
                'athlete_id': f"trxc_{athlete_id}",
                'event': canonical_event,
                'time': time_str,
                'place': place,
                'date_str': date_str,
                'date': meet_date,
                'meet_name': meet_name,
                'record_type': None,
                'gender': athlete_info.get('gender', gender),
                'sport': 'Outdoor Track & Field',
                'source': 'trxc',
                'previous_pr': None,
                'previous_sr': None,
                'pr_improvement': 0,
                'sr_improvement': 0,
                'ncaa_standard': None,
                'ncaa_diff': None,
                'ncaa_diff_pct': None,
            })

        return results

    def _parse_field_results(self, data, uis_athletes, meet_date, meet_name):
        """
        Parse field results array.
        Format: [round, event_num, ?, flight, event_name, unit, "NULL",
                 place, flight_place, athlete_id, position, "Last, First", team, attempts_str]
        """
        results = []

        for entry in data:
            athlete_id = entry[9]
            team = entry[12]
            name_raw = entry[11]  # "Last, First"

            if team != UIS_TEAM:
                continue

            athlete_info = uis_athletes.get(athlete_id)
            if not athlete_info:
                athlete_info = uis_athletes.get(f"name:{name_raw.replace(', ', ',')}")
            if not athlete_info:
                name = _reverse_name(name_raw)
                event_name_raw = entry[4]
                _, gender = _normalize_event_name(event_name_raw)
                athlete_info = {'name': name, 'gender': gender}

            event_name_raw = entry[4]
            canonical_event, gender = _normalize_event_name(event_name_raw)

            attempts_str = entry[13]
            best_mark = _parse_field_best(attempts_str)
            if not best_mark:
                continue

            place = str(entry[7])
            date_str = meet_date.strftime("%b %d, %Y") if meet_date else ''

            results.append({
                'athlete_name': athlete_info['name'],
                'athlete_id': f"trxc_{athlete_id}",
                'event': canonical_event,
                'time': best_mark,
                'place': place,
                'date_str': date_str,
                'date': meet_date,
                'meet_name': meet_name,
                'record_type': None,
                'gender': athlete_info.get('gender', gender),
                'sport': 'Outdoor Track & Field',
                'source': 'trxc',
                'previous_pr': None,
                'previous_sr': None,
                'pr_improvement': 0,
                'sr_improvement': 0,
                'ncaa_standard': None,
                'ncaa_diff': None,
                'ncaa_diff_pct': None,
            })

        return results


if __name__ == "__main__":
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=14)

    print("=" * 60)
    print("TRXC Timing Results Scraper - Standalone Test")
    print(f"Looking for results since {cutoff.strftime('%b %d, %Y')}")
    print("=" * 60)

    print("\nDiscovering TRXC meets with UIS athletes...")
    meets = discover_uis_meets(cutoff)

    if not meets:
        print("No active TRXC meets found with UIS athletes.")
    else:
        print(f"Found {len(meets)} meet(s) with UIS athletes:")
        for m in meets:
            date_str = m['date'].strftime('%b %d, %Y') if m['date'] else 'unknown date'
            print(f"  - {m['name']} ({date_str})")

    all_results = []
    for meet in meets:
        print(f"\nScraping: {meet['name']}...")
        scraper = TRXCResultsScraper(meet['meet_id'], cutoff)
        results = scraper.scrape_all_results(meet_info=meet)
        all_results.extend(results)

    print(f"\n{'=' * 60}")
    print(f"Total UIS results: {len(all_results)}")
    print(f"{'=' * 60}")

    for r in all_results:
        print(f"  {r['athlete_name']:20s} {r['event']:15s} {r['time']:10s} "
              f"P:{r['place']:3s} {r['date_str']:15s} {r['meet_name']}")
