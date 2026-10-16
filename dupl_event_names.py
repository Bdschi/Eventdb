import sqlite3
import json
from difflib import SequenceMatcher

def pretty_print_json(json_str):
    try:
        obj = json.loads(json_str)
        pretty = json.dumps(obj, indent=2, sort_keys=True)
        print(pretty)
    except Exception as e:
        print("Invalid JSON or error:", e)
        print(json_str)

def similarity(a, b):
    # Normalize JSON to avoid key order affecting similarity
    try:
        obj1 = json.loads(a)
        obj2 = json.loads(b)
        a = json.dumps(obj1, sort_keys=True)
        b = json.dumps(obj2, sort_keys=True)
    except:
        pass 
    return SequenceMatcher(None, a, b).ratio()

conn = sqlite3.connect('events.db')
cursor = conn.cursor()

# Find duplicate event names
cursor.execute('''
    SELECT event_name, COUNT(*) as cnt
    FROM events
    GROUP BY event_name
    HAVING cnt > 1
''')
duplicates = cursor.fetchall()

deleted_ids = set()

for event_name, count in duplicates:
    print(f"\n{'='*40}")
    print(f"Processing: {event_name} ({count} occurrences)")
    print(f"{'='*40}")
    
    cursor.execute('''
        SELECT id, raw_row_json
        FROM events
        WHERE event_name = ?
        ORDER BY id ASC
    ''', (event_name,))
    rows = cursor.fetchall()

    for i in range(len(rows)):
        id1, json1 = rows[i]
        if id1 in deleted_ids:
            continue

        for j in range(i + 1, len(rows)):
            id2, json2 = rows[j]
            if id2 in deleted_ids:
                continue

            sim = similarity(json1, json2)
            print(f"\nComparing ID {id1} vs ID {id2} | Similarity: {sim:.2%}")
            #print(f"  JSON 1: {json1[:100]}...")
            #print(f"  JSON 2: {json2[:100]}...")
            print("  JSON 1:")
            pretty_print_json(json1)
            print("  JSON 2:")
            pretty_print_json(json2)

            while True:
                choice = input("Action: [d]elete higher, [r]ename higher, [s]kip, [q]uit: ").lower().strip()
                
                if choice == 'd':
                    higher_id = max(id1, id2)
                    cursor.execute('DELETE FROM events WHERE id = ?', (higher_id,))
                    conn.commit()
                    deleted_ids.add(higher_id)
                    print(f"!!! Deleted ID {higher_id}")
                    break
                elif choice == 'r':
                    higher_id = max(id1, id2)
                    new_name = input(f"New event_name for ID {higher_id}: ").strip()
                    cursor.execute('UPDATE events SET event_name = ? WHERE id = ?', (new_name, higher_id))
                    conn.commit()
                    deleted_ids.add(higher_id)  # Treat rename as removal from this duplicate group
                    print(f"-> Renamed ID {higher_id} to {new_name}")
                    break
                elif choice == 's':
                    print("Skipped pair.")
                    break
                elif choice == 'q':
                    print("Exiting script.")
                    conn.close()
                    exit()
                else:
                    print("Invalid input.")
            
            if id2 in deleted_ids:
                continue

conn.close()
print("\nProcessing complete.")
