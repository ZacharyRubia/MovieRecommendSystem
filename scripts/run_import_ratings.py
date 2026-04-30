"""
导入 ratings.csv 数据到 MovieRecommendSystem 数据库
将评分数据安全地写入 users、users_movies_behaviors 表，并更新 movies.avg_rating
使用 PyMySQL（与后端一致）
"""
import sys
import io
import math
import hashlib
import time
import pandas as pd
import pymysql
import os

# 确保标准输出编码为 UTF-8，避免 Windows 控制台编码问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, 'movie data', 'ml-32m', 'ratings.csv')

DB_CONFIG = {
    'host': '192.168.1.38',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}

# 预生成的 bcryptjs 哈希，对应密码 "123test"
# 使用后端 bcryptjs 生成: bcrypt.hashSync('123test', 10)
DEFAULT_PASSWORD_HASH = '$2b$10$J/NVJL/Y14G5OISjosvI6e65Q38QFciIib.ls5ayMVeQWlHNzmHxC'


def process_and_import_ratings(csv_path, db_config, chunk_size=100000):
    print("=" * 60)
    print("ratings.csv 导入任务开始")
    print(f"   文件路径: {csv_path}")
    print(f"   每块处理: {chunk_size} 条")
    print("=" * 60)

    # 先扫描 CSV 获取总行数和唯一用户数用于进度参考
    print("\n1. 正在扫描 CSV 文件基本信息...")
    total_lines = sum(1 for _ in open(csv_path, 'r')) - 1  # 减去 header
    print(f"   总评分记录数: {total_lines:,} 条")

    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()
    print("   ✅ 数据库连接成功")

    start_time = time.time()
    chunk_count = 0
    total_processed = 0
    total_users_inserted = 0
    total_behaviors_inserted = 0

    try:
        for chunk in pd.read_csv(csv_path, chunksize=chunk_size):
            chunk_count += 1
            total_processed += len(chunk)
            elapsed = time.time() - start_time
            pct = total_processed / total_lines * 100
            print(f"\n--- 正在处理第 {chunk_count} 块 ({total_processed:,}/{total_lines:,}, {pct:.1f}%, 已耗时 {elapsed:.0f}s) ---")

            # ==========================================
            # 步骤 A: 提取并插入基础用户 (users 表)
            # ==========================================
            unique_users = chunk['userId'].unique().tolist()
            user_data = []
            for uid in unique_users:
                user_data.append((
                    int(uid),                       # id (与 CSV userId 一致)
                    f"ml_user_{uid}",               # username
                    None,                            # email (NULL)
                    DEFAULT_PASSWORD_HASH,           # password_hash
                    2                                # role_id (普通用户)
                ))

            user_insert_query = """
            INSERT IGNORE INTO users (id, username, email, password_hash, role_id)
            VALUES (%s, %s, %s, %s, %s)
            """
            cursor.executemany(user_insert_query, user_data)
            conn.commit()
            inserted = cursor.rowcount
            total_users_inserted += inserted
            print(f"   ✅ users 表: 本块 {len(unique_users)} 个用户, 新增 {inserted} 个 (累计 {total_users_inserted:,} 个)")

            # ==========================================
            # 步骤 B: 生成 request_id 和时间戳
            # ==========================================
            # request_id = MD5(userId + movieId + timestamp) 全局唯一
            str_concat = (
                chunk['userId'].astype(str)
                + chunk['movieId'].astype(str)
                + chunk['timestamp'].astype(str)
            )
            chunk['request_id'] = str_concat.apply(
                lambda x: hashlib.md5(x.encode('utf-8')).hexdigest()
            )

            # 将 Unix 时间戳转换为 MySQL datetime
            chunk['created_at'] = pd.to_datetime(
                chunk['timestamp'], unit='s'
            ).dt.strftime('%Y-%m-%d %H:%M:%S')

            # 过滤掉 rating 为空的行
            valid_chunk = chunk.dropna(subset=['rating'])

            # ==========================================
            # 步骤 C: 插入用户行为流水 (users_movies_behaviors)
            # ==========================================
            behavior_data = []
            for _, row in valid_chunk.iterrows():
                behavior_data.append((
                    int(row['userId']),
                    int(row['movieId']),
                    'rate',
                    float(row['rating']),
                    row['request_id'],
                    row['created_at']
                ))

            # 如果本块中没有有效的行为数据，则跳过插入
            if behavior_data:
                behavior_insert_query = """
                INSERT IGNORE INTO users_movies_behaviors 
                (user_id, movie_id, behavior_type, rating, request_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                cursor.executemany(behavior_insert_query, behavior_data)
                conn.commit()
                inserted_behaviors = cursor.rowcount
                total_behaviors_inserted += inserted_behaviors
                print(f"   ✅ users_movies_behaviors 表: 本块插入 {inserted_behaviors} 条 (累计 {total_behaviors_inserted:,} 条)")
            else:
                print(f"   ⚠️  本块无有效评分数据，跳过行为插入")

        # ==========================================
        # 步骤 D: 全量聚合更新电影平均分
        # ==========================================
        print(f"\n{'=' * 60}")
        print("2. 所有评分数据写入完毕。正在更新所有电影的平均评分...")
        agg_start = time.time()

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
        agg_elapsed = time.time() - agg_start
        updated_movies = cursor.rowcount
        print(f"   ✅ 电影平均评分更新完成！更新了 {updated_movies} 部电影 (耗时 {agg_elapsed:.2f} 秒)")

        # ==========================================
        # 最终统计
        # ==========================================
        cursor.execute("SELECT COUNT(*) FROM users")
        final_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users_movies_behaviors")
        final_behaviors = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users_movies_behaviors WHERE behavior_type = 'rate'")
        final_ratings = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM movies WHERE avg_rating > 0")
        movies_with_rating = cursor.fetchone()[0]

        total_elapsed = time.time() - start_time
        print(f"\n{'=' * 60}")
        print("🎉 全部 ratings.csv 数据处理并入库完成！")
        print(f"⏱️  总耗时: {total_elapsed:.2f} 秒")
        print("=" * 60)
        print(f"📊 最终数据库统计:")
        print(f"   users 表:                 {final_users:,} 条")
        print(f"   users_movies_behaviors 表: {final_behaviors:,} 条")
        print(f"   其中评分行为:               {final_ratings:,} 条")
        print(f"   有评分的电影数:             {movies_with_rating:,} 部")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 处理过程中发生错误: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
        print("\n   ✅ 数据库连接已关闭")


if __name__ == "__main__":
    if not os.path.exists(CSV_PATH):
        print(f"❌ 错误: CSV 文件不存在: {CSV_PATH}")
        print("   请确保 ratings.csv 位于 'movie data/ml-32m/' 目录下")
        exit(1)

    process_and_import_ratings(CSV_PATH, DB_CONFIG, chunk_size=100000)