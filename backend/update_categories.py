import sqlite3
import os

def update():
    db_path = os.path.join(os.path.dirname(__file__), "judging.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Hardware Teams
    hw_teams = [
        "AXIOM", "INGLORIOUS", "COSMOTRONICS_26", "Sensoryx", "Mayhem Maestros",
        "Zero Drift", "STRAW HATS", "AegisNet_26", "Sentinels", "NEURAL NINJAS",
        "Floodguard", "SHADOW GARDEN", "CarbonX", "Byte Busters", "WattWise",
        "Codesmiths", "S.E.N.S.E", "VECTOR",
        # From Industry (Hardware/IS)
        "ECLIPSE", "Apex Innovators", "DECADES", "NOVA CORE", "Team Botz", "SIGNAL SEEKERS"
    ]

    # Software Teams
    sw_teams = [
        "InsightX", "Climax", "GREAT MINDS", "VELOURA SQUAD", "PIXEL",
        "ORIGINX", "JD", "ALGORIX", "TEAM NAME", "QUADSQUAD", "ByteCraft",
        "Caffeine crew", "Storm Breaker", "ELITE FORCE", "KNULL", "super nova",
        # From Industry (Software/IS)
        "CELESTIALS", "NO WAY", "LITEFORCE", "The Nibble", "Hackx",
        "Access Denied", "TEAM ROGERS", "PANJAMUM BUDGET - UHM"
    ]

    # Ensure column exists
    try:
        cursor.execute("ALTER TABLE teams ADD COLUMN category TEXT DEFAULT 'SW'")
        print("Column 'category' added to teams table.")
    except sqlite3.OperationalError:
        print("Column 'category' already exists.")

    for team in hw_teams:
        cursor.execute("UPDATE teams SET category = 'HW' WHERE name = ?", (team,))
    
    for team in sw_teams:
        cursor.execute("UPDATE teams SET category = 'SW' WHERE name = ?", (team,))

    conn.commit()
    print(f"Updated {len(hw_teams)} HW teams and {len(sw_teams)} SW teams.")
    conn.close()

if __name__ == "__main__":
    update()
