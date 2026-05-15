#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_usercf.py - 基于用户的协同过滤训练脚本（极致性能优化版）
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
from sklearn.decomposition import TruncatedSVD
from sklearn.neighbors import NearestNeighbors
from threadpoolctl import threadpool_limits

# ─── CPU 线程控制 ─────────────────────────────────────────────
_N_CPUS = int(os.environ.get("TRAIN_N_JOBS", str(os.cpu_count() or 1)))
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

import warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def train_user_cf(train_df, n_neighbors=30, svd_factors=50):
    print("\n" + "=" * 60)
    print(f"[User-CF 训练] 邻居数: {n_neighbors} | SVD 维度: {svd_factors} | 进程数: {_N_CPUS}")

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df)

    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts
    print(f"  用户均值计算完成")

    centered_vals = r_val - user_means[u_idx]
    sparse_R = csr_matrix(
        (centered_vals, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )
    non_zero = sparse_R.nnz
    density = non_zero / (n_users * n_movies) * 100
    print(f"  稀疏矩阵: ({n_users}, {n_movies}), 非零: {non_zero:,} ({density:.4f}%)")

    k = min(svd_factors, min(n_users, n_movies) - 1)
    svd = TruncatedSVD(n_components=k, algorithm='randomized', n_iter=5, random_state=42)
    user_features = svd.fit_transform(sparse_R)
    explained_var = float(svd.explained_variance_ratio_.sum())
    print(f"  SVD 降维: {user_features.shape}, 解释方差: {explained_var:.4f}")

    nn = NearestNeighbors(
        n_neighbors=min(n_neighbors + 1, n_users),
        metric='cosine',
        algorithm='brute',
        n_jobs=_N_CPUS,
    )
    nn.fit(user_features)
    distances, indices = nn.kneighbors(user_features, return_distance=True)

    neighbor_indices = indices[:, 1:]
    neighbor_dists = distances[:, 1:]

    sim_weights = 1.0 / (1.0 + neighbor_dists)
    sim_weights[neighbor_dists > 0.99] = 0.0
    row_sum = sim_weights.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0] = 1.0
    sim_weights = sim_weights / row_sum
    print(f"  邻居搜索完成: 每个用户 {n_neighbors} 个邻居")

    del user_features, svd, nn, distances, indices
    del centered_vals

    print(f"  计算 RMSE（{_N_CPUS} 进程并行）...")

    user_to_indices = defaultdict(list)
    for i in range(len(train_df)):
        user_to_indices[int(train_df['user_id'].iloc[i])].append(i)

    user_ids_list = list(user_to_indices.keys())
    n_users_total = len(user_ids_list)

    batch_size = max(200, min(2000, n_users_total // max(1, _N_CPUS)))
    batches = [user_ids_list[i:i + batch_size] for i in range(0, n_users_total, batch_size)]
    print(f"  批次数: {len(batches)} (batch_size={batch_size})")

    def _predict_user_batch(batch_uids):
        with threadpool_limits(limits=1, user_api='blas'):
            preds_local = []
            idxs_local = []
            for uid in batch_uids:
                u = user2idx.get(uid)
                if u is None:
                    continue
                sample_indices = user_to_indices[uid]
                if not sample_indices:
                    continue

                movie_cols = m_idx[sample_indices]
                nb_rows = neighbor_indices[u]
                weights = sim_weights[u]

                neighbor_rows = sparse_R[nb_rows]
                neighbor_csc = neighbor_rows.tocsc()
                sub_R = neighbor_csc[:, movie_cols]

                # 关键修复：用 .T.dot(weights) 确保返回稠密数组
                pred_centered = np.asarray(sub_R.T.dot(weights))
                pred = pred_centered + user_means[u]

                preds_local.extend(pred.tolist())
                idxs_local.extend(sample_indices)

            return (idxs_local, preds_local)

    from joblib import Parallel, delayed
    results = Parallel(n_jobs=_N_CPUS, prefer='processes', verbose=0)(
        delayed(_predict_user_batch)(batch) for batch in batches
    )

    pred_values = np.zeros(len(train_df), dtype=np.float32)
    for idxs_local, preds_local in results:
        for idx, val in zip(idxs_local, preds_local):
            pred_values[idx] = val

    rmse = float(np.sqrt(np.mean((pred_values - r_val) ** 2)))
    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  User-CF 训练耗时: {elapsed:.2f} 秒")

    user_neighbors = {}
    for i in range(n_users):
        uid = int(all_users[i])
        nb_list = [(int(all_users[neighbor_indices[i, j]]), float(sim_weights[i, j]))
                   for j in range(n_neighbors)]
        user_neighbors[uid] = nb_list

    user_movies = defaultdict(set)
    for uid_val, mid_val in zip(train_df['user_id'], train_df['movie_id']):
        user_movies[int(uid_val)].add(int(mid_val))

    return {
        'algorithm': 'user_cf',
        'n_neighbors': n_neighbors,
        'svd_factors': svd_factors,
        'user_neighbors': user_neighbors,
        'user_means': {int(uid): float(user_means[i]) for i, uid in enumerate(all_users)},
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'user_movies': {str(k): list(v) for k, v in user_movies.items()},
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }


def save_model(model, name='user_cf_model'):
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
    parser = argparse.ArgumentParser(description='User-CF 模型训练（性能优化版）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数 (default: 30)')
    parser.add_argument('--svd-factors', type=int, default=50, help='SVD 降维数 (default: 50)')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行进程数')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用进程数: {_N_CPUS}")

    overall_start = time.time()
    ratings_df = load_data()

    model = train_user_cf(
        ratings_df,
        n_neighbors=args.n_neighbors,
        svd_factors=args.svd_factors,
    )
    save_model(model, 'user_cf_model')

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  User-CF 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()