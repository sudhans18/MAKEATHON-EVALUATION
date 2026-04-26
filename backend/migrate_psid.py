import sqlite3
import os

DB_PATH = "judging.db"

def migrate():
    db = sqlite3.connect(DB_PATH)
    try:
        # Check if ps_id column exists
        cursor = db.execute("PRAGMA table_info(teams)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "ps_id" not in columns:
            print("Adding ps_id column to teams table...")
            db.execute("ALTER TABLE teams ADD COLUMN ps_id TEXT NOT NULL DEFAULT ''")
            db.commit()
            print("Column added.")
        else:
            print("ps_id column already exists.")

        # Extract ps_id from problem_statement if it looks like "ID | Description"
        rows = db.execute("SELECT id, problem_statement FROM teams").fetchall()
        for row_id, ps_text in rows:
            if "|" in ps_text:
                parts = ps_text.split("|", 1)
                ps_id = parts[0].strip()
                new_ps = parts[1].strip()
                db.execute("UPDATE teams SET ps_id = ?, problem_statement = ? WHERE id = ?", (ps_id, new_ps, row_id))
            elif ps_text.startswith("HW") or ps_text.startswith("SW") or ps_text.startswith("IS"):
                # Handle cases where it might just be the ID or start with it
                words = ps_text.split()
                if words and (words[0].startswith("HW") or words[0].startswith("SW") or words[0].startswith("IS")):
                    db.execute("UPDATE teams SET ps_id = ? WHERE id = ?", (words[0], row_id))
        
        db.commit()
        print("Data migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
