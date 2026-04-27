import pandas as pd
import mysql.connector
import re
import math

def process_and_import_movies(csv_path, db_config):
    print("1. 正在加载 CSV 文件...")
    # 读取数据
    df = pd.read_csv(csv_path)

    print("2. 开始清洗与转换数据...")
    # 提取年份：正则匹配字符串末尾括号内的 4 位数字
    df['release_year'] = df['title'].str.extract(r'\((\d{4})\)$')

    # 清理标题：去掉末尾的年份及其括号 (例如 "Toy Story (1995)" 变成 "Toy Story")
    df['title'] = df['title'].str.replace(r'\s*\(\d{4}\)$', '', regex=True).str.strip()

    # 处理 Pandas 中的 NaN：如果是缺失的年份，替换为 None，以便在 MySQL 中存为 NULL
    df['release_year'] = df['release_year'].where(pd.notna(df['release_year']), None)

    print("3. 正在连接 MySQL 数据库...")
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    try:
        # ==========================================
        # 步骤 A: 批量插入电影基础数据 (movie 表)
        # ==========================================
        print("4. 正在写入 movie 表...")
        movie_insert_query = """
        INSERT IGNORE INTO movies (id, title, release_year)
        VALUES (%s, %s, %s)
        """
        # 将 DataFrame 的所需列转为元组列表
        movie_data = list(df[['movieId', 'title', 'release_year']].itertuples(index=False, name=None))
        cursor.executemany(movie_insert_query, movie_data)
        conn.commit()
        print(f"   ✅ 成功执行，受影响/忽略的电影记录：{len(movie_data)} 条")

        # ==========================================
        # 步骤 B: 提取并插入所有不重复的标签 (tag 表)
        # ==========================================
        print("5. 正在提取题材并写入 tag 表...")
        # 过滤掉数据集里标记为 '(no genres listed)' 的无用题材
        valid_genres = df[df['genres'] != '(no genres listed)']['genres']
        # 拆分 '|'，打平列表，并用 set 去重
        all_tags = set(valid_genres.str.split('|').explode())

        tag_insert_query = """
        INSERT IGNORE INTO tags (name)
        VALUES (%s)
        """
        tag_data = [(tag,) for tag in all_tags]
        cursor.executemany(tag_insert_query, tag_data)
        conn.commit()
        print(f"   ✅ 成功写入/忽略 {len(tag_data)} 个基础题材标签")

        # ==========================================
        # 步骤 C: 构建并插入电影-标签关联 (movie_tag 表)
        # ==========================================
        print("6. 正在构建关联数据并写入 movie_tag 表...")
        # 因为 tag 是带有自增主键的，我们需要先从数据库取回真实的 name -> id 映射字典
        cursor.execute("SELECT id, name FROM tags")
        tag_dict = {name: tag_id for tag_id, name in cursor.fetchall()}

        movie_tag_data = []
        for index, row in df.iterrows():
            if row['genres'] == '(no genres listed)':
                continue
            
            genres_list = row['genres'].split('|')
            for genre in genres_list:
                tag_id = tag_dict.get(genre)
                if tag_id:
                    movie_tag_data.append((row['movieId'], tag_id))

        movie_tag_insert_query = """
        INSERT IGNORE INTO movies_tags (movie_id, tag_id)
        VALUES (%s, %s)
        """
        
        # 为了防止数据量过大导致 MySQL 连接超时或内存溢出，按 10000 条一波进行分批提交
        batch_size = 10000
        for i in range(0, len(movie_tag_data), batch_size):
            cursor.executemany(movie_tag_insert_query, movie_tag_data[i:i+batch_size])
            conn.commit()
            
        print(f"   ✅ 成功建立 {len(movie_tag_data)} 条电影-标签关联！")

        print("\n🎉 全部 movies.csv 数据处理并入库完成！")

    except Exception as e:
        print(f"\n❌ 插入过程中发生错误，已回滚: {e}")
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
    
    # 确保 movies.csv 与此脚本在同级目录，或填入绝对路径
    process_and_import_movies('movies.csv', DB_CONFIG)