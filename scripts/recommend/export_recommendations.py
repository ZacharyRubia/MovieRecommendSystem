#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_recommendations.py - 推荐结果导出脚本

将离线训练的推荐模型（SVD / Item-CF）计算结果导出为 CSV 文件，
配合 MySQL 的 LOAD DATA INFILE 命令实现极速批量导入。

目标表（对应 database/init.sql）:
  - users_recommendations      用户推荐缓存表
    user_id (BIGINT PK), recommend_movies (JSON), algorithm (VARCHAR), updated_at (TIMESTAMP)
  - movies_similarities        电影相似度缓存表
    movie_id (BIGINT PK), similar_movies (JSON), updated_at (TIMESTAMP)

用法:
  python export_recommendations.py                      # 导出所有
  python export_recommendations.py --type user          # 只导出用户推荐
  python export_recommendations.py --type movie         # 只导出电影相似度
  python export_recommendations.py --top_n 30          # 自定义 Top-N
"""

import os
import sys
import csv
import json
import argparse
import pickle
import math
import time
import numpy as np
from collections import defaultdict
from datetime import datetime

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, 'models')
OUTPUT_DIR = os.path.join(BASE_DIR, 'export')

# 确保输出目录存在
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# 模型加载（复用 recommend.py 中的逻辑）
# ============================================================

def load_model(algorithm='svd'):
    """加载训练好的模型"""
    model_map = {
        'svd': 'svd_model.pkl',
        'user_cf': 'user_cf_model.pkl',
        'item_cf': 'item_cf_model.pkl',
    }

    filename = model_map.get(algorithm)
    if not filename:
        raise ValueError(f"未知算法: {algorithm}, 可选: {list(model_map.keys())}")

    filepath = os.path.join(MODEL_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"模型文件不存在: {filepath}\n请先运行 train_recommend.py 训练模型")

    print(f"[加载模型] {algorithm}: {filepath}")
    with open(filepath, 'rb') as f:
        model = pickle.load(f)

    # 恢复 Item-CF 中的键为 int
    if model.get('algorithm') in ('item_cf',):
        model['user_movies'] = {
            int(k) if isinstance(k, str) else k: [
                int(x) if isinstance(x, str) else x for x in v
            ]
            for k, v in model['user_movies'].items()
        }
        model['movie_sim_matrix'] = {
            int(k) if isinstance(k, str) else k: {
                int(mk) if isinstance(mk, str) else mk: mv
                for mk, mv in v.items()
            }
            for k, v in model['movie_sim_matrix'].items()
        }
        model['movie_ratings'] = {
            int(k) if isinstance(k, str) else k: {
                int(mk) if isinstance(mk, str) else mk: mv
                for mk, mv in v.items()
            }
            for k, v in model['movie_ratings'].items()
        }
        model['movie_mean_rating'] = {
            int(k) if isinstance(k, str) else k: v
            for k, v in model['movie_mean_rating'].items()
        }

    print(f"  算法: {model['algorithm']}, "
          f"训练集大小: {model.get('train_size', 'N/A')}")
    return model


# ============================================================
# 用户推荐导出（基于 SVD 模型 → users_recommendations 表）
# ============================================================

def export_users_recommendations(svd_model, item_cf_model=None, top_n=20):
    """
    使用 SVD 模型为所有用户生成 Top-N 推荐并导出到 CSV。

    SVD 模型包含完整的 user2idx 映射，我们可以遍历所有已知用户，
    通过向量点积快速计算出所有电影的预测评分。
    """
    print("\n" + "=" * 60)
    print("[导出] 用户推荐缓存 -> users_recommendations.csv")
    print("=" * 60)

    user2idx = svd_model['user2idx']
    movie2idx = svd_model['movie2idx']
    user_features = svd_model['user_features']
    movie_features = svd_model['movie_features']
    user_means = svd_model['user_means']

    n_users = len(user2idx)
    n_movies = len(movie2idx)

    print(f"  用户数: {n_users}")
    print(f"  电影数: {n_movies}")
    print(f"  Top-N: {top_n}")

    # 如果有 Item-CF 模型，获取用户已评分的电影列表以排除
    user_rated_movies = defaultdict(set)
    if item_cf_model and 'user_movies' in item_cf_model:
        for uid, mids in item_cf_model['user_movies'].items():
            user_rated_movies[uid] = set(int(m) for m in mids)
        print(f"  已加载用户评分记录: {len(user_rated_movies)} 个用户")

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    algorithm_tag = 'svd'  # 算法标识，对应表中 algorithm 字段

    # 预计算所有电影的特征向量
    movie_ids = []
    movie_vectors = []
    for mid, m_idx in movie2idx.items():
        movie_ids.append(int(mid))
        movie_vectors.append(movie_features[m_idx])
    movie_vectors = np.array(movie_vectors)

    csv_path = os.path.join(OUTPUT_DIR, 'users_recommendations.csv')
    start_time_total = time.time()

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)

        processed = 0
        errors = 0
        batch_start = time.time()

        for uid in sorted(user2idx.keys()):
            try:
                u_idx = user2idx[uid]
                user_mean = user_means[u_idx]

                # 向量化计算：所有电影评分 = user_vector · all_movie_vectors + user_mean
                scores = np.dot(user_features[u_idx], movie_vectors.T) + user_mean

                # 排除已评分电影
                rated = user_rated_movies.get(int(uid), set())
                if rated:
                    valid_indices = [
                        i for i, mid in enumerate(movie_ids)
                        if mid not in rated
                    ]
                    if valid_indices:
                        filtered_scores = scores[valid_indices]
                        filtered_mids = [movie_ids[i] for i in valid_indices]
                    else:
                        filtered_scores = scores
                        filtered_mids = movie_ids
                else:
                    filtered_scores = scores
                    filtered_mids = movie_ids

                # 取 Top-N（使用 argpartition 提升性能）
                if len(filtered_scores) > top_n:
                    top_indices = np.argpartition(filtered_scores, -top_n)[-top_n:]
                    top_indices = top_indices[np.argsort(-filtered_scores[top_indices])]
                else:
                    top_indices = np.argsort(-filtered_scores)

                # 构建推荐列表
                rec_list = []
                for idx in top_indices:
                    rec_list.append({
                        "movie_id": int(filtered_mids[idx]),
                        "score": round(float(filtered_scores[idx]), 4)
                    })

                # 序列化为 JSON
                json_str = json.dumps(rec_list, ensure_ascii=False)

                # 写入 CSV: user_id, recommend_movies(JSON), algorithm, updated_at
                writer.writerow([int(uid), json_str, algorithm_tag, current_time])
                processed += 1

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  [警告] 用户 {uid} 处理失败: {e}")

            # 进度报告
            if processed > 0 and processed % 1000 == 0:
                elapsed = time.time() - batch_start
                rate = 1000 / elapsed if elapsed > 0 else 0
                print(f"  进度: {processed}/{n_users} (错误: {errors}, 速率: {rate:.0f} 用户/秒)")
                batch_start = time.time()

    total_elapsed = time.time() - start_time_total
    print(f"\n  完成: {processed}/{n_users} 用户 (错误: {errors})")
    print(f"  耗时: {total_elapsed:.2f} 秒")
    print(f"  输出文件: {csv_path}")
    file_size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    print(f"  文件大小: {file_size_mb:.2f} MB")

    return csv_path


# ============================================================
# 电影相似度导出（基于 Item-CF 模型 → movies_similarities 表）
# ============================================================

def export_movies_similarities(item_cf_model, top_n=20):
    """
    从 Item-CF 模型的 movie_sim_matrix 导出每部电影的 Top-N 相似电影。

    movie_sim_matrix 结构: {movie_id: {similar_movie_id: similarity_score, ...}}
    """
    print("\n" + "=" * 60)
    print("[导出] 电影相似度缓存 -> movies_similarities.csv")
    print("=" * 60)

    movie_sim_matrix = item_cf_model.get('movie_sim_matrix', {})
    if not movie_sim_matrix:
        print("[错误] Item-CF 模型中无电影相似度数据")
        return None

    # 转换 key 为 int
    movie_sim_matrix_int = {}
    for k, v in movie_sim_matrix.items():
        movie_sim_matrix_int[int(k)] = {
            int(sk): float(sv) for sk, sv in v.items()
        }
    movie_sim_matrix = movie_sim_matrix_int

    n_movies = len(movie_sim_matrix)
    print(f"  电影数: {n_movies}")
    print(f"  Top-N: {top_n}")

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    csv_path = os.path.join(OUTPUT_DIR, 'movies_similarities.csv')
    start_time_total = time.time()

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)

        processed = 0
        errors = 0

        for mid in sorted(movie_sim_matrix.keys()):
            try:
                sim_movies = movie_sim_matrix[mid]
                if not sim_movies:
                    continue

                # 按相似度降序排列取 Top-N
                sorted_sims = sorted(
                    sim_movies.items(),
                    key=lambda x: -x[1]
                )[:top_n]

                # 构建相似电影列表
                sim_list = [
                    {"movie_id": int(sim_mid), "score": round(float(score), 4)}
                    for sim_mid, score in sorted_sims
                ]

                # 序列化为 JSON
                json_str = json.dumps(sim_list, ensure_ascii=False)

                # 写入 CSV: movie_id, similar_movies(JSON), updated_at
                writer.writerow([int(mid), json_str, current_time])
                processed += 1

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  [警告] 电影 {mid} 处理失败: {e}")

            if processed > 0 and processed % 5000 == 0:
                print(f"  进度: {processed}/{n_movies}")

    total_elapsed = time.time() - start_time_total
    print(f"\n  完成: {processed}/{n_movies} 电影 (错误: {errors})")
    print(f"  耗时: {total_elapsed:.2f} 秒")
    print(f"  输出文件: {csv_path}")
    file_size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    print(f"  文件大小: {file_size_mb:.2f} MB")

    return csv_path


# ============================================================
# 生成 SQL 文件（备选方案，免配 LOAD DATA 权限）
# ============================================================

def generate_sql_from_csv(csv_path, table_type):
    """
    将已导出的 CSV 文件转换为 SQL REPLACE INTO 脚本。
    作为 LOAD DATA INFILE 的备选方案。
    """
    if table_type == 'user':
        sql_path = csv_path.replace('.csv', '.sql')
        table_name = 'users_recommendations'
        id_field = 'user_id'
        json_field = 'recommend_movies'
    else:
        sql_path = csv_path.replace('.csv', '.sql')
        table_name = 'movies_similarities'
        id_field = 'movie_id'
        json_field = 'similar_movies'

    print(f"\n[生成 SQL] {os.path.basename(sql_path)}")

    batch_size = 500
    total_rows = 0

    with open(csv_path, 'r', encoding='utf-8') as csv_in:
        reader = csv.reader(csv_in)
        rows = list(reader)

    with open(sql_path, 'w', encoding='utf-8') as f_out:
        # 写入文件头注释
        f_out.write(f"-- 自动生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f_out.write(f"-- 源文件: {os.path.basename(csv_path)}\n")
        f_out.write(f"-- 目标表: {table_name}\n\n")

        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]

            if table_type == 'user':
                # users_recommendations 有 4 字段: user_id, recommend_movies, algorithm, updated_at
                f_out.write(
                    f"REPLACE INTO `{table_name}` "
                    f"(`{id_field}`, `{json_field}`, `algorithm`, `updated_at`) VALUES\n"
                )
                values = []
                for row in batch:
                    main_id = row[0]
                    json_str = row[1].replace("'", "''")
                    algorithm = row[2]
                    updated_at = row[3]
                    values.append(f"({main_id}, '{json_str}', '{algorithm}', '{updated_at}')")
            else:
                # movies_similarities 有 3 字段: movie_id, similar_movies, updated_at
                f_out.write(
                    f"REPLACE INTO `{table_name}` "
                    f"(`{id_field}`, `{json_field}`) VALUES\n"
                )
                values = []
                for row in batch:
                    main_id = row[0]
                    json_str = row[1].replace("'", "''")
                    values.append(f"({main_id}, '{json_str}')")

            f_out.write(",\n".join(values) + ";\n\n")
            total_rows += len(batch)

    print(f"  行数: {total_rows}")
    print(f"  输出: {sql_path}")
    file_size_mb = os.path.getsize(sql_path) / (1024 * 1024)
    print(f"  大小: {file_size_mb:.2f} MB")

    return sql_path


# ============================================================
# 打印 MySQL 导入指引
# ============================================================

def print_import_guide(csv_user_path, csv_movie_path):
    """打印 MySQL 导入命令"""
    print("\n" + "=" * 60)
    print("  MySQL 导入指引")
    print("=" * 60)
    print(f"""
  方法一: LOAD DATA INFILE（推荐，性能最高）

  -- 登录 MySQL 后执行:
  LOAD DATA LOCAL INFILE '{csv_user_path.replace('\\', '/') if csv_user_path else '<未生成>'}'
  REPLACE INTO TABLE users_recommendations
  FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\\n'
  (user_id, recommend_movies, algorithm, updated_at);

  LOAD DATA LOCAL INFILE '{csv_movie_path.replace('\\', '/') if csv_movie_path else '<未生成>'}'
  REPLACE INTO TABLE movies_similarities
  FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\\n'
  (movie_id, similar_movies, updated_at);

  注意: 需要在 MySQL 客户端开启 local_infile:
    SET GLOBAL local_infile = 1;

  方法二: 导入 SQL 文件（免配权限）

  mysql -u root -p MovieRecommendSystem < {csv_user_path.replace('.csv', '.sql').replace('\\', '/') if csv_user_path else '<未生成>'}
  mysql -u root -p MovieRecommendSystem < {csv_movie_path.replace('.csv', '.sql').replace('\\', '/') if csv_movie_path else '<未生成>'}
""")


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='推荐结果导出脚本 - 将模型计算结果导出为 CSV/SQL 文件'
    )
    parser.add_argument(
        '--type', '-t',
        choices=['user', 'movie', 'all'],
        default='all',
        help='导出类型: user(用户推荐), movie(电影相似度), all(全部, 默认)'
    )
    parser.add_argument(
        '--top_n', '-n',
        type=int,
        default=20,
        help='每用户/每电影的推荐数量 (默认: 20)'
    )
    parser.add_argument(
        '--sql', '-s',
        action='store_true',
        help='同时生成 SQL 文件（备选方案）'
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  推荐结果导出工具")
    print("=" * 60)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型目录: {MODEL_DIR}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  Top-N: {args.top_n}")
    print(f"  导出类型: {args.type}")
    print("=" * 60)

    start_time_total = time.time()
    csv_user_path = None
    csv_movie_path = None

    try:
        # === 导出用户推荐 ===
        if args.type in ('user', 'all'):
            print("\n[1/2] 加载 SVD 模型...")
            svd_model = load_model('svd')

            item_cf_model = None
            if args.type == 'all':
                print("\n[准备] 加载 Item-CF 模型（用于获取用户评分历史）...")
                try:
                    item_cf_model = load_model('item_cf')
                except FileNotFoundError:
                    print("  [警告] Item-CF 模型不存在，将不排除已评分电影")
                    item_cf_model = None

            csv_user_path = export_users_recommendations(
                svd_model, item_cf_model, top_n=args.top_n
            )

            if csv_user_path and args.sql:
                generate_sql_from_csv(csv_user_path, 'user')

        # === 导出电影相似度 ===
        if args.type in ('movie', 'all'):
            print("\n[2/2] 加载 Item-CF 模型...")
            item_cf_model = load_model('item_cf')
            csv_movie_path = export_movies_similarities(
                item_cf_model, top_n=args.top_n
            )

            if csv_movie_path and args.sql:
                generate_sql_from_csv(csv_movie_path, 'movie')

        total_elapsed = time.time() - start_time_total

        print("\n" + "=" * 60)
        print("  导出完成！")
        print(f"  总耗时: {total_elapsed:.2f} 秒")
        print("=" * 60)

        # 打印导入指引
        print_import_guide(csv_user_path, csv_movie_path)

    except FileNotFoundError as e:
        print(f"\n[错误] {e}")
        print("\n请先运行 train_recommend.py 训练模型。")
        sys.exit(1)
    except Exception as e:
        print(f"\n[错误] 导出失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()