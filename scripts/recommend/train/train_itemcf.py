#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_itemcf.py - 基于物品的协同过滤训练脚本（极致内存优化版 + 多核并行）

核心设计（避免 83K×83K 全量相似度矩阵 51GB）：
  1. 稀疏矩阵存储电影-用户评分数据（不创建密集 62GB 矩阵）
  2. **分块计算**电影相似度：每次只计算 chunk_size 部电影与其他所有电影的相似度
  3. 每块计算后立即应用 top-K，只保留 K 个最大相似度值
  4. 增量聚合到最终 movie_sim_dict
  5. RMSE 计算使用 joblib 多核并行 + 向量化批量查询

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
from functools import partial
from scipy.sparse import csr_matrix, isspmatrix_csr
from threadpoolctl import threadpool_limits

# ─── CPU 线程控制 ───
_N_CPUS = int(os.environ.get("TRAIN_N_JOBS", str(os.cpu_count() or 1)))
os.environ["OMP_NUM_THREADS"] = str(_N_CPUS)
os.environ["MKL_NUM_THREADS"] = str(_N_CPUS)
os.environ["OPENBLAS_NUM_THREADS"] = str(_N_CPUS)
os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)  # joblib 感知

import warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)

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


def _adjust_cosine_similarity(sparse_matrix, top_k=30, chunk_size=2000, min_co_ratings=3):
    """
    分块计算 Adjusted Cosine Similarity，避免全量密集相似度矩阵。

    利用 joblib 并行处理各分块，充分利用多核 CPU。

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

    # 分块计算相似度
    chunk_ranges = [(i, min(i + chunk_size, n_movies)) for i in range(0, n_movies, chunk_size)]
    n_chunks = len(chunk_ranges)
    print(f"  分块计算: {chunk_size} 电影/块 × {n_chunks} 块 (使用 {_N_CPUS} 线程)")

    def _process_chunk(c_start, c_end):
        """处理单个分块：计算余弦相似度并保留 top-K"""
        chunk_rows = c_end - c_start
        chunk = normalized[c_start:c_end]

        # threadpool_limits 限制内层 BLAS 线程数为 1, 避免与 joblib 外层嵌套并行
        with threadpool_limits(limits=1, user_api='blas'):
            sim_chunk = cosine_similarity(chunk, normalized)  # (chunk_rows, n_movies)
        np.maximum(sim_chunk, 0, out=sim_chunk)

        # 对角线（自身）置 0
        for local_i in range(chunk_rows):
            global_i = c_start + local_i
            sim_chunk[local_i, global_i] = 0.0

        # top-K
        n_cols = sim_chunk.shape[1]
        actual_k = min(top_k, n_cols - 1)

        result = {}
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
                    result[global_i] = {
                        int(j): float(row[j]) for j in pos
                    }

        return result

    # 使用 joblib 并行处理各分块
    from joblib import Parallel, delayed
    chunk_results = Parallel(n_jobs=_N_CPUS, prefer='threads', verbose=0)(
        delayed(_process_chunk)(c_start, c_end) for c_start, c_end in chunk_ranges
    )

    # 合并结果
    all_movie_sim = {}
    for cr in chunk_results:
        all_movie_sim.update(cr)

    print(f"  相似度计算完成: {len(all_movie_sim)} 部电影有相似邻居")

    return all_movie_sim


def train_item_cf(train_df, n_neighbors=30, chunk_size=2000, min_co_ratings=3):
    """
    Item-CF 训练（分块内存优化版 + 多核并行 RMSE）

    峰值内存分析（chunk_size=2000）：
    - 归一化稀疏矩阵: ~200-300 MB
    - 单块相似度矩阵: 2000 × 83146 × 8 = 1.33 GB (float64)
    - 累积结果字典: 按需要动态增长
    - 峰值: ~2-3 GB
    """
    print("\n" + "=" * 60)
    print(f"[Item-CF 训练] 邻居数: {n_neighbors} | 分块大小: {chunk_size} | 线程: {_N_CPUS}")

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

    # ─── 4. 分块相似度计算（已并行化） ───
    movie_sim = _adjust_cosine_similarity(
        movie_user_sparse,
        top_k=n_neighbors,
        chunk_size=chunk_size,
    )

    # ─── 5. RMSE 计算（joblib 多核并行） ───
    print(f"  计算 RMSE（{_N_CPUS} 线程并行）...")

    # 将 movie_sim 的内部索引转为 numpy 数组便于向量化查询
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

    def _predict_batch(batch_indices):
        """批量预测一批评分样本"""
        # 限制 BLAS 线程数, 避免嵌套并行导致线程爆炸
        with threadpool_limits(limits=1, user_api='blas'):
            return _predict_batch_impl(batch_indices)

    def _predict_batch_impl(batch_indices):
        """批量预测一批评分样本的实际实现"""
        n_batch = len(batch_indices)
        preds = np.zeros(n_batch, dtype=np.float32)
        for k, i in enumerate(batch_indices):
            uid = int(train_df['user_id'].iloc[i])
            mid = int(train_df['movie_id'].iloc[i])
            ui = user2idx.get(uid)
            mi = movie2idx.get(mid)

            if mi is None:
                preds[k] = movie_means[mi] if mi is not None else 3.0
                continue

            nb_cnt = sim_nb_cnt[mi]
            if nb_cnt == 0:
                preds[k] = movie_means[mi]
                continue

            nmi_arr = sim_nb_idx[mi, :nb_cnt]
            sim_arr = sim_nb_val[mi, :nb_cnt]

            neighbor_vals = np.array([
                movie_user_sparse[nmi, ui] for nmi in nmi_arr
            ], dtype=np.float32)

            mask = neighbor_vals != 0
            if not np.any(mask):
                preds[k] = movie_means[mi]
                continue

            neighbor_ratings = neighbor_vals[mask] + movie_means[nmi_arr[mask]]
            sim_used = sim_arr[mask]
            numerator = float(np.sum(sim_used * (neighbor_ratings - movie_means[mi])))
            denominator = float(np.sum(np.abs(sim_used)))
            preds[k] = (numerator / denominator + movie_means[mi]) if denominator > 0 else movie_means[mi]
        return preds

    # 分块并行
    total = len(train_df)
    n_jobs = _N_CPUS
    chunk_size_rmse = max(1000, total // (n_jobs * 4))
    batches = [list(range(i, min(i + chunk_size_rmse, total)))
               for i in range(0, total, chunk_size_rmse)]

    from joblib import Parallel, delayed
    results = Parallel(n_jobs=n_jobs, prefer='threads', verbose=0)(
        delayed(_predict_batch)(batch) for batch in batches
    )

    pred_values = np.concatenate(results).astype(np.float32)
    rmse = float(np.sqrt(np.mean((pred_values - r_val) ** 2)))
    print(f"  RMSE 计算完成: {total} 条, {n_jobs} 线程并行")

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
    parser = argparse.ArgumentParser(description='Item-CF 模型训练（内存优化版 + 多核并行）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数 (default: 30)')
    parser.add_argument('--chunk-size', type=int, default=2000,
                        help='分块大小，控制峰值内存 (default: 2000)')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行线程数')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["OMP_NUM_THREADS"] = str(_N_CPUS)
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

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