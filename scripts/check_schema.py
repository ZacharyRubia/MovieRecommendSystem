import pymysql

conn = pymysql.connect(
    host='192.168.1.38', port=3306,
    user='newuser', password='yourpassword',
    database='MovieRecommendSystem', charset='utf8mb4'
)
c = conn.cursor()

c.execute("""
    SELECT column_name, ordinal_position 
    FROM information_schema.columns 
    WHERE table_schema = database() 
      AND table_name = 'users_movies_behaviors' 
    ORDER BY ordinal_position
""")
print("users_movies_behaviors columns:")
for r in c.fetchall():
    print(f"  {r[1]}: {r[0]}")

c.close()
conn.close()