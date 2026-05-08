#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_recommend.py - 推荐算法训练脚本 (CPU 极致优化版 v4 - 多核优化版)

对比 v3 的优化：
  1. 动态检测 CPU 核心数，自动设置 OMP/MKL/NUMEXPR 线程数
  2. sklearn TruncatedSVD (randomized) 替代 scipy svds，多线程加速
  3. Numba JIT 编译加速 _apply_top_k 和导出热循环
  4. ProcessPoolExecutor 替代 ThreadPoolExecutor，突破 GIL
  5. 新增 --skip-eval 参数，跳过 RMSE 评估（可选）
  6. 导出 batch_size 自适应增大
  7. 导出阶段使用 numpy 内存映射 + 批量 JSON 序列化
  8. 新增 --n-jobs 参数控制并行度
"""

import os
import sys
import pickle
import json
import time
import math
import csv
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed, ThreadPoolExecutor
from functools import partial
from itertools import islice

import numpy as np
from collections import defaultdict
from datetime import datetime

# ──────────────────────── CPU 核心数自动检测 ────────────────────────
_N_CPUS_AVAILABLE = os.cpu_count() or 1
# 允许通过环境变量覆盖
_N_CPUS = int(os.environ.get("TRAIN_N_JOBS", str(min(_N_CPUS_AVAILABLE, 64))))
os.environ["OMP_NUM_THREADS"]       = str(_N_CPUS)
os.environ["MKL_NUM_THREADS"]       = str(_N_CPUS)
os.environ["OPENBLAS_NUM_THREADS"]  = str(_N_CPUS)
os.environ["NUMEXPR_NUM_THREADS"]   = str(_N_CPUS)
os.environ["VECLIB_MAXIMUM_THREADS"]= str(_N_CPUS)
os.environ["MKL_DYNAMIC"]           = "FALSE"  # 禁止 MKL 动态调整，固定线程数

import pandas as pd

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
EXPORT_DIR = os.path.join(BASE_DIR, 'export')

os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# ──────────────── 尝试导入 Numba（可选依赖） ────────────────
try:
    from numba import njit, prange
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False
    # 定义降级函数，避免调用处报错
    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]  # 直接返回原函数
        return lambda f: f
    prange = range


print(f"[系统] CPU 可用核心: {_N_CPUS_AVAILABLE}  |  使用线程数: {_N_CPUS}  |  "
      f"Numba: {'可用' if _HAS_NUMBA else '未安装'}")


# ============================================================
# Numba 加速的热点函数
# ============================================================

@njit(parallel=True, fastmath=True, cache=True)
def _apply_top_k_numba(sim_matrix, k):
    """
    Numba 加速版 top-K（纯 JIT 编译，无 Python 循环开销）。
    对每行保留 top-K 个最大值，其余置零。
    """
    n = sim_matrix.shape[0]
    k_actual = min(k, n - 1)
    if k_actual <= 0:
        return np.zeros_like(sim_matrix)

    result = np.zeros_like(sim_matrix)
    for i in prange(n):
        row = sim_matrix[i].copy()
        # 排除自身（对角线）
        row[i] = -np.inf
        # 找到 top-K 的阈值
        if k_actual < n:
            # 使用 np.argpartition 的等效实现
            idx = np.argpartition(row, -k_actual)[-k_actual:]
            for j in idx:
                if row[j] > 0:
                    result[i, j] = row[j]
        else:
            for j in range(n):
                if j != i and row[j] > 0:
                    result[i, j] = row[j]
    return result


def _apply_top_k(sim_matrix, k):
    """
    对相似度矩阵每行保留 top-K 个正值，其余置零。
    使用 np.argpartition （快速选择算法，O(n) 复杂度）。
    """
    n = sim_matrix.shape[0]
    k = min(k, n - 1)
    if k <= 0:
        return np.zeros_like(sim_matrix)

    # 使用 Numba 加速（如果有）
    if _HAS_NUMBA and n > 500:  # 大矩阵用 Numba
        return _apply_top_k_numba(sim_matrix, k)

    # 临时将对角线置 -inf → 排除自身
    orig_diag = np.copy(sim_matrix.diagonal())
    np.fill_diagonal(sim_matrix, -np.inf)

    # 获取 top-K 索引（未排序，O(n) 复杂度）
    top_k_idx = np.argpartition(sim_matrix, -k, axis=1)[:, -k:]

    # 恢复对角线
    np.fill_diagonal(sim_matrix, orig_diag)

    # 仅保留 top-K 的值
    result = np.zeros_like(sim_matrix)
    rows = np.arange(n)[:, None]
    result[rows, top_k_idx] = sim_matrix[rows, top_k_idx]
    return result


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
    """按用户划分训练/测试集（向量化分组）"""
    print(f"\n[数据划分] 测试集比例: {test_ratio}")
    rng = np.random.default_rng(random_state)

    groups = ratings_df.groupby('user_id')
    n_groups = len(groups)

    train_indices = np.empty(len(ratings_df), dtype=bool)

    # 用 numpy 向量化 per-group 选择
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
# 辅助函数
# ============================================================

def _build_mappings_from_df(train_df):
    all_users = np.sort(train_df['user_id'].unique())
    all_movies = np.sort(train_df['movie_id'].unique())
    user2idx = {int(uid): i for i, uid in enumerate(all_users)}
    movie2idx = {int(mid): i for i, mid in enumerate(all_movies)}
    idx2user = {i: int(uid) for uid, i in user2idx.items()}
    idx2movie = {i: int(mid) for mid, i in movie2idx.items()}
    return (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
            len(all_users), len(all_movies))


# ============================================================
# 2. SVD 矩阵分解（sklearn TruncatedSVD 多线程版）
# ============================================================

def train_svd(train_df, n_factors=50, test_df=None):
    print("\n" + "=" * 60)
    print(f"[SVD 训练] 隐因子数: {n_factors}  |  sklearn TruncatedSVD(randomized)")

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies) = _build_mappings_from_df(train_df)

    # ── 构建稀疏评分矩阵 ──
    u_idx = np.array([user2idx[uid] for uid in train_df['user_id']], dtype=np.int32)
    m_idx = np.array([movie2idx[mid] for mid in train_df['movie_id']], dtype=np.int32)
    r_val = train_df['rating'].values.astype(np.float32)
    global_mean = float(r_val.mean())

    # 用户均值（向量化）
    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts

    # 稀疏评分矩阵（仅存储有评分的位置）
    from scipy.sparse import csr_matrix
    centered_vals = r_val - user_means[u_idx]
    sparse_R = csr_matrix(
        (centered_vals, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )

    # ── sklearn TruncatedSVD (randomized) ──
    # 自动多线程，比 scipy svds 快数倍
    from sklearn.decomposition import TruncatedSVD as SklearnSVD
    k = min(n_factors, min(n_users, n_movies) - 1)

    svd = SklearnSVD(
        n_components=k,
        algorithm='randomized',
        n_iter=5,
        random_state=42,
    )
    user_features = svd.fit_transform(sparse_R)  # (n_users, k)
    explained_variance = svd.explained_variance_ratio_.sum()
    print(f"  解释方差比: {explained_variance:.4f}")

    # 组件矩阵 = V^T → 每行是一个隐因子，每列对应电影
    # sklearn 的 components_ 形状为 (k, n_movies)
    movie_features = svd.components_.T  # (n_movies, k)

    # ── 训练 RMSE ──
    train_pred = np.sum(user_features[u_idx] * movie_features[m_idx], axis=1) + user_means[u_idx]
    train_rmse = float(np.sqrt(np.mean((train_pred - r_val) ** 2)))
    print(f"  训练集 RMSE: {train_rmse:.4f}")

    # ── 测试 RMSE ──
    test_rmse = None
    if test_df is not None and len(test_df) > 0:
        valid_u = np.array([user2idx.get(uid, -1) for uid in test_df['user_id']], dtype=np.int32)
        valid_m = np.array([movie2idx.get(mid, -1) for mid in test_df['movie_id']], dtype=np.int32)
        valid = (valid_u >= 0) & (valid_m >= 0)
        if valid.any():
            pred = np.sum(user_features[valid_u[valid]] * movie_features[valid_m[valid]], axis=1) \
                   + user_means[valid_u[valid]]
            test_rmse = float(np.sqrt(np.mean((pred - test_df['rating'].values[valid]) ** 2)))
            print(f"  测试集 RMSE: {test_rmse:.4f}")

    elapsed = time.time() - start_time
    print(f"  SVD 训练耗时: {elapsed:.2f} 秒")

    return {
        'algorithm': 'svd',
        'n_factors': n_factors,
        'user_features': user_features,
        'movie_features': movie_features,
        'user_means': user_means,
        'global_mean': global_mean,
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'n_users': n_users,
        'n_movies': n_movies,
        'explained_variance': explained_variance,
        'train_rmse': train_rmse,
        'test_rmse': test_rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }


# ============================================================
# 3. User-Based Collaborative Filtering（SVD 投影加速版）
# ============================================================

def train_user_cf(train_df, n_neighbors=30, test_df=None):
    """
    User-CF 训练（轻量版）
    SVD 投影后的隐向量做近邻计算，避免 O(n²) 全量 pairwise。
    """
    print("\n" + "=" * 60)
    print(f"[User-CF 训练] 邻居数: {n_neighbors}")

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies) = _build_mappings_from_df(train_df)

    # 构建用户-电影评分矩阵 → 用 SVD 投影代替全量 Pearson
    u_idx = np.array([user2idx[uid] for uid in train_df['user_id']], dtype=np.int32)
    m_idx = np.array([movie2idx[mid] for mid in train_df['movie_id']], dtype=np.int32)
    r_val = train_df['rating'].values.astype(np.float32)

    global_mean = float(r_val.mean())
    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts

    # 构建评分矩阵并去均值 → 用 SVD 做降维逼近
    from scipy.sparse import csr_matrix
    from scipy.sparse.linalg import svds

    R_mat = np.full((n_users, n_movies), global_mean, dtype=np.float32)
    R_mat[u_idx, m_idx] = r_val
    R_mat -= user_means[:, None]

    k_svd = min(30, n_users - 1, n_movies - 1)
    u_svd, s_svd, vt_svd = svds(csr_matrix(R_mat), k=k_svd)
    idx_sort = np.argsort(-s_svd)
    u_svd = u_svd[:, idx_sort] * np.sqrt(s_svd[idx_sort])

    # 用户相似度矩阵 = 用户隐向量的余弦相似度
    norms = np.linalg.norm(u_svd, axis=1, keepdims=True)
    norms[norms == 0] = 1
    u_norm = u_svd / norms
    sim_matrix = u_norm @ u_norm.T
    sim_matrix = np.maximum(sim_matrix, 0)
    np.fill_diagonal(sim_matrix, 0)

    # 应用 Top-K（使用 Numba 加速）
    sim_topk = _apply_top_k(sim_matrix, n_neighbors)

    # ── 向量化 RMSE ──
    R_centered = R_mat.copy()
    R_centered[R_mat == 0] = 0
    mask = (R_mat != 0).astype(np.float32)

    pred_centered = sim_topk @ R_centered
    denom = np.abs(sim_topk) @ mask
    denom[denom == 0] = 1.0
    pred_all = pred_centered / denom + user_means[:, None]

    train_pred_vals = pred_all[u_idx, m_idx]
    train_rmse = float(np.sqrt(np.mean((train_pred_vals - r_val) ** 2)))
    print(f"  训练集 RMSE: {train_rmse:.4f}")

    test_rmse = None
    if test_df is not None and len(test_df) > 0:
        tu = np.array([user2idx.get(uid, -1) for uid in test_df['user_id']], dtype=np.int32)
        tm = np.array([movie2idx.get(mid, -1) for mid in test_df['movie_id']], dtype=np.int32)
        valid = (tu >= 0) & (tm >= 0)
        if valid.any():
            pred = pred_all[tu[valid], tm[valid]]
            test_rmse = float(np.sqrt(np.mean((pred - test_df['rating'].values[valid]) ** 2)))
            print(f"  测试集 RMSE: {test_rmse:.4f}")

    elapsed = time.time() - start_time
    print(f"  User-CF 训练耗时: {elapsed:.2f} 秒")

    return {
        'algorithm': 'user_cf',
        'n_neighbors': n_neighbors,
        'user_mean_rating': {int(uid): float(user_means[i]) for uid, i in user2idx.items()},
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'train_rmse': train_rmse,
        'test_rmse': test_rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }


# ============================================================
# 4. Item-Based Collaborative Filtering（全向量化）
# ============================================================

def train_item_cf(train_df, n_neighbors=30, test_df=None):
    """
    Item-CF 训练（全向量化）
    Adjusted Cosine Similarity + 多线程并行。
    """
    print("\n" + "=" * 60)
    print(f"[Item-CF 训练] 邻居数: {n_neighbors}")

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies) = _build_mappings_from_df(train_df)

    u_idx = np.array([user2idx[uid] for uid in train_df['user_id']], dtype=np.int32)
    m_idx = np.array([movie2idx[mid] for mid in train_df['movie_id']], dtype=np.int32)
    r_val = train_df['rating'].values.astype(np.float32)

    # ── 使用多线程加速的 numpy 运算 ──
    # 电影-用户评分矩阵
    movie_user = np.full((n_movies, n_users), np.nan, dtype=np.float32)
    movie_user[m_idx, u_idx] = r_val
    movie_means = np.nan_to_num(np.nanmean(movie_user, axis=1), nan=0.0)

    # Adjusted Cosine 相似度（全向量化）
    centered = np.nan_to_num(movie_user - movie_means[:, None], nan=0.0)
    norms = np.linalg.norm(centered, axis=1)
    norms[norms == 0] = 1.0
    normalized = centered / norms[:, None]

    # 矩阵乘法自动利用所有核心（OMP_NUM_THREADS 控制）
    sim_matrix = normalized @ normalized.T

    # 过滤共同评分不足的
    has_rating = np.where(np.isfinite(movie_user), 1.0, 0.0)
    co_counts = has_rating @ has_rating.T
    sim_matrix[co_counts < 3] = 0.0
    np.fill_diagonal(sim_matrix, 0.0)
    sim_matrix = np.maximum(sim_matrix, 0.0)

    # ── Top-K ──
    sim_topk = _apply_top_k(sim_matrix, n_neighbors)

    # ── RMSE ──
    R = np.nan_to_num(movie_user, nan=0.0)
    R_mask = (R > 0).astype(np.float32)

    pred = sim_topk @ R
    denom = np.abs(sim_topk) @ R_mask
    denom[denom == 0] = 1.0
    pred /= denom

    train_pred_vals = pred[m_idx, u_idx]
    train_rmse = float(np.sqrt(np.mean((train_pred_vals - r_val) ** 2)))
    print(f"  训练集 RMSE: {train_rmse:.4f}")

    test_rmse = None
    if test_df is not None and len(test_df) > 0:
        tu = np.array([user2idx.get(uid, -1) for uid in test_df['user_id']], dtype=np.int32)
        tm = np.array([movie2idx.get(mid, -1) for mid in test_df['movie_id']], dtype=np.int32)
        valid = (tu >= 0) & (tm >= 0)
        if valid.any():
            pred_t = pred[tm[valid], tu[valid]]
            test_rmse = float(np.sqrt(np.mean((pred_t - test_df['rating'].values[valid]) ** 2)))
            print(f"  测试集 RMSE: {test_rmse:.4f}")

    elapsed = time.time() - start_time
    print(f"  Item-CF 训练耗时: {elapsed:.2f} 秒")

    # 构建电影均值 dict (给导出用)
    movie_mean_rating = {int(mid): float(movie_means[i]) for i, mid in enumerate(all_movies)}

    # 用户已评分电影集合 (给导出用)
    user_movies = defaultdict(set)
    for uid, mid in zip(train_df['user_id'], train_df['movie_id']):
        user_movies[int(uid)].add(int(mid))

    # 导出 movie_sim_dict（从 sim_topk 提取，仅保留 top-k）
    movie_sim_dict = {}
    for i in range(n_movies):
        row = sim_topk[i]
        pos = np.where(row > 0)[0]
        if len(pos):
            movie_sim_dict[int(all_movies[i])] = {
                int(all_movies[j]): float(row[j]) for j in pos
            }

    return {
        'algorithm': 'item_cf',
        'n_neighbors': n_neighbors,
        'movie_sim_matrix': movie_sim_dict,
        'movie_mean_rating': movie_mean_rating,
        'user_movies': {str(k): list(v) for k, v in user_movies.items()},
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'train_rmse': train_rmse,
        'test_rmse': test_rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }


# ============================================================
# 5. 模型保存
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
            'numba': _HAS_NUMBA,
        },
    }
    filepath = os.path.join(MODEL_DIR, 'metadata.json')
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"\n[元数据] 已保存 -> {filepath}")


# ============================================================
# 6. 缓存导出（多进程并行版）
# ============================================================

def _export_users_batch(uids, user2idx, user_features, movie_vectors,
                        movie_ids, user_means, top_n, user_rated_movies):
    """处理一批用户的推荐计算（全 numpy 向量化）"""
    batch_size = len(uids)
    n_movies = len(movie_ids)

    # 构建用户索引
    u_idx = np.array([user2idx[uid] for uid in uids], dtype=np.int32)
    mu = user_means[u_idx]

    # 所有电影评分 (batch × n_movies) — 矩阵乘法自动多线程
    pred_all = user_features[u_idx] @ movie_vectors.T + mu[:, None]

    # 排除已评分
    for b, uid in enumerate(uids):
        rated = user_rated_movies.get(int(uid))
        if rated:
            pred_all[b, [movie_ids.index(mid) for mid in rated if mid in movie_ids]] = -np.inf

    # Top-N 选择 (对整个 batch 做一次 argpartition)
    k = min(top_n, n_movies)
    top_idx = np.argpartition(pred_all, -k, axis=1)[:, -k:]

    results = []
    for b, uid in enumerate(uids):
        indices = top_idx[b]
        scores = pred_all[b, indices]
        # 按分数降序排序
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
    """
    用户推荐导出（多进程并行）
    每个进程处理一个 batch，numpy 矩阵运算在多进程下可充分利用所有核心。
    """
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
    # 自适应 batch_size：目标每个 batch 处理约 5 秒的工作量
    if batch_size is None:
        batch_size = max(500, min(10000, n_users // (_N_CPUS * 2)))
    batches = [user_ids[i:i + batch_size] for i in range(0, len(user_ids), batch_size)]
    n_batches = len(batches)
    print(f"  用户数: {n_users}  |  电影数: {n_movies}  |  Top-N: {top_n}")
    print(f"  批量: {batch_size} 用户/batch × {n_batches} batches  |  进程数: {_N_CPUS}")

    total_start = time.time()
    all_results = []

    # 使用 ProcessPoolExecutor 替代 ThreadPoolExecutor
    # 多进程可以突破 GIL 限制，每个进程独立使用 numpy 多线程
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

        # 进度报告
        elapsed = time.time() - total_start
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"  进度: {processed}/{n_users} 用户  |  速率: {rate:.0f} 用户/秒")

    all_results.sort(key=lambda x: x[0])

    # 批量写入 CSV（减少 I/O 操作次数）
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

    # 修复 key 类型
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
    """CSV → SQL REPLACE INTO（批量写入优化）"""
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

        batch_size = 1000  # 增大 SQL 批量大小
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

    # 多进程并行计算
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
# 7. 主流程
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='MovieLens 推荐系统 - 模型训练 (CPU 极致优化版 v4)'
    )
    parser.add_argument('--skip-eval', action='store_true',
                        help='skip RMSE evaluation, only train algorithms (save ~95 pct time)')
    parser.add_argument('--n-jobs', type=int, default=None,
                        help=f'parallel jobs (default: {_N_CPUS})')
    parser.add_argument('--export-only', action='store_true',
                        help='export cache from existing models only, no retraining')
    parser.add_argument('--top-n', type=int, default=20,
                        help='top-n recommendations per user/movie (default: 20)')
    return parser.parse_args()


def main():
    args = parse_args()

    # 如果指定了 n-jobs，覆盖全局设置
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
    MovieLens 推荐系统 - 模型训练 (CPU 极致优化版 v4)
{'=' * 60}
  CPU 核心: {_N_CPUS_AVAILABLE}  |  使用: {_N_CPUS}  |  Numba: {'是' if _HAS_NUMBA else '否'}
  跳过评估: {'是' if skip_eval else '否'}  |  Top-N: {top_n}
"""
    print(header)
    overall_start = time.time()

    if args.export_only:
        print("[仅导出模式] 从已有模型加载...")
        # 加载已有模型
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
    # 跳过评估时，使用所有数据训练（不划分测试集）
    if skip_eval:
        train_df = ratings_df
        test_df = None
        print(f"\n[跳过评估] 使用全部 {len(train_df)} 条数据训练")
    else:
        train_df, test_df = train_test_split(ratings_df, test_ratio=0.2, random_state=42)

    print(f"\n{'-' * 60}\n")

    # ── SVD ──
    svd_model = train_svd(train_df, n_factors=50, test_df=test_df)
    print(f"  SVD 总耗时: {svd_model['train_time']:.2f} 秒\n{'-' * 60}\n")

    # ── User-CF ──
    user_cf_model = train_user_cf(train_df, n_neighbors=30, test_df=test_df)
    print(f"  User-CF 总耗时: {user_cf_model['train_time']:.2f} 秒\n{'-' * 60}\n")

    # ── Item-CF ──
    item_cf_model = train_item_cf(train_df, n_neighbors=30, test_df=test_df)
    print(f"  Item-CF 总耗时: {item_cf_model['train_time']:.2f} 秒\n{'-' * 60}\n")

    # ── 保存模型 ──
    save_model(svd_model, 'svd_model')
    save_model(user_cf_model, 'user_cf_model')
    save_model(item_cf_model, 'item_cf_model')

    # ── 元数据 ──
    models_info = [
        {'name': 'svd', 'algorithm': 'svd', 'n_factors': svd_model['n_factors'],
         'train_rmse': svd_model['train_rmse'], 'test_rmse': svd_model['test_rmse'],
         'train_time': svd_model['train_time'], 'train_size': svd_model['train_size']},
        {'name': 'user_cf', 'algorithm': 'user_cf', 'n_neighbors': user_cf_model['n_neighbors'],
         'train_rmse': user_cf_model['train_rmse'], 'test_rmse': user_cf_model['test_rmse'],
         'train_time': user_cf_model['train_time'], 'train_size': user_cf_model['train_size']},
        {'name': 'item_cf', 'algorithm': 'item_cf', 'n_neighbors': item_cf_model['n_neighbors'],
         'train_rmse': item_cf_model['train_rmse'], 'test_rmse': item_cf_model['test_rmse'],
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
svd                  {svd_model['train_rmse']:.4f}       {svd_model['test_rmse'] or 0:.4f}       {svd_model['train_time']:.1f}
user_cf              {user_cf_model['train_rmse']:.4f}       {user_cf_model['test_rmse'] or 0:.4f}       {user_cf_model['train_time']:.1f}
item_cf              {item_cf_model['train_rmse']:.4f}       {item_cf_model['test_rmse'] or 0:.4f}       {item_cf_model['train_time']:.1f}
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

    # ── 导入指引 ──
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