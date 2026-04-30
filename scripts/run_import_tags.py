"""
导入 tags.csv 数据到 MovieRecommendSystem 数据库
将用户标签数据写入 tags、movies_tags、users_preferred_tags 表
使用 PyMySQL（与后端一致）
"""
import sys
import io
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
CSV_PATH = os.path.join(BASE_DIR, 'movie data', 'ml-32m', 'tags.csv')

DB_CONFIG = {
    'host': '192.168.1.38',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}


def process_and_import_tags(csv_path, db_config, chunk_size=50000):
    print("=" * 60)
    print("tags.csv 导入任务开始")
    print(f"   文件路径: {csv_path}")
    print(f"   每块处理: {chunk_size} 条")
    print("=" * 60)

    # 先扫描 CSV 获取总行数用于进度参考
    print("\n1. 正在扫描 CSV 文件基本信息...")
    total_lines = sum(1 for _ in open(csv_path, 'r', encoding='utf-8')) - 1  # 减去 header
    print(f"   总标签记录数: {total_lines:,} 条")

    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()
    print("   ✅ 数据库连接成功")

    start_time = time.time()
    chunk_count = 0
    total_processed = 0
    total_tags_inserted = 0
    total_movie_tag_inserted = 0
    total_preferred_inserted = 0

    try:
        for chunk in pd.read_csv(csv_path, encoding='utf-8', chunksize=chunk_size):
            chunk_count += 1
            total_processed += len(chunk)
            elapsed = time.time() - start_time
            pct = total_processed / total_lines * 100
            print(f"\n--- 正在处理第 {chunk_count} 块 ({total_processed:,}/{total_lines:,}, {pct:.1f}%, 已耗时 {elapsed:.0f}s) ---")

            # 丢弃标签为 NaN 的记录，去除标签中的前后空白，丢弃空字符串
            chunk = chunk.dropna(subset=['tag'])
            chunk['tag'] = chunk['tag'].str.strip()
            chunk = chunk[chunk['tag'] != '']

            if chunk.empty:
                print("   ⚠️ 本块无有效标签数据，跳过")
                continue

            # ==========================================
            # 步骤 A: 确保本块涉及的用户存在 users 表
            # ==========================================
            unique_users = chunk['userId'].unique()
            user_data = []
            for uid in unique_users:
                user_data.append((int(uid), f"ml_user_{uid}", None, "bcrypt_dummy_hash_placeholder", 2))

            user_insert_query = """
            INSERT IGNORE INTO users (id, username, email, password_hash, role_id)
            VALUES (%s, %s, %s, %s, %s)
            """
            cursor.executemany(user_insert_query, user_data)
            conn.commit()
            inserted = cursor.rowcount
            print(f"   ✅ users 表: 本块 {len(unique_users)} 个用户, 新增 {inserted} 个")

            # ==========================================
            # 步骤 B: 提取本块中所有唯一的标签并插入 tag 表
            # ==========================================
            unique_tags = chunk['tag'].unique()

            tag_insert_query = """
            INSERT IGNORE INTO tags (name)
            VALUES (%s)
            """
            tag_data = [(tag,) for tag in unique_tags]
            cursor.executemany(tag_insert_query, tag_data)
            conn.commit()
            tags_inserted_this = cursor.rowcount
            total_tags_inserted += tags_inserted_this
            print(f"   ✅ tags 表: 本块新增 {tags_inserted_this} 个标签 (累计 {total_tags_inserted} 个)")

            # ==========================================
            # 步骤 C: 构建并插入 movies_tags 关联
            # ==========================================
            # 从数据库获取最新的 name -> id 映射
            cursor.execute("SELECT id, name FROM tags")
            tag_dict = {name: tag_id for tag_id, name in cursor.fetchall()}

            # 按 movieId 和 tagId 去重
            movie_tag_set = set()
            for row in chunk.itertuples(index=False):
                tag_id = tag_dict.get(row.tag)
                if tag_id is not None:
                    movie_tag_set.add((int(row.movieId), tag_id))

            movie_tag_data = list(movie_tag_set)
            if movie_tag_data:
                movie_tag_insert_query = """
                INSERT IGNORE INTO movies_tags (movie_id, tag_id)
                VALUES (%s, %s)
                """
                # 分批提交，防止数据量过大
                batch_size = 10000
                for i in range(0, len(movie_tag_data), batch_size):
                    cursor.executemany(movie_tag_insert_query, movie_tag_data[i:i + batch_size])
                    conn.commit()

                total_movie_tag_inserted += len(movie_tag_data)
                print(f"   ✅ movies_tags 表: 本块建立 {len(movie_tag_data)} 条关联 (累计 {total_movie_tag_inserted} 条)")

            # ==========================================
            # 步骤 D: 构建并插入 users_preferred_tags 关联
            # ==========================================
            preferred_set = set()
            for row in chunk.itertuples(index=False):
                tag_id = tag_dict.get(row.tag)
                if tag_id is not None:
                    preferred_set.add((int(row.userId), tag_id))

            preferred_data = list(preferred_set)
            if preferred_data:
                preferred_insert_query = """
                INSERT IGNORE INTO users_preferred_tags (user_id, tag_id)
                VALUES (%s, %s)
                """
                batch_size = 10000
                for i in range(0, len(preferred_data), batch_size):
                    cursor.executemany(preferred_insert_query, preferred_data[i:i + batch_size])
                    conn.commit()

                total_preferred_inserted += len(preferred_data)
                print(f"   ✅ users_preferred_tags 表: 本块建立 {len(preferred_data)} 条关联 (累计 {total_preferred_inserted} 条)")

        # ==========================================
        # 最终统计
        # ==========================================
        cursor.execute("SELECT COUNT(*) FROM tags")
        final_tags = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM movies_tags")
        final_movie_tags = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users_preferred_tags")
        final_preferred = cursor.fetchone()[0]

        total_elapsed = time.time() - start_time
        print(f"\n{'=' * 60}")
        print("🎉 全部 tags.csv 数据处理并入库完成！")
        print(f"⏱️  总耗时: {total_elapsed:.2f} 秒")
        print("=" * 60)
        print(f"📊 最终数据库统计:")
        print(f"   tags 表:                  {final_tags:,} 条")
        print(f"   movies_tags 表:           {final_movie_tags:,} 条")
        print(f"   users_preferred_tags 表:  {final_preferred:,} 条")
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
        print("   请确保 tags.csv 位于 'movie data/ml-32m/' 目录下")
        exit(1)

    process_and_import_tags(CSV_PATH, DB_CONFIG, chunk_size=50000)