import pandas as pd
import mysql.connector


def process_and_import_tags(csv_path, db_config, chunk_size=50000):
    print(f"1. 准备处理 tags.csv (采用分块读取，每块 {chunk_size} 条)...")

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

            # 去除标签中的前后空白，并丢弃标签为空的记录
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
                user_data.append((int(uid), f"ml_user_{uid}", "bcrypt_dummy_hash_placeholder", 2))

            user_insert_query = """
            INSERT IGNORE INTO users (id, username, password_hash, role_id)
            VALUES (%s, %s, %s, %s)
            """
            cursor.executemany(user_insert_query, user_data)
            conn.commit()
            print(f"   ✅ 确保本块中 {len(unique_users)} 个用户存在于 users 表")

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
            print(f"   ✅ 成功写入/忽略 {len(tag_data)} 个标签")

            # ==========================================
            # 步骤 C: 构建并插入 movie_tag 关联
            # ==========================================
            # 从数据库获取最新的 name -> id 映射
            cursor.execute("SELECT id, name FROM tags")
            tag_dict = {name: tag_id for tag_id, name in cursor.fetchall()}

            # 按 movieId 和 tagId 去重 (同一用户可能多次添加相同标签)
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

                print(f"   ✅ 成功建立 {len(movie_tag_data)} 条电影-标签关联")

            # ==========================================
            # 步骤 D: 构建并插入 users_preferred_tags 关联
            # (用户给某电影打了某个标签 => 认为该用户偏好该标签)
            # ==========================================
            # 按 (userId, tagId) 去重
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

                print(f"   ✅ 成功建立 {len(preferred_data)} 条用户偏好标签关联")

        print(f"\n🎉 全部 tags.csv 数据处理并入库完成！总共处理 {total_records} 条记录")

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

    # 执行脚本，chunk_size 设为 5 万，可根据内存情况调整
    process_and_import_tags('tags.csv', DB_CONFIG, chunk_size=50000)