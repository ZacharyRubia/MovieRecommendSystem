import pymysql

conn = pymysql.connect(
    host='192.168.200.128', port=3306,
    user='newuser', password='yourpassword',
    database='MovieRecommendSystem', charset='utf8mb4'
)
c = conn.cursor()

# Add request_id column
print("Adding request_id column...")
c.execute("""
    ALTER TABLE users_movies_behaviors 
    ADD COLUMN request_id VARCHAR(64) NOT NULL AFTER page_referer
""")
conn.commit()
print("Column added successfully")

# Add unique index
print("Adding unique index uk_request_id...")
try:
    c.execute("""
        ALTER TABLE users_movies_behaviors 
        ADD UNIQUE INDEX uk_request_id (request_id)
    """)
    conn.commit()
    print("Unique index added successfully")
except Exception as e:
    print(f"Index might already exist: {e}")
    conn.rollback()

# Verify
c.execute("DESCRIBE users_movies_behaviors")
print("\nUpdated schema:")
for r in c.fetchall():
    print(f"  {r[0]} ({r[1]})")

c.close()
conn.close()
print("\nDone!")