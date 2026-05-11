"""
从 MovieRecommendSystem 数据库中全量导出数据：
 - 所有有评分行为的用户
 - 这些用户评过的所有电影
 - 所有评分记录
 - 所有评论记录

输出文件（到 scripts/extract_test_subset_test/ 目录）：
  - test_users.csv        - 用户信息
  - test_movies.csv       - 电影信息
  - test_ratings.csv      - 评分数据（供 train_recommend.py 使用）
  - test_comments.csv     - 评论数据
"""

import mysql.connector
import pandas as pd
import random
import os
import argparse
import numpy as np
import itertools

# --------------------------------
# 数据库配置
# --------------------------------
DB_CONFIG = {
    'host': '192.168.1.38',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}

# 输出目录（与 train_recommend.py 中的 DATA_DIR 保持一致）
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extract_test_subset_test')

# 分批查询参数：每批处理的记录数
CHUNK_SIZE = 1000


# --------------------------------
# 辅助函数
# --------------------------------
def ensure_dir(directory):
    os.makedirs(directory, exist_ok=True)
    print(f"  ✅ 输出目录: {directory}")


def chunks(lst, n):
    """将列表 lst 分成每批 n 个元素"""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def pick_active_users(cursor, target_count=None):
    """选取活跃用户。target_count=None 表示全量选取"""
    if target_count is not None:
        # 有限量选取：一次查询 + fetchall（量不大，不会超时）
        query = """
            SELECT user_id, COUNT(*) AS rating_count
            FROM users_movies_behaviors
            WHERE behavior_type = 'rate'
            GROUP BY user_id
            ORDER BY rating_count DESC
            LIMIT %s
        """
        cursor.execute(query, (target_count * 3,))
        rows = cursor.fetchall()
        print(f"  📊 数据库存在评分行为的用户数: {len(rows)}")

        if len(rows) <= target_count:
            selected = [r[0] for r in rows]
            print(f"  ℹ️  活跃用户不足 {target_count}，全部选取 ({len(selected)} 个)")
        else:
            selected = [r[0] for r in rows]
            selected = selected[:target_count]
            print(f"  ✅ 从 {len(rows)} 个活跃用户中选取前 {target_count} 个最活跃用户")
    else:
        # 全量选取：用 fetchmany 流式读取，避免一次性拉取全部结果
        print(f"  ⏳ 正在全量选取有评分行为的用户（流式读取）...")
        query = """
            SELECT user_id
            FROM users_movies_behaviors
            WHERE behavior_type = 'rate'
            GROUP BY user_id
            ORDER BY user_id
        """
        cursor.execute(query)
        selected = []
        # 每批取 CHUNK_SIZE 行，循环拉取
        while True:
            batch = cursor.fetchmany(CHUNK_SIZE)
            if not batch:
                break
            selected.extend([r[0] for r in batch])
            print(f"  ⏳ 已读取 {len(selected)} 个用户...")
        print(f"  ✅ 全量选取所有有评分行为的用户: {len(selected)} 个")

    return selected


def extract_user_info(cursor, user_ids):
    """从 users 表提取用户基本信息（分批查询）"""
    if not user_ids:
        return pd.DataFrame()

    all_chunks = list(chunks(user_ids, CHUNK_SIZE))
    total_chunks = len(all_chunks)
    dfs = []
    for i, chunk_ids in enumerate(all_chunks):
        fmt_ids = ','.join(['%s'] * len(chunk_ids))
        query = f"""
            SELECT id, username, email, created_at
            FROM users
            WHERE id IN ({fmt_ids})
        """
        cursor.execute(query, chunk_ids)
        rows = cursor.fetchall()
        dfs.append(pd.DataFrame(rows, columns=['user_id', 'username', 'email', 'created_at']))
        if total_chunks > 1:
            print(f"    └── 批次 {i+1}/{total_chunks}: {len(chunk_ids)} 个用户 -> {len(rows)} 条")
    df = pd.concat(dfs, ignore_index=True)
    print(f"  ✅ 提取用户信息: {len(df)} 条 (分批 {total_chunks} 次)")
    return df


def extract_ratings_for_users(cursor, user_ids, output_path):
    """提取指定用户的评分数据（分批查询）"""
    if not user_ids:
        return pd.DataFrame()

    all_chunks = list(chunks(user_ids, CHUNK_SIZE))
    total_chunks = len(all_chunks)
    total_rows = 0
    dfs = []
    for i, chunk_ids in enumerate(all_chunks):
        fmt_ids = ','.join(['%s'] * len(chunk_ids))
        query = f"""
            SELECT 
                umb.user_id,
                umb.movie_id,
                umb.rating,
                umb.created_at
            FROM users_movies_behaviors umb
            WHERE umb.behavior_type = 'rate'
              AND umb.user_id IN ({fmt_ids})
        """
        cursor.execute(query, chunk_ids)
        rows = cursor.fetchall()
        total_rows += len(rows)
        dfs.append(pd.DataFrame(rows, columns=['user_id', 'movie_id', 'rating', 'created_at']))
        if total_chunks > 1:
            print(f"    └── 批次 {i+1}/{total_chunks}: {len(chunk_ids)} 个用户 -> {len(rows)} 条评分 (累计 {total_rows} 条)")
    df = pd.concat(dfs, ignore_index=True)
    print(f"  ✅ 提取评分数据: {len(df)} 条 (分批 {total_chunks} 次)")
    return df


def extract_comments_for_users(cursor, user_ids, output_path):
    """提取指定用户的评论数据（分批查询）"""
    if not user_ids:
        return pd.DataFrame()

    all_chunks = list(chunks(user_ids, CHUNK_SIZE))
    total_chunks = len(all_chunks)
    total_rows = 0
    dfs = []
    for i, chunk_ids in enumerate(all_chunks):
        fmt_ids = ','.join(['%s'] * len(chunk_ids))
        query = f"""
            SELECT 
                c.id,
                c.user_id,
                c.movie_id,
                c.parent_id,
                c.content,
                c.created_at
            FROM comments c
            WHERE c.user_id IN ({fmt_ids})
        """
        cursor.execute(query, chunk_ids)
        rows = cursor.fetchall()
        total_rows += len(rows)
        dfs.append(pd.DataFrame(rows, columns=['comment_id', 'user_id', 'movie_id', 'parent_id', 'content', 'created_at']))
        if total_chunks > 1:
            print(f"    └── 批次 {i+1}/{total_chunks}: {len(chunk_ids)} 个用户 -> {len(rows)} 条评论 (累计 {total_rows} 条)")
    df = pd.concat(dfs, ignore_index=True)
    print(f"  ✅ 提取评论数据: {len(df)} 条 (分批 {total_chunks} 次)")
    return df


def pick_movies(cursor, movie_ids_from_ratings, target_count=None):
    """从所有涉及的影片中选出 target_count 部（按出现频率优先）。target_count=None 表示全量"""
    # 转换为 Python 原生 int，避免 numpy int64 类型不被 MySQL 连接器支持
    movie_ids_native = [int(x) for x in movie_ids_from_ratings]

    if target_count is not None and len(movie_ids_native) > target_count:
        # 按评分出现次数排序（热门优先）
        # 如果 movie_ids_native 太多，也需要分批查询
        if len(movie_ids_native) > CHUNK_SIZE:
            # 分批统计，然后合并计数
            print(f"  ⏳ 电影数量 {len(movie_ids_native)}，分批统计热度...")
            counter = {}
            all_chunks = list(chunks(movie_ids_native, CHUNK_SIZE))
            total_chunks = len(all_chunks)
            for i, chunk_ids in enumerate(all_chunks):
                fmt_ids = ','.join(['%s'] * len(chunk_ids))
                q = f"""
                    SELECT movie_id, COUNT(*) AS cnt
                    FROM users_movies_behaviors
                    WHERE behavior_type = 'rate'
                      AND movie_id IN ({fmt_ids})
                    GROUP BY movie_id
                """
                cursor.execute(q, chunk_ids)
                for row in cursor.fetchall():
                    counter[row[0]] = counter.get(row[0], 0) + row[1]
                print(f"    └── 批次 {i+1}/{total_chunks}: 已统计 {len(counter)} 部电影")
            # 按计数降序取前 target_count 个
            sorted_movies = sorted(counter.items(), key=lambda x: -x[1])
            selected = [movie_id for movie_id, _ in sorted_movies[:target_count]]
            print(f"  ✅ 从 {len(movie_ids_native)} 部涉及电影中选取最热门的 {len(selected)} 部 (分批统计)")
        else:
            fmt_ids = ','.join(['%s'] * len(movie_ids_native))
            query = f"""
                SELECT movie_id, COUNT(*) AS cnt
                FROM users_movies_behaviors
                WHERE behavior_type = 'rate'
                  AND movie_id IN ({fmt_ids})
                GROUP BY movie_id
                ORDER BY cnt DESC
                LIMIT %s
            """
            params = list(movie_ids_native) + [target_count]
            cursor.execute(query, params)
            rows = cursor.fetchall()
            selected = [r[0] for r in rows]
            print(f"  ✅ 从 {len(movie_ids_native)} 部涉及电影中选取最热门的 {len(selected)} 部")
    else:
        selected = list(movie_ids_native)
        print(f"  ✅ 全量保留涉及的所有电影: {len(selected)} 部")

    return selected


def extract_movie_info(cursor, movie_ids):
    """从 movies 表提取电影基本信息（分批查询）"""
    if not movie_ids:
        return pd.DataFrame()

    all_chunks = list(chunks(movie_ids, CHUNK_SIZE))
    total_chunks = len(all_chunks)
    dfs = []
    for i, chunk_ids in enumerate(all_chunks):
        fmt_ids = ','.join(['%s'] * len(chunk_ids))
        query = f"""
            SELECT 
                m.id,
                m.title,
                m.description,
                m.release_year,
                m.duration,
                m.avg_rating,
                m.created_at
            FROM movies m
            WHERE m.id IN ({fmt_ids})
        """
        cursor.execute(query, chunk_ids)
        rows = cursor.fetchall()
        dfs.append(pd.DataFrame(rows, columns=['movie_id', 'title', 'description', 'release_year', 'duration', 'avg_rating', 'created_at']))
        if total_chunks > 1:
            print(f"    └── 批次 {i+1}/{total_chunks}: {len(chunk_ids)} 部电影 -> {len(rows)} 条")
    df = pd.concat(dfs, ignore_index=True)
    print(f"  ✅ 提取电影信息: {len(df)} 条 (分批 {total_chunks} 次)")
    return df


def filter_ratings_by_movies(ratings_df, movie_ids):
    """按 movie_id 过滤评分数据"""
    movie_set = set(movie_ids)
    filtered = ratings_df[ratings_df['movie_id'].isin(movie_set)]
    print(f"  ✅ 过滤后评分数据: {len(filtered)} 条 (原始: {len(ratings_df)} 条)")
    return filtered


def filter_comments_by_movies(comments_df, movie_ids):
    """按 movie_id 过滤评论数据"""
    movie_set = set(movie_ids)
    filtered = comments_df[comments_df['movie_id'].isin(movie_set)]
    print(f"  ✅ 过滤后评论数据: {len(filtered)} 条 (原始: {len(comments_df)} 条)")
    return filtered


def export_csv(df, filepath, description):
    """导出 DataFrame 到 CSV"""
    df.to_csv(filepath, index=False, encoding='utf-8-sig')
    print(f"  📄 导出 {description}: {len(df)} 条 -> {filepath}")


# --------------------------------
# 主流程
# --------------------------------
def main():
    parser = argparse.ArgumentParser(description='提取 MovieRecommendSystem 测试子集')
    parser.add_argument('--users', type=int, default=0, help='目标用户数 (0=全量导出, 默认: 0)')
    parser.add_argument('--movies', type=int, default=0, help='目标电影数 (0=全量导出, 默认: 0)')
    parser.add_argument('--seed', type=int, default=42, help='随机种子 (默认: 42)')
    args = parser.parse_args()

    target_users = args.users if args.users > 0 else None
    target_movies = args.movies if args.movies > 0 else None
    random.seed(args.seed)

    print("=" * 60)
    print("🎬 MovieRecommendSystem 测试子集提取工具")
    print("=" * 60)
    print(f"\n目标: 抽取 {'ALL' if target_users is None else target_users} 个用户 + {'ALL' if target_movies is None else target_movies} 部电影")

    # 确保输出目录
    ensure_dir(OUTPUT_DIR)

    # 连接数据库
    print("\n[1/6] 正在连接数据库...")
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(buffered=True)
    print("  ✅ 数据库连接成功")

    # 第一步：选取活跃用户
    print(f"\n[2/6] 选取 {'ALL' if target_users is None else target_users} 个活跃用户...")
    user_ids = pick_active_users(cursor, target_users)
    if not user_ids:
        print("  ❌ 未找到活跃用户!")
        cursor.close()
        conn.close()
        return

    # 第二步：抽取用户信息
    print(f"\n[3/6] 提取用户信息...")
    users_df = extract_user_info(cursor, user_ids)
    export_csv(users_df, os.path.join(OUTPUT_DIR, 'test_users.csv'), '用户信息')

    # 第三步：抽取评分数据
    print(f"\n[4/6] 提取评分数据...")
    ratings_df = extract_ratings_for_users(cursor, user_ids, os.path.join(OUTPUT_DIR, 'test_ratings.csv'))
    
    # 从评分数据中获取涉及的电影 ID
    all_movie_ids = set(ratings_df['movie_id'].unique())
    print(f"  ℹ️  涉及的电影总数: {len(all_movie_ids)}")

    # 第四步：选取电影
    print(f"\n[5/6] 选取 {'ALL' if target_movies is None else target_movies} 部电影...")
    movie_ids = pick_movies(cursor, all_movie_ids, target_movies)

    # 抽取电影信息
    movies_df = extract_movie_info(cursor, movie_ids)
    export_csv(movies_df, os.path.join(OUTPUT_DIR, 'test_movies.csv'), '电影信息')

    # 按选定的电影过滤评分
    ratings_filtered = filter_ratings_by_movies(ratings_df, movie_ids)
    export_csv(ratings_filtered, os.path.join(OUTPUT_DIR, 'test_ratings.csv'), '评分数据')

    # 第五步：抽取评论数据
    print(f"\n[6/6] 提取评论数据...")
    comments_df = extract_comments_for_users(cursor, user_ids, os.path.join(OUTPUT_DIR, 'test_comments.csv'))
    comments_filtered = filter_comments_by_movies(comments_df, movie_ids)
    export_csv(comments_filtered, os.path.join(OUTPUT_DIR, 'test_comments.csv'), '评论数据')

    # 完成统计
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM movies")
    total_movies = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users_movies_behaviors WHERE behavior_type='rate'")
    total_ratings = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM comments")
    total_comments = cursor.fetchone()[0]

    print("\n" + "=" * 60)
    print("📊 测试子集提取完成!")
    print("=" * 60)
    print(f"  原始数据规模:")
    print(f"    - 用户: {total_users}")
    print(f"    - 电影: {total_movies}")
    print(f"    - 评分: {total_ratings}")
    print(f"    - 评论: {total_comments}")
    print(f"  测试子集规模:")
    print(f"    - 用户: {len(users_df)}")
    print(f"    - 电影: {len(movies_df)}")
    print(f"    - 评分: {len(ratings_filtered)}")
    print(f"    - 评论: {len(comments_filtered)}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print("=" * 60)

    cursor.close()
    conn.close()


if __name__ == '__main__':
    main()