import json
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

USER_AGENT = (
    "MassacreResearchDatabase/0.2 "
    "(academic research; replace-with-your-email@example.org)"
)


def fetch_wikipedia_article(article_url):
    """
    Return the plain text of the Wikipedia article at article_url.

    Returns None when the URL is not a Wikipedia article or the page
    cannot be loaded.
    """
    if not article_url or not str(article_url).strip():
        return None

    parsed = urlparse(str(article_url).strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    if host != "wikipedia.org" and not host.endswith(".wikipedia.org"):
        return None

    if not parsed.path.startswith("/wiki/"):
        return None

    title = unquote(parsed.path[len("/wiki/"):]).replace("_", " ").strip()
    if not title:
        return None

    api_url = f"https://{host}/w/api.php?" + urlencode({
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "titles": title,
        "format": "json",
        "formatversion": "2",
    })
    request = Request(
        api_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return None

    page = pages[0]
    if page.get("missing") or page.get("invalid"):
        return None

    extract = (page.get("extract") or "").strip()
    return extract or None


def format_raw_row(raw_row_json):
    """Return raw_row_json as readable text."""
    if not raw_row_json:
        return ""

    try:
        parsed = json.loads(raw_row_json)
    except (TypeError, json.JSONDecodeError):
        return str(raw_row_json)

    return json.dumps(parsed, indent=2, ensure_ascii=False)


def input_data_for_row(article_url, raw_row_json):
    """
    Use the linked Wikipedia article as input data.

    Fall back to raw_row_json when article_url is empty or the article
    cannot be retrieved.
    """
    article = fetch_wikipedia_article(article_url)
    if article:
        return article

    return format_raw_row(raw_row_json)


def generate_ai_prompt_from_db(db_path, table_name, row_id):
    """
    Fetch a row by id from the SQLite database and generate an AI prompt
    to infer missing columns and create a SQL UPDATE statement using id in WHERE clause.
    """
    missing_columns = [
        "date_original", "date_start", "date_end", "date_precision", "location_original",
        "killed_original", "killed_min", "killed_max", "killed_confidence",
        "injured_original", "injured_min", "injured_max",
        "displaced_original", "displaced_min", "displaced_max"
    ]
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        query = f"SELECT article_url, raw_row_json FROM `{table_name}` WHERE id = ?"
        cursor.execute(query, (row_id,))
        row = cursor.fetchone()

    if not row:
        raise ValueError(f"No row found with id {row_id}")

    article_url, raw_row_json = row
    input_data = input_data_for_row(article_url, raw_row_json)

    prompt = f"""You are an expert data analyst.
Task: Infer missing event details from the input data and generate a SQL UPDATE statement.

Table Name: '{table_name}'
Target Columns: {', '.join(missing_columns)}

Input Data:
{input_data}

Instructions:
1. Extract dates, locations, and casualty figures (min/max/original).
2. 'killed_confidence' should be 'High', 'Medium', or 'Low' based on the source phrasing.
3. If a value is unknown, use NULL.
4. Set 'review_status' to your name (e.g. Claude Opus 4) in the SQL statement.
5. Set 'reviewed_at' to the current timestamp in the SQL statement.
6. IMPORTANT: Ensure the SQL statement is syntactically correct for SQLite. Escape all single quotes in strings.
7. Provide ONLY the SQL code block.

Output Format:
UPDATE {table_name} SET 
  review_status = '<your name>',
  reviewed_at = '<current timestamp>',
  date_original = '...', 
  killed_min = ..., 
  -- etc
WHERE id = {row_id}
AND date_start is NULL;
"""
    return prompt

def get_id_with_null_date_start(db_path, table_name):
    """
    Retrieves the id of the row with NULL date_start and the longest raw_row_json.
    Returns None if no such row exists.
    """
    # Basic validation to avoid SQL injection via table name
    if not table_name.isidentifier():
        raise ValueError(f"Invalid table name: {table_name}")

    query = f"SELECT id FROM {table_name} WHERE date_start IS NULL ORDER BY LENGTH(raw_row_json) DESC LIMIT 1"

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            result = cursor.fetchone()
            return result[0] if result else None
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None


if __name__ == "__main__":
    db_path = "events.db"
    table_name = "events"
    #row_id = int(input("Enter the id to process: "))
    row_id = get_id_with_null_date_start(db_path, table_name)
    print(generate_ai_prompt_from_db(db_path, table_name, row_id))
