#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
save_to_cache.py - 缓存表写入工具（独立脚本）

用于将实时计算的推荐结果批量写回 MySQL 缓存表。
也可以作为模块被 recommend_api.py / recommend.py 调用。

目标表（对应 database/init.sql）:
  - users_recommendations      用户推荐缓存表
    user_id (BIGINT PK), recommend_movies (JSON), algorithm (VARCHAR), updated_at (TIMESTAMP)
  - movies_similarities        电影相似度缓存表
    movie_id (BIGINT PK), similar_movies (JSON), updated_at (TIMESTAMP)

用法:
  python save_to_cache.py --user-id 1 --algorithm hybrid --input recs.json
  python save_to_cache.py --movie-id 1 --input sims.json
  python save_to_cache.py --batch-user users_recs_list.json
"""

import os
import sys
import json
import argparse
from datetime import datetime

try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False

# ---------- MySQL 连接参数 ----------
CACHE_DB_HOST = os.environ.get('CACHE_DB_HOST', '192.168.1.38')
CACHE_DB_USER = os.environ.get('CACHE_DB_USER', 'newuser')
CACHE_DB_PASS = os.environ.get('CACHE_DB_PASS', 'yourpassword')
CACHE_DB_NAME = os.environ.get('CACHE_DB_NAME', 'MovieRecommendSystem')
CACHE_DB_PORT = int(os.environ.get('CACHE_DB_PORT', '3306'))
CACHE_TTL_SECONDS = 60 * 60  # 缓存有效期 1 小时

# ============================================================
# 数据库连接
# ============================================================

def get_db():
    """获取 MySQL 数据库连接"""
    if not HAS_PYMYSQL:
        print("[错误] pymysql 未安装，请执行: pip install pymysql")
        return None
    try:
        conn = pymysql.connect(
            host=CACHE_DB_HOST,
            user=CACHE_DB_USER,
            password=CACHE_DB_PASS,
            database=CACHE_DB_NAME,
            port=CACHE_DB_PORT,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=5
        )
        return conn
    except Exception as e:
        print(f"[错误] MySQL 连接失败: {e}")
        return None


# ============================================================
# 写回 users_recommendations 表
# ============================================================

def save_user_recommendation(user_id, recommendations, algorithm='hybrid'):
    """
    将用户的推荐结果写回 users_recommendations 缓存表。

    参数:
        user_id (int): 用户 ID
        recommendations (list of tuple or list of dict): 
            tuple 格式: [(movie_id, score), ...]
            dict 格式: [{'movie_id': 1, 'score': 4.5}, ...]
        algorithm (str): 算法标识 (svd, hybrid, user_cf, item_cf)
    
    返回:
        bool: 是否成功
    """
    conn = get_db()
    if not conn:
        return False

    try:
        # 统一转换为 list[dict] 格式
        items = []
        for item in recommendations:
            if isinstance(item, (list, tuple)):
                items.append({
                    'movie_id': int(item[0]),
                    'score': round(float(item[1]), 4)
                })
            elif isinstance(item, dict):
                mid = item.get('movie_id') or item.get('movieId')
                score = item.get('score') or item.get('predictedRating') or item.get('predicted_rating')
                if mid and score is not None:
                    items.append({
                        'movie_id': int(mid),
                        'score': round(float(score), 4)
                    })

        recommend_json = json.dumps(items, ensure_ascii=False)
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with conn.cursor() as cursor:
            sql = (
                "REPLACE INTO users_recommendations "
                "(user_id, algorithm, recommend_movies, updated_at) "
                "VALUES (%s, %s, %s, %s)"
            )
            cursor.execute(sql, (user_id, algorithm, recommend_json, current_time))
        conn.commit()

        print(f"[缓存] ✅ 用户 {user_id} 推荐已写回 (算法: {algorithm}, 条目: {len(items)})")
        return True

    except Exception as e:
        print(f"[缓存] ❌ 用户 {user_id} 写回失败: {e}")
        return False
    finally:
        conn.close()


# ============================================================
# 写回 movies_similarities 表
# ============================================================

def save_movie_similarity(movie_id, similar_movies):
    """
    将一部电影的相似电影列表写回 movies_similarities 缓存表。

    参数:
        movie_id (int): 电影 ID
        similar_movies (list of tuple or list of dict):
            tuple 格式: [(similar_movie_id, score), ...]
            dict 格式: [{'movie_id': 123, 'score': 0.85}, ...]
    
    返回:
        bool: 是否成功
    """
    conn = get_db()
    if not conn:
        return False

    try:
        # 统一转换为 list[dict] 格式
        items = []
        for item in similar_movies:
            if isinstance(item, (list, tuple)):
                items.append({
                    'movie_id': int(item[0]),
                    'score': round(float(item[1]), 4)
                })
            elif isinstance(item, dict):
                mid = item.get('movie_id') or item.get('movieId')
                score = item.get('score') or item.get('similarity')
                if mid and score is not None:
                    items.append({
                        'movie_id': int(mid),
                        'score': round(float(score), 4)
                    })

        similar_json = json.dumps(items, ensure_ascii=False)
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with conn.cursor() as cursor:
            sql = (
                "REPLACE INTO movies_similarities "
                "(movie_id, similar_movies, updated_at) "
                "VALUES (%s, %s, %s)"
            )
            cursor.execute(sql, (movie_id, similar_json, current_time))
        conn.commit()

        print(f"[缓存] ✅ 电影 {movie_id} 相似度已写回 (条目: {len(items)})")
        return True

    except Exception as e:
        print(f"[缓存] ❌ 电影 {movie_id} 写回失败: {e}")
        return False
    finally:
        conn.close()


# ============================================================
# 批量导入用户推荐
# ============================================================

def batch_save_user_recommendations(records, algorithm='hybrid'):
    """
    批量将用户推荐结果写回 users_recommendations 表。

    参数:
        records (list): 每项为 {'user_id': int, 'recommendations': list, 'algorithm': str}
        algorithm (str): 默认算法（如果 records 中未指定）
    
    返回:
        int: 成功写入的用户数
    """
    conn = get_db()
    if not conn:
        return 0

    success_count = 0
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with conn.cursor() as cursor:
            sql = (
                "REPLACE INTO users_recommendations "
                "(user_id, algorithm, recommend_movies, updated_at) "
                "VALUES (%s, %s, %s, %s)"
            )

            for record in records:
                user_id = record.get('user_id')
                rec_algo = record.get('algorithm', algorithm)
                recs = record.get('recommendations', [])

                if not user_id or not recs:
                    continue

                items = []
                for item in recs:
                    if isinstance(item, (list, tuple)):
                        items.append({
                            'movie_id': int(item[0]),
                            'score': round(float(item[1]), 4)
                        })
                    elif isinstance(item, dict):
                        mid = item.get('movie_id') or item.get('movieId')
                        score = item.get('score') or item.get('predictedRating') or item.get('predicted_rating')
                        if mid and score is not None:
                            items.append({
                                'movie_id': int(mid),
                                'score': round(float(score), 4)
                            })

                recommend_json = json.dumps(items, ensure_ascii=False)
                cursor.execute(sql, (user_id, rec_algo, recommend_json, current_time))
                success_count += 1

        conn.commit()
        print(f"[缓存] ✅ 批量写回完成: {success_count}/{len(records)} 个用户")

    except Exception as e:
        print(f"[缓存] ❌ 批量写回失败: {e}")
    finally:
        conn.close()

    return success_count


# ============================================================
# 从 JSON 文件读入并写回
# ============================================================

def save_from_json_file(input_file, mode='user', algorithm='hybrid'):
    """
    从 JSON 文件读取数据并写入缓存表。

    用户推荐 JSON 格式:
        {"user_id": 1, "recommendations": [["movie_id", score], ...]}

    或批量格式:
        [{"user_id": 1, "recommendations": [...]}, ...]

    电影相似度 JSON:
        {"movie_id": 1, "similar_movies": [["movie_id", score], ...]}
    """
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if isinstance(data, list):
        # 批量模式
        if mode == 'user':
            ok = batch_save_user_recommendations(data, algorithm)
            return ok > 0
        else:
            print("[错误] 电影相似度暂不支持批量模式")
            return False
    elif isinstance(data, dict):
        if mode == 'user':
            user_id = data.get('user_id')
            recs = data.get('recommendations', [])
            if user_id and recs:
                return save_user_recommendation(user_id, recs, algorithm)
        else:
            movie_id = data.get('movie_id')
            sims = data.get('similar_movies', [])
            if movie_id and sims:
                return save_movie_similarity(movie_id, sims)

    print("[错误] 无法识别的 JSON 格式")
    return False


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='缓存表写入工具 - 将推荐结果写回 MySQL 缓存表'
    )
    parser.add_argument('--user-id', type=int, default=None,
                        help='用户 ID')
    parser.add_argument('--movie-id', type=int, default=None,
                        help='电影 ID')
    parser.add_argument('--algorithm', type=str, default='hybrid',
                        choices=['svd', 'hybrid', 'user_cf', 'item_cf'],
                        help='算法标识 (默认: hybrid)')
    parser.add_argument('--input', type=str, default=None,
                        help='输入 JSON 文件路径')
    parser.add_argument('--stdin', action='store_true',
                        help='从标准输入读取 JSON')
    parser.add_argument('--mode', type=str, default='user',
                        choices=['user', 'movie'],
                        help='写入模式: user(推荐结果)/movie(电影相似度)')
    parser.add_argument('--host', type=str, default=CACHE_DB_HOST,
                        help=f'MySQL 主机地址 (默认: {CACHE_DB_HOST})')
    parser.add_argument('--port', type=int, default=CACHE_DB_PORT,
                        help=f'MySQL 端口 (默认: {CACHE_DB_PORT})')
    parser.add_argument('--user', type=str, default=CACHE_DB_USER,
                        help=f'MySQL 用户名 (默认: {CACHE_DB_USER})')
    parser.add_argument('--password', type=str, default=CACHE_DB_PASS,
                        help='MySQL 密码')
    parser.add_argument('--db', type=str, default=CACHE_DB_NAME,
                        help=f'数据库名 (默认: {CACHE_DB_NAME})')
    args = parser.parse_args()

    # 覆盖全局连接参数
    global CACHE_DB_HOST, CACHE_DB_USER, CACHE_DB_PASS, CACHE_DB_NAME, CACHE_DB_PORT
    if args.host: CACHE_DB_HOST = args.host
    if args.port: CACHE_DB_PORT = args.port
    if args.user: CACHE_DB_USER = args.user
    if args.password and args.password != 'yourpassword': CACHE_DB_PASS = args.password
    if args.db: CACHE_DB_NAME = args.db

    if not HAS_PYMYSQL:
        print("[错误] 需要 pymysql 库: pip install pymysql")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"  缓存表写入工具")
    print(f"{'=' * 60}")
    print(f"  数据库: {CACHE_DB_HOST}:{CACHE_DB_PORT}/{CACHE_DB_NAME}")
    print(f"{'=' * 60}")

    if args.input:
        ok = save_from_json_file(args.input, args.mode, args.algorithm)
    elif args.stdin:
        data = sys.stdin.read()
        input_data = json.loads(data)
        if args.mode == 'user':
            if isinstance(input_data, list):
                ok = batch_save_user_recommendations(input_data, args.algorithm) > 0
            elif isinstance(input_data, dict):
                uid = input_data.get('user_id') or args.user_id
                recs = input_data.get('recommendations', [])
                ok = save_user_recommendation(uid, recs, args.algorithm)
            else:
                ok = False
        else:
            mid = input_data.get('movie_id') or args.movie_id
            sims = input_data.get('similar_movies', [])
            ok = save_movie_similarity(mid, sims)
    elif args.user_id:
        print("[错误] 需要 --input 或 --stdin 来提供推荐数据")
        sys.exit(1)
    elif args.movie_id:
        print("[错误] 需要 --input 或 --stdin 来提供相似度数据")
        sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    if ok:
        print("\n✅ 写入完成")
    else:
        print("\n❌ 写入失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
