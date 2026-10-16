import sqlite3
import json
from difflib import SequenceMatcher

def similarity(a, b):
    try:
        obj1 = json.loads(a)
        obj2 = json.loads(b)
        a = json.dumps(obj1, sort_keys=True)
        b = json.dumps(obj2, sort_keys=True)
    except:
        pass
    return SequenceMatcher(None, a, b).ratio()

def merge_json(target_json_str, source_json_str):
    try:
        target_obj = json.loads(target_json_str)
        source_obj = json.loads(source_json_str)
        for key, value in source_obj.items():
            if key not in target_obj:
                target_obj[key] = value
        return json.dumps(target_obj, sort_keys=True)
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return target_json_str

conn = sqlite3.connect('events.db')
cursor = conn.cursor()

cursor.execute('SELECT event_name, COUNT(*) as cnt FROM events GROUP BY event_name HAVING cnt > 1')
duplicates = cursor.fetchall()

deleted_or_moved_ids = set()

for event_name, count in duplicates:
    print(f"\n{'='*60}")
    print(f"Processing: {event_name.upper()} ({count} occurrences)")
    print(f"{'='*60}")
    
    cursor.execute('SELECT id, raw_row_json FROM events WHERE event_name = ? ORDER BY id ASC', (event_name,))
    rows = cursor.fetchall()

    current_data = {row[0]: row[1] for row in rows}
    ids = sorted(current_data.keys())

    for i in range(len(ids)):
        id1 = ids[i]
        if id1 in deleted_or_moved_ids:
            continue

        for j in range(i + 1, len(ids)):
            id2 = ids[j]
            if id2 in deleted_or_moved_ids:
                continue

            json1 = current_data[id1]
            json2 = current_data[id2]

            sim = similarity(json1, json2)
            print(f"\nID {id1} vs ID {id2} | Similarity: {sim:.2%}")

            if sim >= 0.10:
                merged_json = merge_json(json1, json2)
                cursor.execute('UPDATE events SET raw_row_json = ? WHERE id = ?', (merged_json, id1))
                cursor.execute('DELETE FROM events WHERE id = ?', (id2,))
                current_data[id1] = merged_json
                deleted_or_moved_ids.add(id2)
                conn.commit()
                print(f"  [AUTO] Merged ID {id2} into ID {id1} and deleted {id2}.")
            else:
                print(f"  JSON 1: {json1[:500]}...")
                print(f"  JSON 2: {json2[:500]}...")
                
                choice = input("  Action: [d]elete ID2, [r]ename ID2, [s]kip, [q]uit: ").lower().strip()
                
                if choice == 'd':
                    cursor.execute('DELETE FROM events WHERE id = ?', (id2,))
                    deleted_or_moved_ids.add(id2)
                    conn.commit()
                    print(f"  !!! Deleted ID {id2}")
                elif choice == 'r':
                    new_name = input(f"  New event_name for ID {id2}: ").strip()
                    cursor.execute('UPDATE events SET event_name = ? WHERE id = ?', (new_name, id2))
                    deleted_or_moved_ids.add(id2)
                    conn.commit()
                    print(f"  -> Renamed ID {id2} to {new_name}")
                elif choice == 'q':
                    print("Exiting.")
                    conn.close()
                    exit()
                else:
                    print("  Skipped pair.")

conn.close()
print("\nProcessing complete.")
