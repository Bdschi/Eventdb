#!/usr/bin/env python3

import csv
import html
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


API_URL = "https://en.wikipedia.org/w/api.php"
INDEX_TITLE = "Lists of massacres"

DATABASE_FILE = "events.db"
RAW_DIRECTORY = Path("raw")
RAW_PAGES_DIRECTORY = RAW_DIRECTORY / "pages"

USER_AGENT = (
    "MassacreResearchDatabase/0.2 "
    "(academic research; replace-with-your-email@example.org)"
)

REQUEST_DELAY_SECONDS = 0.3


# ================================================================
# GENERAL HELPERS
# ================================================================

def clean_text(value):
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def api_request(params):
    params = dict(params)
    params["format"] = "json"
    params["formatversion"] = "2"

    url = API_URL + "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def wikipedia_url(title):
    return (
        "https://en.wikipedia.org/wiki/"
        + quote(title.replace(" ", "_"), safe=":/()'")
    )


# ================================================================
# HTML TABLE PARSER
# ================================================================

class TableParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.tables = []
        self.current_table = None
        self.current_row = None
        self.current_cell = None

        self.in_link = False
        self.current_link_href = None
        self.current_link_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)

        if tag == "table":
            self.current_table = []

        elif tag == "tr" and self.current_table is not None:
            self.current_row = []

        elif tag in ("th", "td") and self.current_row is not None:
            self.current_cell = {
                "text_parts": [],
                "links": [],
                "tag": tag,
            }

        elif tag == "a" and self.current_cell is not None:
            self.in_link = True
            self.current_link_href = attributes.get("href")
            self.current_link_text = []

    def handle_endtag(self, tag):
        if tag == "a" and self.in_link:
            link_text = clean_text("".join(self.current_link_text))

            if self.current_link_href:
                self.current_cell["links"].append({
                    "text": link_text,
                    "href": self.current_link_href,
                })

            self.in_link = False
            self.current_link_href = None
            self.current_link_text = []

        elif tag in ("th", "td") and self.current_cell is not None:
            self.current_cell["text"] = clean_text(
                "".join(self.current_cell["text_parts"])
            )

            self.current_row.append(self.current_cell)
            self.current_cell = None

        elif tag == "tr" and self.current_row is not None:
            self.current_table.append(self.current_row)
            self.current_row = None

        elif tag == "table" and self.current_table is not None:
            self.tables.append(self.current_table)
            self.current_table = None

    def handle_data(self, data):
        if self.current_cell is not None:
            self.current_cell["text_parts"].append(data)

        if self.in_link:
            self.current_link_text.append(data)


# ================================================================
# FIND SUBPAGES FROM THE INDEX
# ================================================================

def get_index_links():
    """
    Returns article links from the Lists of massacres index.

    We use the API instead of parsing the index HTML because the API
    handles pagination more reliably.
    """

    links = []
    continuation = {}

    while True:
        params = {
            "action": "query",
            "titles": INDEX_TITLE,
            "prop": "links",
            "plnamespace": "0",
            "pllimit": "max",
        }

        params.update(continuation)

        data = api_request(params)

        pages = data.get("query", {}).get("pages", [])

        for page in pages:
            for link in page.get("links", []):
                title = link["title"]

                # Keep pages whose title clearly belongs to this index.
                # This includes country, historical-country, conflict,
                # group, and other massacre list pages.
                if title.startswith("List of massacres"):
                    links.append(title)

                # Also retain known variants such as:
                # "List of mass executions and massacres in..."
                elif (
                    "massacres" in title.lower()
                    and title.lower().startswith("list of ")
                ):
                    links.append(title)

        if "continue" not in data:
            break

        continuation = data["continue"]

    # Remove duplicates while preserving order
    unique_links = []
    seen = set()

    for title in links:
        if title not in seen and title != INDEX_TITLE:
            unique_links.append(title)
            seen.add(title)

    return unique_links


# ================================================================
# FETCH AND SAVE WIKIPEDIA PAGES
# ================================================================

def fetch_page(title):
    data = api_request({
        "action": "parse",
        "page": title,
        "prop": "text|revid",
    })

    parsed = data["parse"]

    return {
        "title": parsed["title"],
        "revision_id": parsed["revid"],
        "html": parsed["text"],
    }


def save_page(page):
    RAW_PAGES_DIRECTORY.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", page["title"])
    html_file = RAW_PAGES_DIRECTORY / f"{safe_name}.html"
    metadata_file = RAW_PAGES_DIRECTORY / f"{safe_name}.json"

    html_file.write_text(page["html"], encoding="utf-8")

    metadata_file.write_text(
        json.dumps({
            "title": page["title"],
            "revision_id": page["revision_id"],
            "url": wikipedia_url(page["title"]),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )

    return str(html_file)


# ================================================================
# DATE PARSING
# ================================================================

def parse_date_interval(value):
    if not value:
        return {
            "original": None,
            "start": None,
            "end": None,
            "precision": "missing",
        }

    original = clean_text(str(value))
    text = re.sub(r"\[[^\]]+\]", "", original.lower())

    # Exact year
    match = re.search(r"\b(\d{3,4})\b", text)

    if match and "century" not in text:
        year = int(match.group(1))

        return {
            "original": original,
            "start": f"{year:04d}-01-01",
            "end": f"{year:04d}-12-31",
            "precision": "year",
        }

    # Decade, for example 1850s
    match = re.search(r"\b(\d{3,4})s\b", text)

    if match:
        year = int(match.group(1))

        return {
            "original": original,
            "start": f"{year:04d}-01-01",
            "end": f"{year + 9:04d}-12-31",
            "precision": "decade",
        }

    # Century
    match = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s*century",
        text,
    )

    if match:
        century = int(match.group(1))

        start_year = (century - 1) * 100 + 1
        end_year = century * 100

        if "early" in text:
            end_year = start_year + 32
            precision = "early_century"

        elif "mid" in text or "middle" in text:
            start_year += 33
            end_year = start_year + 33
            precision = "mid_century"

        elif "late" in text:
            start_year += 67
            precision = "late_century"

        else:
            precision = "century"

        return {
            "original": original,
            "start": f"{start_year:04d}-01-01",
            "end": f"{end_year:04d}-12-31",
            "precision": precision,
        }

    return {
        "original": original,
        "start": None,
        "end": None,
        "precision": "unparsed",
    }


# ================================================================
# CASUALTY PARSING
# ================================================================

def parse_number(value):
    value = value.lower().replace(",", "").strip()

    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(thousand|million)?",
        value,
    )

    if not match:
        return None

    number = float(match.group(1))
    multiplier = match.group(2)

    if multiplier == "thousand":
        number *= 1_000
    elif multiplier == "million":
        number *= 1_000_000

    return int(number)


def parse_casualties(value):
    if not value:
        return {
            "original": None,
            "minimum": None,
            "maximum": None,
            "confidence": "missing",
        }

    original = clean_text(str(value))
    text = re.sub(r"\[[^\]]+\]", "", original.lower())
    text = text.replace("–", "-").replace("—", "-")

    # Range: 500-1,000 or 500 to 1,000
    match = re.search(
        r"(\d[\d,.]*\s*(?:thousand|million)?)\s*"
        r"(?:-|to)\s*"
        r"(\d[\d,.]*\s*(?:thousand|million)?)",
        text,
    )

    if match:
        return {
            "original": original,
            "minimum": parse_number(match.group(1)),
            "maximum": parse_number(match.group(2)),
            "confidence": "range",
        }

    # Single number
    match = re.search(
        r"(\d[\d,.]*\s*(?:thousand|million)?)",
        text,
    )

    if not match:
        return {
            "original": original,
            "minimum": None,
            "maximum": None,
            "confidence": "qualitative_or_unparsed",
        }

    number = parse_number(match.group(1))

    if any(word in text for word in [
        "more than",
        "over ",
        "at least",
        "greater than",
    ]):
        return {
            "original": original,
            "minimum": number,
            "maximum": None,
            "confidence": "lower_bound",
        }

    if any(word in text for word in [
        "fewer than",
        "less than",
        "up to",
    ]):
        return {
            "original": original,
            "minimum": None,
            "maximum": number,
            "confidence": "upper_bound",
        }

    return {
        "original": original,
        "minimum": number,
        "maximum": number,
        "confidence": "single_reported_value",
    }


# ================================================================
# TABLE NORMALIZATION
# ================================================================

def normalize_column_name(value):
    value = clean_text(value).lower()
    value = re.sub(r"\[[^\]]+\]", "", value)

    aliases = {
        "date": "date_original",
        "dates": "date_original",
        "period": "date_original",

        "location": "location_original",
        "place": "location_original",
        "location(s)": "location_original",

        "deaths": "killed_original",
        "death toll": "killed_original",
        "killed": "killed_original",

        "casualties": "casualty_original",
        "casualty": "casualty_original",

        "name": "event_name",
        "event": "event_name",
        "massacre": "event_name",
    }

    return aliases.get(value, value)


def table_to_records(table):
    if not table:
        return []

    header_index = None
    headers = None

    for index, row in enumerate(table):
        cells = [cell["text"] for cell in row]

        if len(cells) >= 2 and any(cells):
            header_index = index
            headers = [normalize_column_name(c) for c in cells]
            break

    if headers is None:
        return []

    records = []

    for row in table[header_index + 1:]:
        cells = [cell["text"] for cell in row]

        if not any(cells):
            continue

        record = {}

        for index, header in enumerate(headers):
            if header:
                record[header] = cells[index] if index < len(cells) else ""

        records.append(record)

    return records


def get_value(record, names):
    for name in names:
        if name in record and record[name]:
            return record[name]

    return ""


# ================================================================
# SQLITE DATABASE
# ================================================================

def create_database(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS source_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_title TEXT NOT NULL UNIQUE,
            page_url TEXT NOT NULL,
            revision_id INTEGER,
            retrieved_at TEXT NOT NULL,
            local_file TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            event_name TEXT,

            date_original TEXT,
            date_start TEXT,
            date_end TEXT,
            date_precision TEXT,

            location_original TEXT,

            killed_original TEXT,
            killed_min INTEGER,
            killed_max INTEGER,
            killed_confidence TEXT,

            injured_original TEXT,
            injured_min INTEGER,
            injured_max INTEGER,

            displaced_original TEXT,
            displaced_min INTEGER,
            displaced_max INTEGER,

            article_url TEXT,

            source_page_title TEXT NOT NULL,
            source_revision_id INTEGER,

            source_table_number INTEGER,
            source_row_number INTEGER,

            raw_row_json TEXT,

            extraction_method TEXT,
            review_status TEXT,
            retrieved_at TEXT
        );
    """)


def insert_source_page(connection, page, local_file, retrieved_at):
    connection.execute("""
        INSERT OR REPLACE INTO source_pages (
            page_title,
            page_url,
            revision_id,
            retrieved_at,
            local_file
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        page["title"],
        wikipedia_url(page["title"]),
        page["revision_id"],
        retrieved_at,
        local_file,
    ))


def insert_event(connection, event):
    connection.execute("""
        INSERT INTO events (
            event_name,
            date_original,
            date_start,
            date_end,
            date_precision,
            location_original,
            killed_original,
            killed_min,
            killed_max,
            killed_confidence,
            injured_original,
            injured_min,
            injured_max,
            displaced_original,
            displaced_min,
            displaced_max,
            article_url,
            source_page_title,
            source_revision_id,
            source_table_number,
            source_row_number,
            raw_row_json,
            extraction_method,
            review_status,
            retrieved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event["event_name"],
        event["date_original"],
        event["date_start"],
        event["date_end"],
        event["date_precision"],
        event["location_original"],
        event["killed_original"],
        event["killed_min"],
        event["killed_max"],
        event["killed_confidence"],
        event["injured_original"],
        event["injured_min"],
        event["injured_max"],
        event["displaced_original"],
        event["displaced_min"],
        event["displaced_max"],
        event["article_url"],
        event["source_page_title"],
        event["source_revision_id"],
        event["source_table_number"],
        event["source_row_number"],
        event["raw_row_json"],
        event["extraction_method"],
        event["review_status"],
        event["retrieved_at"],
    ))


def export_csv(connection, filename="events.csv"):
    cursor = connection.execute("SELECT * FROM events")
    rows = cursor.fetchall()
    headers = [description[0] for description in cursor.description]

    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)


# ================================================================
# MAIN IMPORT PROCESS
# ================================================================

def main():
    retrieved_at = datetime.now(timezone.utc).isoformat()

    RAW_DIRECTORY.mkdir(exist_ok=True)
    RAW_PAGES_DIRECTORY.mkdir(parents=True, exist_ok=True)

    print("Finding massacre-list subpages...")
    subpages = get_index_links()

    print(f"Found {len(subpages)} subpages.")

    with open(RAW_DIRECTORY / "subpages.json", "w", encoding="utf-8") as file:
        json.dump(subpages, file, indent=2, ensure_ascii=False)

    connection = sqlite3.connect(DATABASE_FILE)
    create_database(connection)

    total_events = 0

    for page_number, title in enumerate(subpages, start=1):
        print(f"[{page_number}/{len(subpages)}] Downloading: {title}")

        try:
            page = fetch_page(title)
            local_file = save_page(page)

            insert_source_page(
                connection,
                page,
                local_file,
                retrieved_at,
            )

            parser = TableParser()
            parser.feed(page["html"])

            page_events = 0

            for table_number, table in enumerate(parser.tables):
                records = table_to_records(table)

                for row_number, record in enumerate(records):
                    event_name = get_value(
                        record,
                        ["event_name"],
                    )

                    date_original = get_value(
                        record,
                        ["date_original"],
                    )

                    location_original = get_value(
                        record,
                        ["location_original"],
                    )

                    killed_original = get_value(
                        record,
                        [
                            "killed_original",
                            "casualty_original",
                        ],
                    )

                    # Ignore clearly irrelevant navigation tables.
                    if not event_name and not date_original:
                        continue

                    date_data = parse_date_interval(date_original)
                    killed_data = parse_casualties(killed_original)

                    event = {
                        "event_name": event_name,
                        "date_original": date_data["original"],
                        "date_start": date_data["start"],
                        "date_end": date_data["end"],
                        "date_precision": date_data["precision"],

                        "location_original": location_original,

                        "killed_original": killed_data["original"],
                        "killed_min": killed_data["minimum"],
                        "killed_max": killed_data["maximum"],
                        "killed_confidence": killed_data["confidence"],

                        "injured_original": None,
                        "injured_min": None,
                        "injured_max": None,

                        "displaced_original": None,
                        "displaced_min": None,
                        "displaced_max": None,

                        "article_url": None,

                        "source_page_title": page["title"],
                        "source_revision_id": page["revision_id"],

                        "source_table_number": table_number,
                        "source_row_number": row_number,

                        "raw_row_json": json.dumps(
                            record,
                            ensure_ascii=False,
                        ),

                        "extraction_method": "html_table",
                        "review_status": "unreviewed",
                        "retrieved_at": retrieved_at,
                    }

                    insert_event(connection, event)
                    page_events += 1
                    total_events += 1

            connection.commit()

            print(f"    Imported {page_events} records.")

        except Exception as error:
            print(f"    ERROR: {error}")

        # Be polite to Wikipedia's API.
        time.sleep(REQUEST_DELAY_SECONDS)

    export_csv(connection)
    connection.close()

    print()
    print(f"Finished. Imported {total_events} records.")
    print(f"Database: {DATABASE_FILE}")
    print("CSV export: events.csv")
    print("Source pages: raw/pages/")
    print("Subpage list: raw/subpages.json")


if __name__ == "__main__":
    main()
