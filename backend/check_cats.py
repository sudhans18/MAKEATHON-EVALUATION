import sqlite3
db = sqlite3.connect("judging.db")
res = db.execute("SELECT DISTINCT category FROM teams").fetchall()
print([r[0] for r in res])
db.close()
