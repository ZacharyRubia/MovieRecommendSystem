#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_recommend.py - 推荐算法训练主入口（内存优化版 v5）

全量数据（20万用户 × 8万电影）在 64核128G 机器上可稳定运行。

相比 v4 的关键优化：
  1. Item-CF: 移除 62GB 密集电影-用户矩阵，改用 CSR 稀疏矩阵
  2. Item-CF: 分块计算相似度（chunk_size=2000），峰值仅 ~2-3 GB
  3. User-CF: 移除 320GB 密集用户-用户相似度矩阵，改用 sparse SVD + 索引查找
  4. User-CF: 移除 62GB 密集评分矩阵 R_mat
  5. 所有算法均采用稀疏数据结构，峰值内存控制在 12-16 GB 以内
  6. 训练好的模型格式兼容，recommend.py 已适配

用法:
  python train_recommend.py                          # 完整训练+评估+导出
  python train_recommend.py --skip-eval              # 训练+导出（跳过评估）
  python train_recommend.py --export-only            # 仅从已有模型导出缓存
"""

import os
import sys
import pickle
import json
import time
import csv
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime

import numpy as np

# ──────────────────────── CPU 核心数自动检测 ────────────────────────
_N_CPUS_AVAILABLE = os.cpu_count() or 1
_N_CPUS = int(os.environ.get("TRAIN_N_JOBS", str(min(_N_CPUS_AVAILABLE, 64))))
os.environ["OMP_NUM_THREADS"]       = str(_N_CPUS)
os.environ["MKL_NUM_THREADS"]       = str(_N_CPUS)
os.environ["OPENBLAS_NUM_THREADS"]  = str(_N_CPUS)
os.environ["NUMEXPR_NUM_THREADS"]   = str(_N_CPUS)
os.environ["VECLIB_MAXIMUM_THREADS"]= str(_N_CPUS)
os.environ["MKL_DYNAMIC"]           = "FALSE"

import pandas as pd

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
EXPORT_DIR = os.path.join(BASE_DIR, 'export')

os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# ──────── 导入优化版训练模块（内存友好） ────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train.train_svd import train_svd as train_svd_optimized
from train.train_usercf import train_user_cf as train_user_cf_optimized
from train.train_itemcf import train_item_cf as train_item_cf_optimized


print(f"[系统] CPU 可用核心: {_N_CPUS_AVAILABLE}  |  使用线程数: {_N_CPUS}  |  "
      f"内存优化版 v5")


# ============================================================
# 1. 数据加载与预处理
# ============================================================

def load_data():
    """加载评分数据和电影信息"""
    print("=" * 60)
    print("[加载数据] 读取评分数据和电影信息...")

    ratings_df = pd.read_csv(
        os.path.join(DATA_DIR, 'test_ratings.csv'),
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )
    print(f"  评分数据: {len(ratings_df)} 条, "
          f"用户 {ratings_df['user_id'].nunique()} 个, "
          f"电影 {ratings_df['movie_id'].nunique()} 部")

    movies_df = pd.read_csv(os.path.join(DATA_DIR, 'test_movies.csv'))
    print(f"  电影信息: {len(movies_df)} 部电影")

    unique_users = np.sort(ratings_df['user_id'].unique())
    unique_movies = np.sort(ratings_df['movie_id'].unique())

    user2idx = {int(uid): i for i, uid in enumerate(unique_users)}
    movie2idx = {int(mid): i for i, mid in enumerate(unique_movies)}
    idx2user = {i: int(uid) for uid, i in user2idx.items()}
    idx2movie = {i: int(mid) for mid, i in movie2idx.items()}

    print(f"  用户映射: {len(user2idx)} 个, 电影映射: {len(movie2idx)} 个")
    return ratings_df, movies_df, user2idx, movie2idx, idx2user, idx2movie


def train_test_split(ratings_df, test_ratio=0.2, random_state=42):
    """按用户划分训练/测试集"""
    print(f"\n[数据划分] 测试集比例: {test_ratio}")
    rng = np.random.default_rng(random_state)

    groups = ratings_df.groupby('user_id')
    train_indices = np.empty(len(ratings_df), dtype=bool)

    for _, group in groups:
        n = len(group)
        n_test = max(1, min(int(n * test_ratio), n - 1))
        mask = np.zeros(n, dtype=bool)
        mask[rng.choice(n, size=n_test, replace=False)] = True
        train_indices[group.index] = ~mask

    train_df = ratings_df.loc[train_indices].reset_index(drop=True)
    test_df = ratings_df.loc[~train_indices].reset_index(drop=True)

    print(f"  训练集: {len(train_df)} 条  |  测试集: {len(test_df)} 条  |  "
          f"用户: {train_df['user_id'].nunique()} / {test_df['user_id'].nunique()}")
    return train_df, test_df


# ============================================================
# 2. 训练分派（调用优化模块）
# ============================================================

def train_svd(train_df, n_factors=50, test_df=None):
    """SVD 训练 → 调用优化模块"""
    return train_svd_optimized(train_df, n_factors=n_factors, test_df=test_df)


def train_user_cf(train_df, n_neighbors=30, test_df=None):
    """User-CF 训练（内存优化版）→ 调用优化模块"""
    return train_user_cf_optimized(train_df, n_neighbors=n_neighbors, test_df=test_df)


def train_item_cf(train_df, n_neighbors=30, test_df=None, chunk_size=2000):
    """Item-CF 训练（内存优化版）→ 调用优化模块"""
    return train_item_cf_optimized(
        train_df, n_neighbors=n_neighbors, chunk_size=chunk_size,
    )


# ============================================================
# 3. 模型保存
# ============================================================

def save_model(model, name):
    filepath = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {name} -> {filepath}")
    with open(filepath, 'wb') as f:
        pickle.dump(model, f)
    print(f"  模型大小: {os.path.getsize(filepath) / (1024 * 1024):.2f} MB")


def save_metadata(models_info, train_df, test_df):
    metadata = {
        'train_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'dataset': {
            'train_size': len(train_df),
            'test_size': len(test_df) if test_df is not None else 0,
            'n_users': int(train_df['user_id'].nunique()),
            'n_movies': int(train_df['movie_id'].nunique()),
            'rating_mean': float(train_df['rating'].mean()),
            'rating_std': float(train_df['rating'].std()),
        },
        'models': models_info,
        'system': {
            'n_cpus': _N_CPUS,
        },
    }
    filepath = os.path.join(MODEL_DIR, 'metadata.json')
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"\n[元数据] 已保存 -> {filepath}")


# ============================================================
# 4. 缓存导出（多进程并行）
# ============================================================

def _export_users_batch(uids, user2idx, user_features, movie_vectors,
                        movie_ids, user_means, top_n, user_rated_movies):
    """处理一批用户的推荐计算（全 numpy 向量化）"""
    batch_size = len(uids)
    n_movies = len(movie_ids)

    u_idx = np.array([user2idx[uid] for uid in uids], dtype=np.int32)
    mu = user_means[u_idx]

    # 所有电影评分 (batch × n_movies)
    pred_all = user_features[u_idx] @ movie_vectors.T + mu[:, None]

    # 排除已评分
    for b, uid in enumerate(uids):
        rated = user_rated_movies.get(int(uid))
        if rated:
            pred_all[b, [movie_ids.index(mid) for mid in rated if mid in movie_ids]] = -np.inf

    # Top-N 选择
    k = min(top_n, n_movies)
    top_idx = np.argpartition(pred_all, -k, axis=1)[:, -k:]

    results = []
    for b, uid in enumerate(uids):
        indices = top_idx[b]
        scores = pred_all[b, indices]
        order = np.argsort(-scores)
        indices = indices[order]
        scores = scores[order]
        rec_list = [
            {"movie_id": int(movie_ids[idx]), "score": round(float(scores[i]), 4)}
            for i, idx in enumerate(indices)
            if scores[i] > -np.inf
        ][:top_n]
        results.append((int(uid), rec_list))

    return results


def export_users_recommendations_csv(svd_model, item_cf_model=None, top_n=20,
                                     batch_size=None):
    """用户推荐导出（多进程并行）"""
    print("\n" + "=" * 60)
    print("[缓存导出] 用户推荐 -> users_recommendations.csv (多进程并行)")
    print("=" * 60)

    user2idx = svd_model['user2idx']
    movie2idx = svd_model['movie2idx']
    user_features = svd_model['user_features']
    movie_features = svd_model['movie_features']
    user_means = svd_model['user_means']

    n_users = len(user2idx)
    n_movies = len(movie2idx)

    # 用户已评分电影
    user_rated_movies = defaultdict(set)
    if item_cf_model and 'user_movies' in item_cf_model:
        for uid, mids in item_cf_model['user_movies'].items():
            user_rated_movies[int(uid)] = set(int(m) for m in mids)

    movie_ids = [int(mid) for mid in sorted(movie2idx.keys())]
    movie_vectors = np.array([movie_features[movie2idx[mid]] for mid in movie_ids])

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    algorithm_tag = 'svd'
    csv_path = os.path.join(EXPORT_DIR, 'users_recommendations.csv')

    user_ids = sorted(user2idx.keys())
    if batch_size is None:
        batch_size = max(500, min(10000, n_users // (_N_CPUS * 2)))
    batches = [user_ids[i:i + batch_size] for i in range(0, len(user_ids), batch_size)]
    n_batches = len(batches)
    print(f"  用户数: {n_users}  |  电影数: {n_movies}  |  Top-N: {top_n}")
    print(f"  批量: {batch_size} 用户/batch × {n_batches} batches  |  进程数: {_N_CPUS}")

    total_start = time.time()
    all_results = []

    n_workers = min(_N_CPUS, n_batches)
    print(f"  并行进程数: {n_workers}")
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = [
            executor.submit(_export_users_batch, batch,
                            user2idx, user_features, movie_vectors,
                            movie_ids, user_means, top_n, user_rated_movies)
            for batch in batches
        ]

        processed = 0
        errors = 0
        for future in as_completed(futures):
            try:
                batch_results = future.result()
                all_results.extend(batch_results)
                processed += len(batch_results)
            except Exception as e:
                errors += 1
                print(f"  [警告] batch 处理失败: {e}")

        elapsed = time.time() - total_start
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"  进度: {processed}/{n_users} 用户  |  速率: {rate:.0f} 用户/秒")

    all_results.sort(key=lambda x: x[0])

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for uid, rec_list in all_results:
            writer.writerow([uid, json.dumps(rec_list, ensure_ascii=False),
                             algorithm_tag, current_time])

    total_elapsed = time.time() - total_start
    print(f"\n  完成: {processed}/{n_users} 用户  |  耗时: {total_elapsed:.2f} 秒")
    print(f"  输出: {csv_path} ({os.path.getsize(csv_path) / (1024 * 1024):.2f} MB)")
    return csv_path


def _export_movie_similarity_batch(mids, movie_sim_matrix, top_n, current_time):
    """批量处理电影相似度"""
    results = []
    for mid in mids:
        sim_movies = movie_sim_matrix.get(mid, {})
        if not sim_movies:
            continue
        sorted_sims = sorted(sim_movies.items(), key=lambda x: -x[1])[:top_n]
        sim_list = [
            {"movie_id": int(sim_mid), "score": round(float(score), 4)}
            for sim_mid, score in sorted_sims
        ]
        results.append((mid, [int(mid), json.dumps(sim_list, ensure_ascii=False),
                              current_time]))
    return results


def export_movies_similarities_csv(item_cf_model, top_n=20, batch_size=None):
    """电影相似度导出（多进程并行）"""
    print("\n" + "=" * 60)
    print("[缓存导出] 电影相似度 -> movies_similarities.csv (多进程并行)")
    print("=" * 60)

    movie_sim_matrix = item_cf_model.get('movie_sim_matrix', {})
    if not movie_sim_matrix:
        print("[警告] Item-CF 模型中无电影相似度数据")
        return None

    movie_sim_matrix = {
        int(k): {int(sk): float(sv) for sk, sv in v.items()}
        for k, v in movie_sim_matrix.items()
    }

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    movie_ids = sorted(movie_sim_matrix.keys())
    n_movies = len(movie_ids)
    if batch_size is None:
        batch_size = max(500, min(5000, n_movies // (_N_CPUS * 2)))
    batches = [movie_ids[i:i + batch_size] for i in range(0, n_movies, batch_size)]

    print(f"  电影数: {n_movies}  |  Top-N: {top_n}")
    print(f"  批量: {batch_size} 电影/batch × {len(batches)} batches")

    csv_path = os.path.join(EXPORT_DIR, 'movies_similarities.csv')
    total_start = time.time()
    all_results = []

    n_workers = min(_N_CPUS, len(batches))
    print(f"  并行进程数: {n_workers}")
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = [
            executor.submit(_export_movie_similarity_batch, batch,
                            movie_sim_matrix, top_n, current_time)
            for batch in batches
        ]

        processed = 0
        for future in as_completed(futures):
            batch_results = future.result()
            all_results.extend(batch_results)
            processed += len(batch_results)

    all_results.sort(key=lambda x: x[0])
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for _, row in all_results:
            writer.writerow(row)

    total_elapsed = time.time() - total_start
    print(f"\n  完成: {processed}/{n_movies} 电影  |  耗时: {total_elapsed:.2f} 秒")
    print(f"  输出: {csv_path} ({os.path.getsize(csv_path) / (1024 * 1024):.2f} MB)")
    return csv_path


def generate_sql_from_csv(csv_path, table_type):
    """CSV → SQL REPLACE INTO"""
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

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        print(f"  [跳过] 找不到 CSV: {csv_path}")
        return None
    if not rows:
        print(f"  [跳过] CSV 为空")
        return None

    with open(sql_path, 'w', encoding='utf-8') as f_out:
        f_out.write(f"-- 自动生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f_out.write(f"-- 源文件: {os.path.basename(csv_path)}\n\n")

        batch_size = 1000
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            if table_type == 'user':
                f_out.write(
                    f"REPLACE INTO `{table_name}` "
                    f"(`{id_field}`, `{json_field}`, `algorithm`, `updated_at`) VALUES\n"
                )
                vals = []
                for row in batch:
                    escaped_json = row[1].replace("'", "''")
                    vals.append(f"({row[0]}, '{escaped_json}', "
                                f"'{row[2]}', '{row[3]}')")
            else:
                f_out.write(
                    f"REPLACE INTO `{table_name}` "
                    f"(`{id_field}`, `{json_field}`, `updated_at`) VALUES\n"
                )
                vals = []
                for row in batch:
                    escaped_json = row[1].replace("'", "''")
                    updated_at = row[2] if len(row) > 2 else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    vals.append(f"({row[0]}, '{escaped_json}', '{updated_at}')")
            f_out.write(",\n".join(vals) + ";\n\n")

    print(f"  行数: {len(rows)}  |  输出: {sql_path}")
    return sql_path


def export_caches_to_qdrant_json(svd_model, item_cf_model=None, top_n=20, batch_size=None):
    """JSON 导出（多进程并行）"""
    print("\n" + "=" * 60)
    print("[缓存导出] 推荐数据 -> JSON (多进程并行)")
    print("=" * 60)

    user2idx = svd_model['user2idx']
    movie2idx = svd_model['movie2idx']
    user_features = svd_model['user_features']
    movie_features = svd_model['movie_features']
    user_means = svd_model['user_means']

    user_rated_movies = defaultdict(set)
    if item_cf_model and 'user_movies' in item_cf_model:
        for uid, mids in item_cf_model['user_movies'].items():
            user_rated_movies[int(uid)] = set(int(m) for m in mids)

    movie_ids = [int(mid) for mid in sorted(movie2idx.keys())]
    movie_vectors = np.array([movie_features[movie2idx[mid]] for mid in movie_ids])

    user_ids = sorted(user2idx.keys())
    if batch_size is None:
        batch_size = max(500, min(10000, len(user_ids) // (_N_CPUS * 2)))
    batches = [user_ids[i:i + batch_size] for i in range(0, len(user_ids), batch_size)]
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    all_users = []
    n_workers = min(_N_CPUS, len(batches))
    print(f"  并行进程数: {n_workers}")
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = [
            executor.submit(_export_users_batch, batch,
                            user2idx, user_features, movie_vectors,
                            movie_ids, user_means, top_n, user_rated_movies)
            for batch in batches
        ]
        for future in as_completed(futures):
            for uid, rec_list in future.result():
                all_users.append({
                    "user_id": uid,
                    "recommend_movies": rec_list,
                    "algorithm": "svd",
                    "updated_at": now_str,
                })

    user_json_path = os.path.join(EXPORT_DIR, 'users_recommendations.json')
    with open(user_json_path, 'w', encoding='utf-8') as f:
        json.dump({"users": all_users}, f, ensure_ascii=False, indent=1)
    print(f"  用户推荐 JSON: {user_json_path} ({len(all_users)} 个用户)")

    # 电影相似度
    movie_json_path = os.path.join(EXPORT_DIR, 'movies_similarities.json')
    movie_sim_matrix = item_cf_model.get('movie_sim_matrix', {})
    if movie_sim_matrix:
        movie_sim_matrix = {
            int(k): {int(sk): float(sv) for sk, sv in v.items()}
            for k, v in movie_sim_matrix.items()
        }
        mids_list = sorted(movie_sim_matrix.keys())
        sim_batch_size = max(500, min(5000, len(mids_list) // (_N_CPUS * 2)))
        sim_batches = [mids_list[i:i + sim_batch_size] for i in range(0, len(mids_list), sim_batch_size)]

        movie_results = []
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = [
                executor.submit(_export_movie_similarity_batch, batch,
                                movie_sim_matrix, top_n, now_str)
                for batch in sim_batches
            ]
            for future in as_completed(futures):
                for mid, row in future.result():
                    sim_list = json.loads(row[1])
                    movie_results.append({
                        "movie_id": mid,
                        "similar_movies": sim_list,
                        "updated_at": now_str,
                    })

        with open(movie_json_path, 'w', encoding='utf-8') as f:
            json.dump({"movies": movie_results}, f, ensure_ascii=False, indent=1)
        print(f"  电影相似度 JSON: {movie_json_path} ({len(movie_results)} 部电影)")

    return user_json_path, movie_json_path


# ============================================================
# 5. 主流程
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='MovieLens 推荐系统 - 模型训练 (内存优化版 v5)'
    )
    parser.add_argument('--skip-eval', action='store_true',
                        help='skip RMSE evaluation, only train algorithms')
    parser.add_argument('--n-jobs', type=int, default=None,
                        help=f'parallel jobs (default: {_N_CPUS})')
    parser.add_argument('--export-only', action='store_true',
                        help='export cache from existing models only, no retraining')
    parser.add_argument('--top-n', type=int, default=20,
                        help='top-n recommendations per user/movie (default: 20)')
    parser.add_argument('--chunk-size', type=int, default=2000,
                        help='Item-CF chunk size, lower = less memory (default: 2000)')
    return parser.parse_args()


def main():
    args = parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["OMP_NUM_THREADS"] = str(_N_CPUS)
        os.environ["MKL_NUM_THREADS"] = str(_N_CPUS)
        os.environ["OPENBLAS_NUM_THREADS"] = str(_N_CPUS)
        print(f"[系统] 用户指定工作线程数: {_N_CPUS}")

    skip_eval = args.skip_eval
    top_n = args.top_n

    header = f"""
{'=' * 60}
    MovieLens 推荐系统 - 模型训练 (内存优化版 v5)
{'=' * 60}
  CPU 核心: {_N_CPUS_AVAILABLE}  |  使用: {_N_CPUS}
  跳过评估: {'是' if skip_eval else '否'}  |  Top-N: {top_n}
  峰值内存预估: ~12-16 GB（全量 20万用户 × 8万电影）
"""
    print(header)
    overall_start = time.time()

    if args.export_only:
        print("[仅导出模式] 从已有模型加载...")
        with open(os.path.join(MODEL_DIR, 'svd_model.pkl'), 'rb') as f:
            svd_model = pickle.load(f)
        with open(os.path.join(MODEL_DIR, 'item_cf_model.pkl'), 'rb') as f:
            item_cf_model = pickle.load(f)
        print("  模型加载完成")

        csv_path_user = export_users_recommendations_csv(svd_model, item_cf_model, top_n=top_n)
        csv_path_movie = export_movies_similarities_csv(item_cf_model, top_n=top_n)

        sql_path_user = generate_sql_from_csv(csv_path_user, 'user') if csv_path_user else None
        sql_path_movie = generate_sql_from_csv(csv_path_movie, 'movie') if csv_path_movie else None

        export_caches_to_qdrant_json(svd_model, item_cf_model, top_n=top_n)

        total_time = time.time() - overall_start
        print(f"\n{'=' * 60}")
        print(f"  仅导出完成！总耗时: {total_time:.2f} 秒")
        print(f"{'=' * 60}\n")
        return

    # ── 加载 ──
    ratings_df, movies_df, user2idx, movie2idx, idx2user, idx2movie = load_data()

    # ── 划分 ──
    if skip_eval:
        train_df = ratings_df
        test_df = None
        print(f"\n[跳过评估] 使用全部 {len(train_df)} 条数据训练")
    else:
        train_df, test_df = train_test_split(ratings_df, test_ratio=0.2, random_state=42)

    print(f"\n{'-' * 60}\n")

    # ── SVD（峰值内存 ~4-6 GB）──
    print("[开始] SVD 训练...")
    svd_model = train_svd(train_df, n_factors=50, test_df=test_df)
    print(f"  SVD 总耗时: {svd_model['train_time']:.2f} 秒\n{'-' * 60}\n")

    # ── User-CF（峰值内存 ~6-8 GB）──
    print("[开始] User-CF 训练...")
    user_cf_model = train_user_cf(train_df, n_neighbors=30, test_df=test_df)
    print(f"  User-CF 总耗时: {user_cf_model['train_time']:.2f} 秒\n{'-' * 60}\n")

    # ── Item-CF（峰值内存 ~2-3 GB）──
    print("[开始] Item-CF 训练...")
    item_cf_model = train_item_cf(
        train_df, n_neighbors=30, chunk_size=args.chunk_size,
    )
    print(f"  Item-CF 总耗时: {item_cf_model['train_time']:.2f} 秒\n{'-' * 60}\n")

    # ── 保存模型 ──
    save_model(svd_model, 'svd_model')
    save_model(user_cf_model, 'user_cf_model')
    save_model(item_cf_model, 'item_cf_model')

    # ── 元数据 ──
    models_info = [
        {'name': 'svd', 'algorithm': 'svd', 'n_factors': svd_model.get('n_factors'),
         'train_rmse': svd_model.get('rmse') or svd_model.get('train_rmse'),
         'test_rmse': svd_model.get('test_rmse'),
         'train_time': svd_model['train_time'], 'train_size': svd_model['train_size']},
        {'name': 'user_cf', 'algorithm': 'user_cf',
         'n_neighbors': user_cf_model.get('n_neighbors'),
         'train_rmse': user_cf_model.get('rmse') or user_cf_model.get('train_rmse'),
         'test_rmse': user_cf_model.get('test_rmse'),
         'train_time': user_cf_model['train_time'], 'train_size': user_cf_model['train_size']},
        {'name': 'item_cf', 'algorithm': 'item_cf',
         'n_neighbors': item_cf_model.get('n_neighbors'),
         'train_rmse': item_cf_model.get('rmse') or item_cf_model.get('train_rmse'),
         'test_rmse': None,
         'train_time': item_cf_model['train_time'], 'train_size': item_cf_model['train_size']},
    ]
    save_metadata(models_info, train_df, test_df)

    overall_training_time = time.time() - overall_start

    # ── 汇总 ──
    eval_tag = "(跳过评估)" if skip_eval else ""
    print(f"""
{'=' * 60}
                训练完成！{eval_tag}
{'=' * 60}
算法                   训练RMSE       测试RMSE       耗时(秒)
{'-' * 60}
svd                  {svd_model.get('rmse', 0):.4f}       {svd_model.get('test_rmse', 0) or 0:.4f}       {svd_model['train_time']:.1f}
user_cf              {user_cf_model.get('rmse', 0):.4f}       {user_cf_model.get('test_rmse', 0) or 0:.4f}       {user_cf_model['train_time']:.1f}
item_cf              {item_cf_model.get('rmse', 0):.4f}       {item_cf_model.get('test_rmse', 0) or 0:.4f}       {item_cf_model['train_time']:.1f}
{'=' * 60}
模型已保存至: {MODEL_DIR}
""")

    # ── 导出 ──
    print(f"\n{'=' * 60}")
    print(f"  自动导出缓存数据（MySQL/Qdrant 可导入格式）")
    print(f"{'=' * 60}")

    export_start = time.time()

    csv_path_user = export_users_recommendations_csv(svd_model, item_cf_model, top_n=top_n)
    csv_path_movie = export_movies_similarities_csv(item_cf_model, top_n=top_n)

    sql_path_user = generate_sql_from_csv(csv_path_user, 'user') if csv_path_user else None
    sql_path_movie = generate_sql_from_csv(csv_path_movie, 'movie') if csv_path_movie else None

    export_caches_to_qdrant_json(svd_model, item_cf_model, top_n=top_n)

    export_time = time.time() - export_start

    print(f"""
{'=' * 60}
  导入指引
{'=' * 60}

  users_recommendations: CSV={csv_path_user}  SQL={sql_path_user}
  movies_similarities:  CSV={csv_path_movie}  SQL={sql_path_movie}

  MySQL LOAD DATA:
    LOAD DATA LOCAL INFILE '{csv_path_user}' REPLACE INTO TABLE users_recommendations
    FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\\\\n'
    (user_id, recommend_movies, algorithm, updated_at);

    LOAD DATA LOCAL INFILE '{csv_path_movie}' REPLACE INTO TABLE movies_similarities
    FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\\\\n'
    (movie_id, similar_movies, updated_at);

  JSON → save_to_cache.py:
    python scripts/recommend/save_to_cache.py --batch-user {os.path.join(EXPORT_DIR, 'users_recommendations.json').replace(BASE_DIR, 'scripts/..').replace('\\\\', '/')}
    python scripts/recommend/save_to_cache.py --input {os.path.join(EXPORT_DIR, 'movies_similarities.json').replace(BASE_DIR, 'scripts/..').replace('\\\\', '/')} --mode movie
""")

    total_time = time.time() - overall_start
    print(f"{'=' * 60}")
    print(f"  全部完成！总耗时: {total_time:.2f} 秒 "
          f"(训练: {overall_training_time:.2f}s, 导出: {export_time:.2f}s)")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()