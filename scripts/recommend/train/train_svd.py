#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_svd.py - SVD 矩阵分解训练脚本（内存优化版）

全量数据 20万用户 × 8万电影，峰值内存仅约 8-10 GB。
基于 sklearn TruncatedSVD (randomized) + 稀疏矩阵。
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
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

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


def build_sparse_matrix(train_df):
    """构建稀疏评分矩阵（内存友好）"""
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

    # 用户均值（向量化）
    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts

    # 去均值后的稀疏矩阵
    centered_vals = r_val - user_means[u_idx]
    sparse_R = csr_matrix(
        (centered_vals, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )
    return sparse_R, user_means, user2idx, movie2idx, idx2user, idx2movie, n_users, n_movies


def train_svd(train_df, n_factors=50):
    """
    SVD 训练（稀疏矩阵 + TruncatedSVD，内存友好）
    全量 20万×8万 数据，峰值 ~8-10 GB
    """
    print("\n" + "=" * 60)
    print(f"[SVD 训练] 隐因子数: {n_factors} | sklearn TruncatedSVD(randomized)")

    start_time = time.time()

    sparse_R, user_means, user2idx, movie2idx, idx2user, idx2movie, n_users, n_movies = \
        build_sparse_matrix(train_df)

    print(f"  稀疏矩阵形状: ({n_users}, {n_movies}), "
          f"非零元素: {sparse_R.nnz:,}")

    k = min(n_factors, min(n_users, n_movies) - 1)
    svd = TruncatedSVD(
        n_components=k,
        algorithm='randomized',
        n_iter=5,
        random_state=42,
    )
    user_features = svd.fit_transform(sparse_R)   # (n_users, k)
    movie_features = svd.components_.T            # (n_movies, k)
    explained_variance = float(svd.explained_variance_ratio_.sum())
    print(f"  解释方差比: {explained_variance:.4f}")
    print(f"  用户隐向量: {user_features.shape}, 电影隐向量: {movie_features.shape}")

    # ─── RMSE 计算 ───
    u_idx = np.array([user2idx[uid] for uid in train_df['user_id']], dtype=np.int32)
    m_idx = np.array([movie2idx[mid] for mid in train_df['movie_id']], dtype=np.int32)
    r_val = train_df['rating'].values.astype(np.float32)

    pred = np.sum(user_features[u_idx] * movie_features[m_idx], axis=1) + user_means[u_idx]
    rmse = float(np.sqrt(np.mean((pred - r_val) ** 2)))

    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  SVD 训练耗时: {elapsed:.2f} 秒")

    return {
        'algorithm': 'svd',
        'n_factors': n_factors,
        'user_features': user_features,
        'movie_features': movie_features,
        'user_means': user_means,
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'n_users': n_users,
        'n_movies': n_movies,
        'explained_variance': explained_variance,
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }


def save_model(model, name='svd_model'):
    path = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {path}")
    with open(path, 'wb') as f:
        pickle.dump(model, f)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  模型大小: {size_mb:.2f} MB")

    # 保存元信息 JSON
    meta = {k: v for k, v in model.items() if isinstance(v, (str, int, float, bool, list))}
    meta_path = os.path.join(MODEL_DIR, f'{name}_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
    print(f"  元数据: {meta_path}")
    return path


def main():
    parser = argparse.ArgumentParser(description='SVD 模型训练（内存优化版）')
    parser.add_argument('--n-factors', type=int, default=50, help='隐因子数 (default: 50)')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行线程数')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["OMP_NUM_THREADS"] = str(_N_CPUS)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用线程: {_N_CPUS}")

    overall_start = time.time()

    # 1. 加载数据
    ratings_df = load_data()

    # 2. SVD 训练
    model = train_svd(ratings_df, n_factors=args.n_factors)

    # 3. 保存
    save_model(model, 'svd_model')

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  SVD 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()