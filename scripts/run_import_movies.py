"""
导入 movies.csv 数据到 MovieRecommendSystem 数据库
将数据安全地写入 movies、genres、movies_genres 三张表
使用 PyMySQL（与后端一致）
"""
import sys
import io
import math
import pandas as pd
import pymysql
import os

# 确保标准输出编码为 UTF-8，避免 Windows 控制台编码问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 获取脚本所在目录的上一级（项目根目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, 'movie data', 'ml-32m', 'movies.csv')

DB_CONFIG = {
    'host': '192.168.1.38',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}


def process_and_import_movies(csv_path, db_config):
    print("=" * 60)
    print("1. 正在加载 CSV 文件...")
    print(f"   文件路径: {csv_path}")
    df = pd.read_csv(csv_path)
    total_movies = len(df)
    print(f"   CSV 共 {total_movies} 条电影记录")

    print("\n2. 开始清洗与转换数据...")
    # 提取年份：正则匹配字符串末尾括号内的 4 位数字
    df['release_year'] = df['title'].str.extract(r'\((\d{4})\)$')

    # 清理标题：去掉末尾的年份及其括号
    df['title'] = df['title'].str.replace(r'\s*\(\d{4}\)$', '', regex=True).str.strip()

    # 处理缺失的年份：先统一转为数值类型（float64，NaN 表示缺失）
    df['release_year'] = pd.to_numeric(df['release_year'], errors='coerce')

    # 统计有效数据
    valid_year_count = df['release_year'].notna().sum()
    undef_year_count = total_movies - valid_year_count
    print(f"   成功提取年份的电影: {valid_year_count}/{total_movies} (未提取: {undef_year_count})")

    # 检查数据类型
    sample_years = df['release_year'].iloc[:5].tolist()
    print(f"   年份示例 (前5): {sample_years}")
    print(f"   年份类型示例: {[type(y).__name__ for y in sample_years]}")

    print("\n3. 正在连接 MySQL 数据库...")
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()
    print("   ✅ 数据库连接成功")

    try:
        # ==========================================
        # 步骤 A: 批量插入电影基础数据 (movies 表)
        # ==========================================
        print("\n4. 正在写入 movies 表...")
        movie_insert_query = """
        INSERT IGNORE INTO movies (id, title, release_year)
        VALUES (%s, %s, %s)
        """
        # 确保所有值为 Python 原生类型，NaN 转为 None
        movie_data = []
        for _, row in df[['movieId', 'title', 'release_year']].iterrows():
            mid = int(row['movieId'])
            title = str(row['title'])
            year = row['release_year']
            # 将 float('nan') 显式转为 None，避免 PyMySQL 报错
            if isinstance(year, float) and math.isnan(year):
                year = None
            movie_data.append((mid, title, year))

        cursor.executemany(movie_insert_query, movie_data)
        conn.commit()
        inserted_movies = cursor.rowcount
        print(f"   ✅ 写入 movies 表完成: 插入/忽略 {inserted_movies} 条")
        print(f"   (总 CSV 记录: {len(movie_data)}, 重复/忽略: {len(movie_data) - inserted_movies})")

        # ==========================================
        # 步骤 B: 提取并插入所有不重复的题材 (genres 表)
        # ==========================================
        print("\n5. 正在提取题材并写入 genres 表...")
        # 过滤掉数据集里标记为 '(no genres listed)' 的无用题材
        valid_genres = df[df['genres'] != '(no genres listed)']['genres']
        # 拆分 '|'，打平列表，并用 set 去重
        all_genre_codes = set(valid_genres.str.split('|').explode())
        print(f"   从数据集中提取到 {len(all_genre_codes)} 个不重复的题材代码")

        # 获取已有题材，避免重复插入时报错
        cursor.execute("SELECT code FROM genres")
        existing_codes = {row[0] for row in cursor.fetchall()}

        # 将题材代码写入 genres 表：code = 英文代码，name = 缺省时使用代码作为安全值
        new_genres = [code for code in all_genre_codes if code not in existing_codes]
        if new_genres:
            genre_insert_query = """
            INSERT IGNORE INTO genres (name, code)
            VALUES (%s, %s)
            """
            genre_data = [(code, code) for code in new_genres]
            cursor.executemany(genre_insert_query, genre_data)
            conn.commit()
            inserted_genres = cursor.rowcount
            print(f"   ✅ 写入 genres 表完成: 新增 {inserted_genres} 个题材（已存在 {len(all_genre_codes) - len(new_genres)} 个跳过）")
        else:
            print(f"   ℹ️ 所有题材已存在于 genres 表中，无需新增")

        # ==========================================
        # 步骤 C: 构建并写入电影-题材关联 (movies_genres 表)
        # ==========================================
        print("\n6. 正在构建关联数据并写入 movies_genres 表...")
        # 获取 genre code -> id 映射字典
        cursor.execute("SELECT id, code FROM genres")
        genre_dict = {code: genre_id for genre_id, code in cursor.fetchall()}

        movie_genre_data = []
        no_genre_count = 0
        for _, row in df.iterrows():
            if row['genres'] == '(no genres listed)':
                no_genre_count += 1
                continue

            genres_list = row['genres'].split('|')
            for genre_code in genres_list:
                genre_id = genre_dict.get(genre_code)
                if genre_id:
                    movie_genre_data.append((int(row['movieId']), int(genre_id)))

        print(f"   - 跳过 '(no genres listed)' 电影: {no_genre_count} 部")
        print(f"   - 需建立关联记录: {len(movie_genre_data)} 条")

        movie_genre_insert_query = """
        INSERT IGNORE INTO movies_genres (movie_id, genre_id)
        VALUES (%s, %s)
        """

        # 按 10000 条一波进行分批提交
        batch_size = 10000
        total_inserted = 0

        if movie_genre_data:
            total_batches = (len(movie_genre_data) + batch_size - 1) // batch_size
            for i in range(0, len(movie_genre_data), batch_size):
                batch = movie_genre_data[i:i + batch_size]
                cursor.executemany(movie_genre_insert_query, batch)
                conn.commit()
                total_inserted += len(batch)
                batch_num = i // batch_size + 1
                print(f"   - 批次 {batch_num}/{total_batches}: 已写入 {total_inserted}/{len(movie_genre_data)} 条...")

            print(f"   ✅ 成功建立 {total_inserted} 条电影-题材关联！")
        else:
            print(f"   ℹ️ 没有可建立的电影-题材关联")

        # ==========================================
        # 最终统计汇总
        # ==========================================
        cursor.execute("SELECT COUNT(*) FROM movies")
        final_movie_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM genres")
        final_genre_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM movies_genres")
        final_movie_genre_count = cursor.fetchone()[0]

        print("\n" + "=" * 60)
        print("🎉 全部 movies.csv 数据处理并入库完成！")
        print("=" * 60)
        print(f"📊 最终数据库统计:")
        print(f"   movies 表:       {final_movie_count} 条记录")
        print(f"   genres 表:       {final_genre_count} 条记录")
        print(f"   movies_genres 表: {final_movie_genre_count} 条关联")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 插入过程中发生错误，已回滚: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        print("\n   ✅ 数据库连接已关闭")


if __name__ == "__main__":
    if not os.path.exists(CSV_PATH):
        print(f"❌ 错误: CSV 文件不存在: {CSV_PATH}")
        print("   请确保 movies.csv 位于 'movie data/ml-32m/' 目录下")
        exit(1)

    process_and_import_movies(CSV_PATH, DB_CONFIG)