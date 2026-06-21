import sqlite3

conn = sqlite3.connect("healthlynked.db")
cols = [row[1] for row in conn.execute("PRAGMA table_info(providers)")]
print("عدد الأعمدة:", len(cols))
print("الأعمدة:", cols)
conn.close()