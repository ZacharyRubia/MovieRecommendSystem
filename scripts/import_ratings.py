import pandas as pd
import mysql.connector
import hashlib
import time

def process_and_import_ratings(csv_path, db_config, chunk_size=100000):
    print(f"1. 准备处理 ratings.csv (采用分块读取，每块 {chunk_size} 条)...")
    
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    
    try:
        chunk_count = 0
        total_records = 0
        
        # 使用 chunksize 逐块读取大型 CSV
        for chunk in pd.read_csv(csv_path, chunksize=chunk_size):
            chunk_count += 1
            total_records += len(chunk)
            print(f"\n--- 正在处理第 {chunk_count} 块数据 (累计已读 {total_records} 条) ---")
            
            # ==========================================
            # 步骤 A: 提取并插入基础用户 (users 表)
            # ==========================================
            unique_users = chunk['userId'].unique()
            user_data = []
            for uid in unique_users:
                # 生成虚拟用户数据：id, username, password_hash, role_id(2=普通用户)
                user_data.append((int(uid), f"ml_user_{uid}", "bcrypt_dummy_hash_placeholder", 2))
                
            user_insert_query = """
            INSERT IGNORE INTO users (id, username, password_hash, role_id)
            VALUES (%s, %s, %s, %s)
            """
            cursor.executemany(user_insert_query, user_data)
            conn.commit()
            print(f"   ✅ 成功确保本块中 {len(unique_users)} 个用户存在于 users 表")

            # ==========================================
            # 步骤 B: 预处理行为流水数据 (生成 request_id 和时间转换)
            # ==========================================
            # 1. 批量生成全局唯一的 request_id: MD5(userId + movieId + timestamp)
            # 采用向量化字符串拼接后再 apply md5，提升性能
            str_concat = chunk['userId'].astype(str) + chunk['movieId'].astype(str) + chunk['timestamp'].astype(str)
            chunk['request_id'] = str_concat.apply(lambda x: hashlib.md5(x.encode('utf-8')).hexdigest())
            
            # 2. 将 Unix 时间戳转换为 MySQL 认识的 Datetime 格式
            chunk['created_at'] = pd.to_datetime(chunk['timestamp'], unit='s').dt.strftime('%Y-%m-%d %H:%M:%S')
            
            # 3. 准备插入行为表的数据 (user_id, movie_id, behavior_type, rating, request_id, created_at)
            # 过滤掉 rating 为空的异常数据（如有）
            valid_chunk = chunk.dropna(subset=['rating'])
            
            behavior_data = []
            for row in valid_chunk.itertuples(index=False):
                behavior_data.append((
                    row.userId, 
                    row.movieId, 
                    'rate', 
                    float(row.rating), 
                    row.request_id, 
                    row.created_at
                ))
                
            # ==========================================
            # 步骤 C: 插入用户行为流水 (user_movie_behavior)
            # ==========================================
            # 注意：如果某个 movieId 在前面导入 movie 表时被过滤掉了，
            # 这里的 INSERT IGNORE 会因为外键约束 (foreign key) 自动忽略该条记录，起到数据清洗作用。
            behavior_insert_query = """
            INSERT IGNORE INTO users_movies_behaviors 
            (user_id, movie_id, behavior_type, rating, request_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.executemany(behavior_insert_query, behavior_data)
            conn.commit()
            print(f"   ✅ 成功将 {len(behavior_data)} 条评分流水推入数据库")

        # ==========================================
        # 步骤 D: 全量聚合更新电影的平均分 (avg_rating)
        # ==========================================
        print("\n2. 所有评分数据写入完毕。正在开始重新计算并更新所有电影的平均评分...")
        start_time = time.time()
        
        # 使用 JOIN 进行全表批量更新，比在循环中逐条 UPDATE 快成百上千倍
        update_avg_query = """
        UPDATE movies m
        JOIN (
            SELECT movie_id, ROUND(AVG(rating), 2) as calc_avg 
            FROM users_movies_behaviors 
            WHERE behavior_type = 'rate' 
            GROUP BY movie_id
        ) b ON m.id = b.movie_id
        SET m.avg_rating = b.calc_avg;
        """
        cursor.execute(update_avg_query)
        conn.commit()
        
        print(f"   ✅ 电影平均评分更新完成！(耗时 {time.time() - start_time:.2f} 秒)")
        print("\n🎉 全部 ratings.csv 数据处理并入库完成！")

    except Exception as e:
        print(f"\n❌ 插入过程中发生错误: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    # 替换为你自己的数据库配置
    DB_CONFIG = {
        'host': '127.0.0.1',
        'port': 3306,
        'user': 'root',
        'password': 'your_password',  # 填入你的 MySQL 密码
        'database': 'MovieRecommendSystem',
        'charset': 'utf8mb4'
    }
    
    # 执行脚本，chunk_size 设为 10 万，如果你的电脑内存够大，可以调高至 20 万或 50 万
    process_and_import_ratings('ratings.csv', DB_CONFIG, chunk_size=100000)