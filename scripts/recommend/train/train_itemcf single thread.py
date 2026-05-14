#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_itemcf.py - 基于物品的协同过滤训练脚本（单线程串行版）

核心设计：
  1. 稀疏矩阵存储电影-用户评分数据
  2. 分块计算电影相似度（顺序执行，无并行）
  3. RMSE 计算顺序执行，避免多进程/多线程开销
  4. 内存可控，适合单机调试
"""

import os
import sys
import json
import time
import pickle
import argparse
import numpy as np
import pandas as pd
from collections import defaultdict
from scipy.sparse import csr_matrix
from threadpoolctl import threadpool_limits

# ─── CPU 控制：全局 BLAS 线程数设为 1，避免内部多线程 ──────────
_N_CPUS = 1   # 改为单线程，你可手工改为 2 以启用少量线程
os.environ["OMP_NUM_THREADS"] = str(_N_CPUS)
os.environ["MKL_NUM_THREADS"] = str(_N_CPUS)
os.environ["OPENBLAS_NUM_THREADS"] = str(_N_CPUS)
os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

import warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)

# ─── 路径 ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
os.makedirs(MODEL_DIR, exist_ok=True)


def load_data():
    print("=" * 60)
    print("[加载数据] 读取评分数据...")
    ratings_df = pd.read_csv(
        os.path.join(DATA_DIR, 'test_ratings.csv'),
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )
    print(f"  评分数据: {len(ratings_df)} 条, "
          f"用户 {ratings_df['user_id'].nunique()} 个, "
          f"电影 {ratings_df['movie_id'].nunique()} 部")
    return ratings_df


def build_mappings(train_df):
    """构建映射和稀疏矩阵"""
    all_users = np.sort(train_df['user_id'].unique())
    all_movies = np.sort(train_df['movie_id'].unique())
    user2idx = {int(uid): i for i, uid in enumerate(all_users)}
    movie2idx = {int(mid): i for i, mid in enumerate(all_movies)}
    idx2user = {i: int(uid) for uid, i in user2idx.items()}
    idx2movie = {i: int(mid) for mid, i in movie2idx.items()}
    n_users = len(all_users)
    n_movies = len(all_movies)

    u_idx = np.array([user2idx[uid] for uid in train_df['user_id']], dtype=np.int32)
    m_idx = np.array([movie2idx[mid] for mid in train_df['movie_id']], dtype=np.int32)
    r_val = train_df['rating'].values.astype(np.float32)

    return (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
            n_users, n_movies, u_idx, m_idx, r_val)


def _adjust_cosine_similarity(sparse_matrix, top_k=30, chunk_size=2000):
    """分块计算 Adjusted Cosine Similarity（顺序执行，无并行）"""
    from sklearn.metrics.pairwise import cosine_similarity
    from scipy.sparse import dia_matrix

    n_movies = sparse_matrix.shape[0]
    n_users = sparse_matrix.shape[1]

    # 计算每行 L2 范数
    norms = np.sqrt(sparse_matrix.multiply(sparse_matrix).sum(axis=1)).A.ravel()
    norms[norms == 0] = 1.0

    inv_norms = dia_matrix((1.0 / norms, [0]), shape=(n_movies, n_movies))
    normalized = inv_norms @ sparse_matrix

    print(f"  归一化矩阵计算完成")

    chunk_ranges = [(i, min(i + chunk_size, n_movies)) for i in range(0, n_movies, chunk_size)]
    n_chunks = len(chunk_ranges)
    print(f"  分块计算: {chunk_size} 电影/块 × {n_chunks} 块 (顺序执行)")

    all_movie_sim = {}
    for c_start, c_end in chunk_ranges:
        chunk = normalized[c_start:c_end]
        # 单线程 BLAS
        with threadpool_limits(limits=1, user_api='blas'):
            sim_chunk = cosine_similarity(chunk, normalized)
        np.maximum(sim_chunk, 0, out=sim_chunk)

        chunk_rows = c_end - c_start
        for local_i in range(chunk_rows):
            global_i = c_start + local_i
            sim_chunk[local_i, global_i] = 0.0

        n_cols = sim_chunk.shape[1]
        actual_k = min(top_k, n_cols - 1)
        if actual_k > 0:
            top_idx = np.argpartition(sim_chunk, -actual_k, axis=1)[:, -actual_k:]
            filtered = np.zeros_like(sim_chunk)
            rows_idx = np.arange(chunk_rows)[:, None]
            filtered[rows_idx, top_idx] = sim_chunk[rows_idx, top_idx]
            filtered[filtered < 0.01] = 0.0

            for local_i in range(chunk_rows):
                global_i = c_start + local_i
                row = filtered[local_i]
                pos = np.where(row > 0)[0]
                if len(pos):
                    all_movie_sim[global_i] = {int(j): float(row[j]) for j in pos}

    print(f"  相似度计算完成: {len(all_movie_sim)} 部电影有相似邻居")
    return all_movie_sim


def train_item_cf(train_df, n_neighbors=30, chunk_size=2000):
    print("\n" + "=" * 60)
    print(f"[Item-CF 训练] 邻居数: {n_neighbors} | 分块大小: {chunk_size} | 模式: 单线程串行")

    start_time = time.time()

    # 1. 映射
    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df)

    # 2. 电影均值
    movie_mean_arr = np.zeros(n_movies, dtype=np.float64)
    movie_count_arr = np.zeros(n_movies, dtype=np.int64)
    np.add.at(movie_mean_arr, m_idx, r_val)
    np.add.at(movie_count_arr, m_idx, 1)
    movie_count_arr[movie_count_arr == 0] = 1
    movie_means = (movie_mean_arr / movie_count_arr).astype(np.float32)
    print(f"  电影均值计算完成")

    # 3. 稀疏电影-用户矩阵（中心化）
    centered_vals = r_val - movie_means[m_idx]
    sorted_order = np.argsort(m_idx)
    movie_user_sparse = csr_matrix(
        (centered_vals[sorted_order], (m_idx[sorted_order], u_idx[sorted_order])),
        shape=(n_movies, n_users),
        dtype=np.float32
    )
    print(f"  稀疏矩阵: ({n_movies}, {n_users}), 非零: {movie_user_sparse.nnz:,}")

    # 4. 相似度计算（顺序分块）
    movie_sim = _adjust_cosine_similarity(
        movie_user_sparse,
        top_k=n_neighbors,
        chunk_size=chunk_size,
    )

    # 5. 准备 RMSE 预测所需的数据结构（向量化邻居表）
    max_neighbors = max(len(nb) for nb in movie_sim.values()) if movie_sim else 0
    sim_nb_idx = np.zeros((n_movies, max_neighbors), dtype=np.int32)
    sim_nb_val = np.zeros((n_movies, max_neighbors), dtype=np.float32)
    sim_nb_cnt = np.zeros(n_movies, dtype=np.int32)
    for mi, neighbors in movie_sim.items():
        nb_list = list(neighbors.items())
        cnt = len(nb_list)
        sim_nb_cnt[mi] = cnt
        for j, (nmi, sim) in enumerate(nb_list):
            sim_nb_idx[mi, j] = nmi
            sim_nb_val[mi, j] = sim

    # 将训练数据转为 numpy 数组
    train_user_ids = train_df['user_id'].values.astype(np.int32)
    train_movie_ids = train_df['movie_id'].values.astype(np.int32)
    train_ratings = r_val

    # ---------- 单线程批量预测（顺序执行所有样本）----------
    total = len(train_df)
    print(f"  RMSE 计算: {total} 条样本，单线程顺序执行")

    # 为了减少内存碎片，可以一次性分配预测数组
    pred_values = np.zeros(total, dtype=np.float32)
    for i in range(total):
        uid = train_user_ids[i]
        mid = train_movie_ids[i]
        ui = user2idx.get(uid)
        mi = movie2idx.get(mid)

        if mi is None:
            pred_values[i] = movie_means[mi] if mi is not None else 3.0
            continue

        nb_cnt = sim_nb_cnt[mi]
        if nb_cnt == 0:
            pred_values[i] = movie_means[mi]
            continue

        nmi_arr = sim_nb_idx[mi, :nb_cnt]
        sim_arr = sim_nb_val[mi, :nb_cnt]

        # 获取邻居评分（向量化但循环较小）
        neighbor_vals = np.array([movie_user_sparse[nmi, ui] for nmi in nmi_arr], dtype=np.float32)
        mask = neighbor_vals != 0
        if not np.any(mask):
            pred_values[i] = movie_means[mi]
            continue

        neighbor_ratings = neighbor_vals[mask] + movie_means[nmi_arr[mask]]
        sim_used = sim_arr[mask]
        numerator = float(np.sum(sim_used * (neighbor_ratings - movie_means[mi])))
        denominator = float(np.sum(np.abs(sim_used)))
        pred_values[i] = (numerator / denominator + movie_means[mi]) if denominator > 0 else movie_means[mi]

    rmse = float(np.sqrt(np.mean((pred_values - train_ratings) ** 2)))
    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  Item-CF 训练耗时: {elapsed:.2f} 秒")

    # 6. 转换为原始 movie_id 的相似度字典
    movie_sim_dict = {}
    for mi, neighbors in movie_sim.items():
        mid = int(all_movies[mi])
        movie_sim_dict[mid] = {int(all_movies[nmi]): float(nsim) for nmi, nsim in neighbors.items()}
    print(f"  最终相似度字典: {len(movie_sim_dict)} 部电影有相似邻居")

    movie_mean_rating = {int(all_movies[i]): float(movie_means[i]) for i in range(n_movies)}
    user_movies = defaultdict(set)
    for uid_val, mid_val in zip(train_df['user_id'], train_df['movie_id']):
        user_movies[int(uid_val)].add(int(mid_val))

    return {
        'algorithm': 'item_cf',
        'n_neighbors': n_neighbors,
        'chunk_size': chunk_size,
        'movie_sim_matrix': movie_sim_dict,
        'movie_mean_rating': movie_mean_rating,
        'user_movies': {str(k): list(v) for k, v in user_movies.items()},
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }


def save_model(model, name='item_cf_model'):
    path = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {path}")
    with open(path, 'wb') as f:
        pickle.dump(model, f)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  模型大小: {size_mb:.2f} MB")

    meta = {k: v for k, v in model.items() if isinstance(v, (str, int, float, bool, list))}
    meta_path = os.path.join(MODEL_DIR, f'{name}_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  元数据: {meta_path}")
    return path


def main():
    parser = argparse.ArgumentParser(description='Item-CF 模型训练（单线程串行版）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数')
    parser.add_argument('--chunk-size', type=int, default=2000, help='分块大小')
    parser.add_argument('--threads', type=int, default=1,
                        help='BLAS 线程数（建议 1 或 2），默认 1')
    args = parser.parse_args()

    # 设置 BLAS 线程数
    threads = args.threads
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["MKL_NUM_THREADS"] = str(threads)
    os.environ["OPENBLAS_NUM_THREADS"] = str(threads)
    print(f"[系统] BLAS 线程数: {threads} | 整体模式: 单进程串行")

    overall_start = time.time()
    ratings_df = load_data()
    model = train_item_cf(ratings_df, n_neighbors=args.n_neighbors, chunk_size=args.chunk_size)
    save_model(model, 'item_cf_model')
    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  Item-CF 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()