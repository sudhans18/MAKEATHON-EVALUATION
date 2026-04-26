
import sqlite3

def migrate(db_path):
    print(f"Migrating database: {db_path}")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Ensure columns exist
    try:
        c.execute("ALTER TABLE teams ADD COLUMN ps_id TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE teams ADD COLUMN category TEXT NOT NULL DEFAULT 'General'")
    except sqlite3.OperationalError:
        pass

    # Update data
    teams_data = [
        # Hardware Teams
        ("AXIOM", "HW0101", "HW"), ("INGLORIOUS", "HW0101", "HW"), ("COSMOTRONICS_26", "HW0102", "HW"),
        ("Sensoryx", "HW0103", "HW"), ("Mayhem Maestros", "HW0104", "HW"), ("Zero Drift", "HW0107", "HW"),
        ("SPARK INNOVATORS", "HW", "HW"), ("TECH TITANS", "HW", "HW"), ("NEXUS", "HW", "HW"),
        
        # Software Teams
        ("BYTE BUSTERS", "SW0101", "SW"), ("CYBER SENTINELS", "SW0102", "SW"), ("CODE CRUSADERS", "SW0103", "SW"),
        ("TECH WIZARDS", "SW0104", "SW"), ("DATA DYNAMICS", "SW0105", "SW"), ("ALGO ARCHITECTS", "SW0106", "SW"),
        ("DEBUG DEMONS", "SW0107", "SW"), ("SYSTEM SYNC", "SW0108", "SW"), ("LOGIC LORDS", "SW0109", "SW")
    ]

    for name, ps_id, category in teams_data:
        c.execute("UPDATE teams SET ps_id = ?, category = ? WHERE name = ?", (ps_id, category, name))
        if c.rowcount == 0:
            # Maybe the name is slightly different, try case-insensitive
            c.execute("UPDATE teams SET ps_id = ?, category = ? WHERE name LIKE ?", (ps_id, category, name))

    conn.commit()
    print("Migration successful.")
    conn.close()

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else 'backend/judging.db'
    migrate(path)
