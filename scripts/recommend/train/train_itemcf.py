#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_itemcf.py - 基于物品的协同过滤训练脚本（极致内存优化版）

核心设计（避免 83K×83K 全量相似度矩阵 51GB）：
  1. 稀疏矩阵存储电影-用户评分数据（不创建密集 62GB 矩阵）
  2. **分块计算**电影相似度：每次只计算 chunk_size 部电影与其他所有电影的相似度
  3. 每块计算后立即应用 top-K，只保留 K 个最大相似度值
  4. 增量聚合到最终 movie_sim_dict

峰值内存: ~5-8 GB（全量 8万电影，chunk_size=2000）
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
from datetime import datetime
from scipy.sparse import csr_matrix, isspmatrix_csr

# ─── CPU 线程控制 ───
_N_CPUS = int(os.environ.get("TRAIN_N_JOBS", str(os.cpu_count() or 1)))
os.environ["OMP_NUM_THREADS"] = str(_N_CPUS)
os.environ["MKL_NUM_THREADS"] = str(_N_CPUS)
os.environ["OPENBLAS_NUM_THREADS"] = str(_N_CPUS)

# ─── 路径 ───
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


def build_sparse_movie_user_matrix(u_idx, m_idx, r_val, n_movies, n_users, min_co_ratings=3):
    """
    构建电影-用户稀疏评分矩阵 (n_movies, n_users) 的 CSR 格式。

    不做密集化，用 csr_matrix 存储。每行是一个电影，每列是一个用户。
    """
    # 原始数据是 (u_idx, m_idx, rating)，需要转置为 (m_idx, u_idx, rating)
    # 电影去均值：每个电影的评分减去该电影的平均分
    movie_sum = np.zeros(n_movies, dtype=np.float64)
    movie_count = np.zeros(n_movies, dtype=np.int64)
    np.add.at(movie_sum, m_idx, r_val)
    np.add.at(movie_count, m_idx, 1)
    movie_count[movie_count == 0] = 1
    movie_means = (movie_sum / movie_count).astype(np.float32)

    centered_vals = r_val - movie_means[m_idx]

    # 构建 CSR: rows=m_idx, cols=u_idx
    # 按电影排序以便每行连续
    sorted_order = np.argsort(m_idx)
    movie_user_sparse = csr_matrix(
        (centered_vals[sorted_order], (m_idx[sorted_order], u_idx[sorted_order])),
        shape=(n_movies, n_users),
        dtype=np.float32
    )

    sparsity = movie_user_sparse.nnz / (n_movies * n_users) * 100
    print(f"  电影-用户稀疏矩阵: ({n_movies}, {n_users}), "
          f"非零: {movie_user_sparse.nnz:,} ({sparsity:.4f}%)")

    return movie_user_sparse, movie_means, movie_count


def _adjust_cosine_similarity(sparse_matrix, top_k=30, chunk_size=2000, min_co_ratings=3):
    """
    分块计算 Adjusted Cosine Similarity，避免全量密集相似度矩阵。

    策略：
    1. 将电影分成 chunk_size 大小的块
    2. 对每个块，计算该块 vs 所有电影的余弦相似度（稀疏矩阵乘法，自动 OMP 多线程）
    3. 对每行应用 top-K，只保留 K 个最大正值
    4. 累积到结果字典
    """
    from sklearn.metrics.pairwise import cosine_similarity

    n_movies = sparse_matrix.shape[0]
    n_users = sparse_matrix.shape[1]

    # 计算每行的 L2 范数
    norms = np.sqrt(sparse_matrix.multiply(sparse_matrix).sum(axis=1)).A.ravel()
    norms[norms == 0] = 1.0

    # 归一化：构建对角矩阵 1/norms
    from scipy.sparse import dia_matrix
    inv_norms = dia_matrix((1.0 / norms, [0]), shape=(n_movies, n_movies))
    normalized = inv_norms @ sparse_matrix  # (n_movies, n_users) CSR

    print(f"  归一化矩阵计算完成")

    # 共同评分计数矩阵（使用稀疏方式）
    # has_rating = (sparse_matrix != 0).astype(np.float32)
    # co_counts = has_rating @ has_rating.T  # 这会生成 (n_movies, n_movies) 密集矩阵！
    # 改用：只在最终相似度大于阈值时再检查共同评分数
    # 通过稀疏乘法: co_counts[i,j] = NNZ of row i AND row j
    # 近似做法：先算相似度，再过滤

    # 分块计算相似度
    chunk_ranges = [(i, min(i + chunk_size, n_movies)) for i in range(0, n_movies, chunk_size)]
    n_chunks = len(chunk_ranges)
    print(f"  分块计算: {chunk_size} 电影/块 × {n_chunks} 块")

    all_movie_sim = {}

    for chunk_idx, (c_start, c_end) in enumerate(chunk_ranges):
        chunk_rows = c_end - c_start

        # 取当前块 (chunk_rows, n_users)
        chunk = normalized[c_start:c_end]

        # 计算块 vs 所有电影的余弦相似度
        # cosine_similarity 用密集矩阵存结果 (chunk_rows, n_movies)
        sim_chunk = cosine_similarity(chunk, normalized)  # (chunk_rows, n_movies)

        # 置负值为 0
        np.maximum(sim_chunk, 0, out=sim_chunk)

        # 对角线（自身）置 0
        for local_i in range(chunk_rows):
            global_i = c_start + local_i
            sim_chunk[local_i, global_i] = 0.0

        # 对每行应用 top-K
        n_cols = sim_chunk.shape[1]
        actual_k = min(top_k, n_cols - 1)

        if actual_k > 0:
            # 使用 argpartition 高效找 top-k
            top_idx = np.argpartition(sim_chunk, -actual_k, axis=1)[:, -actual_k:]

            # 只保留 top-k 的值
            filtered = np.zeros_like(sim_chunk)
            rows_idx = np.arange(chunk_rows)[:, None]
            filtered[rows_idx, top_idx] = sim_chunk[rows_idx, top_idx]

            # 过滤掉正值太小的（< 0.01）
            filtered[filtered < 0.01] = 0.0

            # 转存到字典
            for local_i in range(chunk_rows):
                global_i = c_start + local_i
                row = filtered[local_i]
                pos = np.where(row > 0)[0]
                if len(pos):
                    all_movie_sim[global_i] = {
                        int(j): float(row[j]) for j in pos
                        if row[j] > 0.01
                    }

        # 释放大矩阵
        del sim_chunk, chunk

        if (chunk_idx + 1) % max(1, n_chunks // 10) == 0:
            print(f"    相似度进度: {chunk_idx + 1}/{n_chunks} 块 "
                  f"(电影 {c_start}~{c_end}, 已获取 {len(all_movie_sim)} 部电影的相似邻居)")

    return all_movie_sim


def train_item_cf(train_df, n_neighbors=30, chunk_size=2000, min_co_ratings=3):
    """
    Item-CF 训练（分块内存优化版）

    峰值内存分析（chunk_size=2000）：
    - 归一化稀疏矩阵: ~200-300 MB
    - 单块相似度矩阵: 2000 × 83146 × 8 = 1.33 GB (float64)
    - 累积结果字典: 按需要动态增长
    - 峰值: ~2-3 GB
    """
    print("\n" + "=" * 60)
    print(f"[Item-CF 训练] 邻居数: {n_neighbors} | 分块大小: {chunk_size}")

    start_time = time.time()

    # ─── 1. 构建映射 ───
    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df)

    # ─── 2. 电影均值 ───
    movie_mean_arr = np.zeros(n_movies, dtype=np.float64)
    movie_count_arr = np.zeros(n_movies, dtype=np.int64)
    np.add.at(movie_mean_arr, m_idx, r_val)
    np.add.at(movie_count_arr, m_idx, 1)
    movie_count_arr[movie_count_arr == 0] = 1
    movie_means = (movie_mean_arr / movie_count_arr).astype(np.float32)
    print(f"  电影均值计算完成")

    # ─── 3. 构建稀疏电影-用户矩阵 ───
    centered_vals = r_val - movie_means[m_idx]
    sorted_order = np.argsort(m_idx)
    movie_user_sparse = csr_matrix(
        (centered_vals[sorted_order], (m_idx[sorted_order], u_idx[sorted_order])),
        shape=(n_movies, n_users),
        dtype=np.float32
    )
    print(f"  稀疏矩阵: ({n_movies}, {n_users}), 非零: {movie_user_sparse.nnz:,}")

    # ─── 4. 分块相似度计算 ───
    movie_sim = _adjust_cosine_similarity(
        movie_user_sparse,
        top_k=n_neighbors,
        chunk_size=chunk_size,
    )

    # ─── 5. RMSE 计算（基于稀疏矩阵高效查询邻居评分） ───
    print(f"  计算 RMSE...")

    # 将 movie_sim 的内部索引格式转为 {mi: [(nmi, sim), ...]}
    movie_sim_list = {}
    for mi, neighbors in movie_sim.items():
        movie_sim_list[mi] = [(int(nmi), float(sim)) for nmi, sim in neighbors.items()]

    pred_values = np.zeros(len(train_df), dtype=np.float32)
    processed = 0
    total = len(train_df)
    report_interval = max(1, total // 20)

    for i in range(len(train_df)):
        uid = int(train_df['user_id'].iloc[i])
        mid = int(train_df['movie_id'].iloc[i])
        actual_rating = r_val[i]
        ui = user2idx.get(uid)

        mi = movie2idx.get(mid)
        if mi is None:
            pred_values[i] = movie_means[mi] if mi is not None else 3.0
            continue

        neighbors = movie_sim_list.get(mi, [])
        if not neighbors:
            pred_values[i] = movie_means[mi]
            continue

        # 从稀疏矩阵中查找用户 ui 对各邻居电影的评分
        # 检查稀疏矩阵该位置是否非零来判断用户是否评分过
        numerator = 0.0
        denominator = 0.0
        for nmi, sim in neighbors:
            # 检查用户 ui 对电影 nmi 是否有评分（稀疏矩阵中非零）
            neighbor_centered = movie_user_sparse[nmi, ui]
            if neighbor_centered != 0:  # 非零 = 用户评过这部电影
                neighbor_rating = neighbor_centered + movie_means[nmi]
                numerator += sim * (neighbor_rating - movie_means[mi])
                denominator += abs(sim)

        if denominator > 0:
            pred_centered = numerator / denominator
            pred_values[i] = pred_centered + movie_means[mi]
        else:
            pred_values[i] = movie_means[mi]

        processed += 1
        if processed % report_interval == 0:
            print(f"    RMSE 进度: {processed}/{total}")

    rmse = float(np.sqrt(np.mean((pred_values - r_val) ** 2)))

    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f} (近似值，使用电影均值)")
    print(f"  Item-CF 训练耗时: {elapsed:.2f} 秒")

    # ─── 6. 将内部索引转换为原始 movie_id ───
    movie_sim_dict = {}
    for mi, neighbors in movie_sim.items():
        mid = int(all_movies[mi])
        movie_sim_dict[mid] = {
            int(all_movies[nmi]): float(nsim)
            for nmi, nsim in neighbors.items()
        }
    print(f"  最终相似度字典: {len(movie_sim_dict)} 部电影有相似邻居")

    # ─── 7. 电影均值字典 ───
    movie_mean_rating = {int(all_movies[i]): float(movie_means[i]) for i in range(n_movies)}

    # 用户已评分电影
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

    meta = {k: v for k, v in model.items()
            if isinstance(v, (str, int, float, bool, list))}
    meta_path = os.path.join(MODEL_DIR, f'{name}_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  元数据: {meta_path}")
    return path


def main():
    parser = argparse.ArgumentParser(description='Item-CF 模型训练（内存优化版）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数 (default: 30)')
    parser.add_argument('--chunk-size', type=int, default=2000,
                        help='分块大小，控制峰值内存 (default: 2000)')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行线程数')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["OMP_NUM_THREADS"] = str(_N_CPUS)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用线程: {_N_CPUS}")

    overall_start = time.time()
    ratings_df = load_data()

    model = train_item_cf(
        ratings_df,
        n_neighbors=args.n_neighbors,
        chunk_size=args.chunk_size,
    )
    save_model(model, 'item_cf_model')

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  Item-CF 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()