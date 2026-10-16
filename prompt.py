import sqlite3
import json

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
    identifying_cols = ["source_page_title", "raw_row_json", "event_name"]

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        query = f"SELECT {', '.join(identifying_cols)} FROM `{table_name}` WHERE id = ?"
        cursor.execute(query, (row_id,))
        row = cursor.fetchone()

    if not row:
        raise ValueError(f"No row found with id {row_id}")

    row_data = dict(zip(identifying_cols, row))

    prompt = f"""You are an expert data analyst.
Task: Infer missing event details from the 'raw_row_json' and 'event_name' and generate a SQL UPDATE statement.

Table Name: '{table_name}'
Target Columns: {', '.join(missing_columns)}

Input Data:
{json.dumps(row_data, indent=2)}

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


# Example usage:
db_path = 'events.db'
table_name = 'events'
#row_id = int(input("Enter the id to process: "))
row_id = get_id_with_null_date_start(db_path, table_name)
print(generate_ai_prompt_from_db(db_path, table_name, row_id))
