import mysql.connector

conn = mysql.connector.connect(
    host='192.168.1.38',
    user='newuser',
    password='yourpassword',
    database='MovieRecommendSystem',
    charset='utf8mb4'
)
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM users_movies_behaviors WHERE behavior_type='rate'")
print('ratings:', cursor.fetchone()[0])

cursor.execute("SELECT COUNT(*) FROM users")
print('users:', cursor.fetchone()[0])

cursor.execute("SELECT COUNT(*) FROM movies")
print('movies:', cursor.fetchone()[0])

cursor.execute("SHOW TABLES LIKE '%cache%'")
tables = cursor.fetchall()
print('cache tables:', [t[0] for t in tables])

# Check if rating data has enough for testing
cursor.execute("SELECT COUNT(DISTINCT movie_id) FROM users_movies_behaviors WHERE behavior_type='rate'")
print('movies with ratings:', cursor.fetchone()[0])

cursor.execute("SELECT COUNT(DISTINCT user_id) FROM users_movies_behaviors WHERE behavior_type='rate'")
print('users with ratings:', cursor.fetchone()[0])

# Check if there's a sample user with enough ratings
cursor.execute("""
    SELECT user_id, COUNT(*) as cnt FROM users_movies_behaviors 
    WHERE behavior_type='rate' GROUP BY user_id ORDER BY cnt DESC LIMIT 5
""")
print('top users by rating count:', cursor.fetchall())

cursor.close()
conn.close()