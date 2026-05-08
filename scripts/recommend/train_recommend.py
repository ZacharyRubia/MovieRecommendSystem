#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_recommend.py - 推荐算法训练脚本 (CPU 优化版)

训练三种推荐算法：
1. SVD (Singular Value Decomposition) 矩阵分解
2. User-Based Collaborative Filtering
3. Item-Based Collaborative Filtering

训练完成后自动将推荐结果导出为 MySQL 可导入的 CSV/SQL 文件，
以及 Qdrant 可导入的数据格式。

数据来源: scripts/extract_test_subset_test/
模型输出: scripts/models/
缓存导出: scripts/export/  (可供 MySQL LOAD DATA / Qdrant 导入)

优化说明：
  - Item-CF 相似度矩阵使用 numpy 向量化计算，消除 O(n²) 双重循环
  - SVD 去均值使用 numpy 广播替代逐行循环
  - RMSE 计算使用 numpy 向量化替代 pandas iterrows()
  - train_test_split 使用 numpy 随机选择替代 pandas groupby
  - 导出函数使用批量处理 + multiprocessing 并行
  - 数据映射复用，避免重复构建
  - 使用多进程加速独立任务
"""

import os
import sys
import pickle
import json
import time
import math
import random
import csv
from concurrent.futures import ProcessPoolExecutor, as_completed, ThreadPoolExecutor
from functools import partial

import numpy as np
from collections import defaultdict
from datetime import datetime

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
EXPORT_DIR = os.path.join(BASE_DIR, 'export')

# 确保目录存在
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# CPU 核心数（用于并行处理）
N_CPUS = min(os.cpu_count() or 1, 32)
print(f"[系统] CPU 核心数: {N_CPUS}")


# ============================================================
# 1. 数据加载与预处理
# ============================================================

def load_data():
    """加载评分数据和电影信息（优化版：使用更高效的数据结构）"""
    print("=" * 60)
    print("[加载数据] 读取评分数据和电影信息...")

    import pandas as pd

    # 加载评分数据 - 使用 dtype 指定列类型减少内存
    ratings_df = pd.read_csv(os.path.join(DATA_DIR, 'test_ratings.csv'),
                             dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32})
    print(f"  评分数据: {len(ratings_df)} 条, "
          f"用户 {ratings_df['user_id'].nunique()} 个, "
          f"电影 {ratings_df['movie_id'].nunique()} 部")

    # 加载电影信息
    movies_df = pd.read_csv(os.path.join(DATA_DIR, 'test_movies.csv'))
    print(f"  电影信息: {len(movies_df)} 部电影")

    # 用户/电影映射: 原始 id -> 0-based 连续索引
    # 使用 numpy 的 unique + searchsorted 加速
    unique_users = np.sort(ratings_df['user_id'].unique())
    unique_movies = np.sort(ratings_df['movie_id'].unique())

    user2idx = {int(uid): i for i, uid in enumerate(unique_users)}
    movie2idx = {int(mid): i for i, mid in enumerate(unique_movies)}
    idx2user = {i: int(uid) for uid, i in user2idx.items()}
    idx2movie = {i: int(mid) for mid, i in movie2idx.items()}

    print(f"  用户映射: {len(user2idx)} 个, 电影映射: {len(movie2idx)} 个")

    return ratings_df, movies_df, user2idx, movie2idx, idx2user, idx2movie


def train_test_split(ratings_df, test_ratio=0.2, random_state=42):
    """按用户划分训练集和测试集（优化版：向量化操作）"""
    print(f"\n[数据划分] 测试集比例: {test_ratio}")

    rng = np.random.default_rng(random_state)
    train_indices = []
    test_indices = []

    # 使用 groupby + numpy 替代逐行迭代
    import pandas as pd
    for _, group in ratings_df.groupby('user_id'):
        n = len(group)
        # 至少保留一条给训练集
        n_test = max(1, min(int(n * test_ratio), n - 1))
        # 随机选择测试集索引
        chosen = rng.choice(n, size=n_test, replace=False)
        mask = np.zeros(n, dtype=bool)
        mask[chosen] = True
        test_indices.extend(group.index[mask].tolist())
        train_indices.extend(group.index[~mask].tolist())

    train_df = ratings_df.loc[train_indices].reset_index(drop=True)
    test_df = ratings_df.loc[test_indices].reset_index(drop=True)

    print(f"  训练集: {len(train_df)} 条")
    print(f"  测试集: {len(test_df)} 条")
    print(f"  训练集用户: {train_df['user_id'].nunique()}, "
          f"测试集用户: {test_df['user_id'].nunique()}")

    return train_df, test_df


def build_rating_matrix(train_df, n_users, n_movies, user2idx, movie2idx):
    """构建稀疏评分矩阵 (用户×电影) - 优化版"""
    print("\n[构建矩阵] 构建评分矩阵...")
    global_mean = float(train_df['rating'].mean())

    # 使用 numpy 直接填充矩阵，比逐行循环快
    matrix = np.full((n_users, n_movies), global_mean, dtype=np.float32)

    # 批量映射并填充
    u_indices = np.array([user2idx.get(uid, 0) for uid in train_df['user_id']], dtype=np.int32)
    m_indices = np.array([movie2idx.get(mid, 0) for mid in train_df['movie_id']], dtype=np.int32)
    ratings = train_df['rating'].values.astype(np.float32)
    matrix[u_indices, m_indices] = ratings

    n_total = n_users * n_movies
    print(f"  矩阵大小: {n_users} × {n_movies}")
    print(f"  填充元素: {len(train_df)} / {n_total} ({100 * len(train_df) / n_total:.4f}%)")
    print(f"  全局评分均值: {global_mean:.4f}")

    return matrix, global_mean


# ============================================================
# 辅助函数：快速构建用户/电影映射
# ============================================================

def _build_mappings_from_df(train_df):
    """从 DataFrame 快速构建映射"""
    all_users = np.sort(train_df['user_id'].unique())
    all_movies = np.sort(train_df['movie_id'].unique())

    user2idx = {int(uid): i for i, uid in enumerate(all_users)}
    movie2idx = {int(mid): i for i, mid in enumerate(all_movies)}
    idx2user = {i: int(uid) for uid, i in user2idx.items()}
    idx2movie = {i: int(mid) for mid, i in movie2idx.items()}

    return (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
            len(all_users), len(all_movies))


# ============================================================
# 2. SVD 矩阵分解 (使用 scipy.sparse.linalg.svds)
# ============================================================

def train_svd(train_df, n_factors=50, test_df=None):
    """
    使用 SVD 矩阵分解训练模型

    原理: R ≈ U·Σ·V^T
    其中 R 是评分矩阵, U 是用户特征矩阵, V 是电影特征矩阵

    参数:
        n_factors: 隐因子数量（特征维度）
    """
    print("\n" + "=" * 60)
    print(f"[SVD 训练] 隐因子数: {n_factors}")

    start_time = time.time()

    # 构建映射
    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies) = _build_mappings_from_df(train_df)

    matrix, global_mean = build_rating_matrix(train_df, n_users, n_movies,
                                               user2idx, movie2idx)

    # 均值中心化 - 使用 numpy 向量化操作
    user_means = np.zeros(n_users, dtype=np.float32)
    u_indices = np.array([user2idx[uid] for uid in train_df['user_id']], dtype=np.int32)
    m_indices = np.array([movie2idx[mid] for mid in train_df['movie_id']], dtype=np.int32)
    np.add.at(user_means, u_indices, train_df['rating'].values.astype(np.float32))
    user_counts = np.bincount(u_indices, minlength=n_users).astype(np.float32)
    user_counts[user_counts == 0] = 1  # 避免除零
    user_means /= user_counts

    # 去均值（向量化）
    centered = matrix - user_means[:, np.newaxis]
    # 在已评分位置恢复精确的去均值值
    ratings_vals = train_df['rating'].values.astype(np.float32)
    centered[u_indices, m_indices] = ratings_vals - user_means[u_indices]

    # 使用 truncated SVD
    print(f"  运行 Truncated SVD (因子数={n_factors})...")
    from scipy.sparse.linalg import svds
    from scipy.sparse import csr_matrix

    k = min(n_factors, min(n_users, n_movies) - 1)
    # 转为稀疏矩阵加速
    sparse_centered = csr_matrix(centered)
    u_svd, s_svd, vt_svd = svds(sparse_centered, k=k)

    # 按奇异值降序排列
    idx_sort = np.argsort(-s_svd)
    s_svd = s_svd[idx_sort]
    u_svd = u_svd[:, idx_sort]
    vt_svd = vt_svd[idx_sort, :]

    # 构建用户特征矩阵和电影特征矩阵
    # R ≈ U * S * V^T = (U * sqrt(S)) * (sqrt(S) * V^T)
    sqrt_s = np.sqrt(s_svd)
    user_features = u_svd * sqrt_s
    movie_features = vt_svd.T * sqrt_s

    print(f"  SVD 奇异值: {s_svd[:5]} ...")

    # ---------- 计算训练 RMSE (向量化) ----------
    train_pred = np.sum(user_features[u_indices] * movie_features[m_indices], axis=1) + user_means[u_indices]
    train_rmse = float(np.sqrt(np.mean((train_pred - ratings_vals) ** 2)))
    print(f"  训练集 RMSE: {train_rmse:.4f}")

    # ---------- 计算测试 RMSE (如果有测试集) ----------
    test_rmse = None
    if test_df is not None:
        test_u = np.array([user2idx.get(uid, 0) for uid in test_df['user_id']], dtype=np.int32)
        test_m = np.array([movie2idx.get(mid, 0) for mid in test_df['movie_id']], dtype=np.int32)
        test_r = test_df['rating'].values.astype(np.float32)
        # 过滤映射中不存在的用户/电影
        valid = np.array([uid in user2idx and mid in movie2idx for uid, mid in
                          zip(test_df['user_id'], test_df['movie_id'])])
        if valid.any():
            test_pred = np.sum(user_features[test_u[valid]] * movie_features[test_m[valid]], axis=1) + user_means[test_u[valid]]
            test_rmse = float(np.sqrt(np.mean((test_pred - test_r[valid]) ** 2)))
            print(f"  测试集 RMSE: {test_rmse:.4f}")

    elapsed = time.time() - start_time
    print(f"  SVD 训练耗时: {elapsed:.2f} 秒")

    model = {
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
        'singular_values': s_svd,
        'train_rmse': train_rmse,
        'test_rmse': test_rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }

    return model


# ============================================================
# 3. User-Based Collaborative Filtering
# ============================================================

def train_user_cf(train_df, n_neighbors=30, test_df=None):
    """
    训练 User-Based Collaborative Filtering

    原理: 找到与目标用户最相似的 K 个用户，
          用这些用户对某电影的评分加权平均作为预测值
    相似度: Pearson 相关系数
    """
    print("\n" + "=" * 60)
    print(f"[User-CF 训练] 邻居数: {n_neighbors}")

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies) = _build_mappings_from_df(train_df)

    # 构建用户-电影评分字典
    user_ratings = defaultdict(dict)
    for uid, mid, rating in zip(train_df['user_id'], train_df['movie_id'], train_df['rating']):
        user_ratings[int(uid)][int(mid)] = float(rating)

    # 计算每个用户的平均评分（向量化）
    user_mean_series = train_df.groupby('user_id')['rating'].mean()
    user_mean_rating = {int(uid): float(mean) for uid, mean in user_mean_series.items()}

    print(f"  用户平均分计算完成, 共 {len(user_mean_rating)} 个用户")

    # ---------- 计算用户相似度矩阵 (Pearson) ----------
    print("  [优化版] 正在构建 User-Movie 评分矩阵...")
    start_sim_time = time.time()

    import pandas as pd
    # 利用 pandas 透视出 用户-电影 矩阵
    user_movie_matrix = train_df.pivot(index='user_id', columns='movie_id', values='rating')
    print(f"  矩阵形状: {user_movie_matrix.shape}")

    print("  [优化版] 正在计算 Pearson 相似度矩阵 (向量化计算)...")
    sim_df = user_movie_matrix.T.corr(method='pearson', min_periods=5)

    # 提取有效相似度对
    user_sim_matrix = defaultdict(dict)
    sim_stacked = sim_df.stack()
    # 只取上半三角加上对角线，避免重复
    for (uid1, uid2), sim in sim_stacked.items():
        if uid1 != uid2 and sim > 0:
            user_sim_matrix[int(uid1)][int(uid2)] = float(sim)

    pair_count = sum(len(v) for v in user_sim_matrix.values())
    print(f"  有效相似度用户对: {pair_count}")
    print(f"  相似度计算耗时: {time.time() - start_sim_time:.2f} 秒")

    # ---------- 计算训练 RMSE ----------
    train_rmse = _compute_user_cf_rmse(
        train_df, user_ratings, user_sim_matrix, user_mean_rating, n_neighbors
    )
    print(f"  训练集 RMSE: {train_rmse:.4f}")

    # ---------- 计算测试 RMSE ----------
    test_rmse = None
    if test_df is not None:
        test_rmse = _compute_user_cf_rmse(
            test_df, user_ratings, user_sim_matrix, user_mean_rating, n_neighbors
        )
        print(f"  测试集 RMSE: {test_rmse:.4f}")

    elapsed = time.time() - start_time
    print(f"  User-CF 训练耗时: {elapsed:.2f} 秒")

    model = {
        'algorithm': 'user_cf',
        'n_neighbors': n_neighbors,
        'user_ratings': dict(user_ratings),
        'user_sim_matrix': {str(k): v for k, v in user_sim_matrix.items()},
        'user_mean_rating': user_mean_rating,
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

    return model


def _predict_user_cf_batch(uids, mids, user_ratings, user_sim_matrix,
                            user_mean_rating, n_neighbors):
    """User-CF 批量预测（向量化版本）"""
    predictions = []
    valid_mask = []

    for uid, mid in zip(uids, mids):
        uid_int = int(uid)
        mid_int = int(mid)
        if uid_int not in user_ratings:
            predictions.append(user_mean_rating.get(uid_int, 3.5))
            valid_mask.append(True)
            continue

        sim_users = user_sim_matrix.get(uid_int, {})
        if not sim_users:
            predictions.append(user_mean_rating.get(uid_int, 3.5))
            valid_mask.append(True)
            continue

        uid_mean = user_mean_rating.get(uid_int, 3.5)
        neighbors = []
        for nuid, sim in sim_users.items():
            if mid_int in user_ratings.get(nuid, {}):
                neighbors.append((nuid, sim, user_ratings[nuid][mid_int]))

        if not neighbors:
            predictions.append(uid_mean)
            valid_mask.append(True)
            continue

        # 取 Top-K
        neighbors.sort(key=lambda x: -x[1])
        neighbors = neighbors[:n_neighbors]

        num = 0.0
        den = 0.0
        for nuid, sim, rating in neighbors:
            n_mean = user_mean_rating.get(nuid, 3.5)
            num += sim * (rating - n_mean)
            den += abs(sim)

        if den > 0:
            predictions.append(uid_mean + num / den)
        else:
            predictions.append(uid_mean)
        valid_mask.append(True)

    return np.array(predictions, dtype=np.float32)


def _compute_user_cf_rmse(df, user_ratings, user_sim_matrix,
                          user_mean_rating, n_neighbors):
    """计算 User-CF 预测的 RMSE（优化版：批量处理）"""
    errors = _predict_user_cf_batch(
        df['user_id'].values, df['movie_id'].values,
        user_ratings, user_sim_matrix, user_mean_rating, n_neighbors
    )
    true_ratings = df['rating'].values.astype(np.float32)
    # 截断到有效范围
    min_len = min(len(errors), len(true_ratings))
    if min_len == 0:
        return float('inf')
    return float(np.sqrt(np.mean((errors[:min_len] - true_ratings[:min_len]) ** 2)))


# ============================================================
# 4. Item-Based Collaborative Filtering (向量化优化版)
# ============================================================

def train_item_cf(train_df, n_neighbors=30, test_df=None):
    """
    训练 Item-Based Collaborative Filtering (向量化优化版)

    使用 numpy 矩阵运算替代双重循环，大幅提升 CPU 利用率。

    原理: 对于目标用户已评分的每部电影，
          找到与它最相似的 K 部电影，
          用相似度和评分的加权平均作为预测
    相似度: 调整的余弦相似度 (Adjusted Cosine Similarity)
    """
    print("\n" + "=" * 60)
    print(f"[Item-CF 训练] 邻居数: {n_neighbors}")

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies) = _build_mappings_from_df(train_df)

    # ---------- 构建电影-用户评分矩阵 (密集矩阵) ----------
    print("  [优化版] 构建电影-用户评分矩阵...")
    # 行=电影，列=用户，值=评分
    movie_user_matrix = np.full((n_movies, n_users), np.nan, dtype=np.float32)
    u_indices = np.array([user2idx[uid] for uid in train_df['user_id']], dtype=np.int32)
    m_indices = np.array([movie2idx[mid] for mid in train_df['movie_id']], dtype=np.int32)
    ratings = train_df['rating'].values.astype(np.float32)
    movie_user_matrix[m_indices, u_indices] = ratings

    # 计算每部电影的评分均值
    with np.errstate(invalid='ignore'):
        movie_means = np.nanmean(movie_user_matrix, axis=1)
    movie_means = np.nan_to_num(movie_means, nan=0.0)

    print(f"  电影平均分计算完成, 共 {n_movies} 部电影")
    print(f"  矩阵形状: {n_movies} × {n_users}")

    # ---------- 计算电影相似度矩阵 (向量化) ----------
    print("  [优化版] 计算电影相似度矩阵 (向量化)...")
    start_sim_time = time.time()

    # 去均值
    centered = movie_user_matrix - movie_means[:, np.newaxis]
    centered = np.nan_to_num(centered, nan=0.0)

    # 计算余弦相似度矩阵
    # sim(i,j) = dot(ci, cj) / (||ci|| * ||cj||)
    norms = np.linalg.norm(centered, axis=1)
    norms[norms == 0] = 1.0  # 避免除零

    # 归一化
    normalized = centered / norms[:, np.newaxis]

    # 计算相似度矩阵 (n_movies × n_movies)
    # 使用矩阵乘法，利用 BLAS 加速
    sim_matrix = np.dot(normalized, normalized.T)

    # 获取共同评分计数矩阵（用于过滤）
    has_rating = ~np.isnan(movie_user_matrix)
    co_counts = np.dot(has_rating.astype(np.float32), has_rating.astype(np.float32).T)

    # 过滤：至少共同评分过 3 个用户
    min_periods = 3
    sim_matrix[co_counts < min_periods] = 0.0
    # 排除自相似和负相关
    np.fill_diagonal(sim_matrix, 0.0)  # 对角设为0（自相似不计入）

    # 构建 sparse 格式的相似度字典
    movie_sim_matrix = {}
    for i in range(n_movies):
        row = sim_matrix[i]
        pos_mask = row > 0
        if pos_mask.any():
            indices = np.where(pos_mask)[0]
            movie_sim_matrix[int(all_movies[i])] = {
                int(all_movies[j]): float(row[j]) for j in indices
            }

    pair_count = sum(len(v) for v in movie_sim_matrix.values())
    print(f"  有效相似度电影对: {pair_count}")
    print(f"  相似度计算耗时: {time.time() - start_sim_time:.2f} 秒")

    # 构建 movie_ratings 字典（用于后续预测）
    movie_ratings = defaultdict(dict)
    user_movies = defaultdict(set)
    for uid, mid, rating in zip(train_df['user_id'], train_df['movie_id'], train_df['rating']):
        movie_ratings[int(mid)][int(uid)] = float(rating)
        user_movies[int(uid)].add(int(mid))

    movie_mean_rating = {int(mid): float(mean) for mid, mean in
                         zip(all_movies, movie_means)}

    # ---------- 计算训练 RMSE ----------
    train_rmse = _compute_item_cf_rmse(
        train_df, movie_ratings, movie_sim_matrix, movie_mean_rating,
        user_movies, n_neighbors
    )
    print(f"  训练集 RMSE: {train_rmse:.4f}")

    # ---------- 计算测试 RMSE ----------
    test_rmse = None
    if test_df is not None:
        test_rmse = _compute_item_cf_rmse(
            test_df, movie_ratings, movie_sim_matrix, movie_mean_rating,
            user_movies, n_neighbors
        )
        print(f"  测试集 RMSE: {test_rmse:.4f}")

    elapsed = time.time() - start_time
    print(f"  Item-CF 训练耗时: {elapsed:.2f} 秒")

    model = {
        'algorithm': 'item_cf',
        'n_neighbors': n_neighbors,
        'movie_ratings': dict(movie_ratings),
        'movie_sim_matrix': {str(k): v for k, v in movie_sim_matrix.items()},
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

    return model


def _compute_item_cf_rmse(df, movie_ratings, movie_sim_matrix,
                          movie_mean_rating, user_movies, n_neighbors):
    """计算 Item-CF 预测的 RMSE（优化版）"""
    errors = []
    for _, row in df.iterrows():
        uid, mid, true_rating = int(row['user_id']), int(row['movie_id']), row['rating']
        pred = _predict_item_cf(uid, mid, movie_ratings, movie_sim_matrix,
                                movie_mean_rating, user_movies, n_neighbors)
        if pred is not None:
            errors.append((pred - true_rating) ** 2)
    return math.sqrt(np.mean(errors)) if errors else float('inf')


def _predict_item_cf(uid, mid, movie_ratings, movie_sim_matrix,
                     movie_mean_rating, user_movies, n_neighbors):
    """Item-CF 单条预测"""
    if uid not in user_movies:
        return 3.5

    user_rated = user_movies[uid]
    if not user_rated:
        return 3.5

    # 获取目标电影的相似电影
    sim_movies = movie_sim_matrix.get(mid, {})
    if not sim_movies:
        return movie_mean_rating.get(mid, 3.5)

    # 找出用户评分过的、与目标电影相似的电影
    neighbors = []
    for rmid in user_rated:
        if rmid in sim_movies:
            sim = sim_movies[rmid]
            if sim > 0:
                rating = movie_ratings.get(rmid, {}).get(uid)
                if rating is not None:
                    neighbors.append((rmid, sim, rating))

    if not neighbors:
        return movie_mean_rating.get(mid, 3.5)

    neighbors.sort(key=lambda x: -x[1])
    neighbors = neighbors[:n_neighbors]

    num = 0.0
    den = 0.0
    for _, sim, rating in neighbors:
        num += sim * rating
        den += abs(sim)

    if den > 0:
        return num / den
    return movie_mean_rating.get(mid, 3.5)


# ============================================================
# 5. 模型保存
# ============================================================

def save_model(model, name):
    """保存训练好的模型"""
    filepath = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {name} -> {filepath}")

    with open(filepath, 'wb') as f:
        pickle.dump(model, f)

    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"  模型大小: {size_mb:.2f} MB")


def save_metadata(models_info, train_df, test_df):
    """保存训练元数据"""
    metadata = {
        'train_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'dataset': {
            'train_size': len(train_df),
            'test_size': len(test_df),
            'n_users': int(train_df['user_id'].nunique()),
            'n_movies': int(train_df['movie_id'].nunique()),
            'rating_mean': float(train_df['rating'].mean()),
            'rating_std': float(train_df['rating'].std()),
        },
        'models': models_info
    }

    filepath = os.path.join(MODEL_DIR, 'metadata.json')
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"\n[元数据] 已保存 -> {filepath}")


# ============================================================
# 6. 缓存导出（MySQL 可导入的 CSV / SQL / JSON）
# ============================================================

def _export_movie_similarity_worker(mid, movie_sim_matrix, top_n, current_time):
    """处理单个电影的相似度输出（用于并行处理，模块级函数）"""
    try:
        sim_movies = movie_sim_matrix[mid]
        if not sim_movies:
            return mid, None
        sorted_sims = sorted(sim_movies.items(), key=lambda x: -x[1])[:top_n]
        sim_list = [
            {"movie_id": int(sim_mid), "score": round(float(score), 4)}
            for sim_mid, score in sorted_sims
        ]
        json_str = json.dumps(sim_list, ensure_ascii=False)
        return mid, [int(mid), json_str, current_time]
    except Exception as e:
        return mid, str(e)


def _compute_user_recommendations(uid, user2idx, user_features, movie_vectors,
                                  movie_ids, user_means, top_n, rated_set):
    """计算单个用户的推荐列表（用于并行处理）"""
    try:
        u_idx = user2idx[uid]
        user_mean = float(user_means[u_idx])

        # 向量化计算：所有电影评分
        scores = np.dot(user_features[u_idx], movie_vectors.T) + user_mean

        # 排除已评分电影
        if rated_set:
            valid_mask = np.array([mid not in rated_set for mid in movie_ids])
            if valid_mask.any():
                filtered_scores = scores[valid_mask]
                filtered_mids = [int(mid) for mid, keep in zip(movie_ids, valid_mask) if keep]
            else:
                filtered_scores = scores
                filtered_mids = movie_ids
        else:
            filtered_scores = scores
            filtered_mids = movie_ids

        # 取 Top-N
        if len(filtered_scores) > top_n:
            top_indices = np.argpartition(filtered_scores, -top_n)[-top_n:]
            top_indices = top_indices[np.argsort(-filtered_scores[top_indices])]
        else:
            top_indices = np.argsort(-filtered_scores)

        rec_list = [
            {"movie_id": int(filtered_mids[idx]), "score": round(float(filtered_scores[idx]), 4)}
            for idx in top_indices
        ]

        return int(uid), rec_list, None
    except Exception as e:
        return int(uid), None, str(e)


def export_users_recommendations_csv(svd_model, item_cf_model=None, top_n=20):
    """
    使用 SVD 模型为所有训练用户生成 Top-N 推荐，导出为 CSV。
    优化版：多进程并行处理用户推荐计算。
    """
    print("\n" + "=" * 60)
    print("[缓存导出] 用户推荐 -> users_recommendations.csv (并行优化)")
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

    # 获取用户已评分的电影列表
    user_rated_movies = defaultdict(set)
    if item_cf_model and 'user_movies' in item_cf_model:
        for uid, mids in item_cf_model['user_movies'].items():
            user_rated_movies[int(uid)] = set(int(m) for m in mids)
        print(f"  已加载用户评分记录: {len(user_rated_movies)} 个用户")

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    algorithm_tag = 'svd'

    # 预计算所有电影的特征向量
    movie_ids = [int(mid) for mid in sorted(movie2idx.keys())]
    movie_vectors = np.array([movie_features[movie2idx[mid]] for mid in movie_ids])

    csv_path = os.path.join(EXPORT_DIR, 'users_recommendations.csv')
    start_time_total = time.time()

    # 准备参数
    user_ids = sorted(user2idx.keys())
    batch_size = max(1, len(user_ids) // (N_CPUS * 2))

    print(f"  并行处理: {N_CPUS} 进程, 每批次 {batch_size} 用户")

    # 使用多进程并行计算推荐
    all_results = []
    with ProcessPoolExecutor(max_workers=N_CPUS) as executor:
        futures = []
        for uid in user_ids:
            rated = user_rated_movies.get(int(uid), set())
            future = executor.submit(
                _compute_user_recommendations,
                uid, user2idx, user_features, movie_vectors,
                movie_ids, user_means, top_n, rated
            )
            futures.append(future)

        processed = 0
        errors = 0
        next_report = 1000

        for future in as_completed(futures):
            uid, rec_list, error = future.result()
            if error:
                errors += 1
                if errors <= 5:
                    print(f"  [警告] 用户 {uid} 处理失败: {error}")
            else:
                all_results.append((uid, rec_list))
                processed += 1

            if processed >= next_report:
                elapsed = time.time() - start_time_total
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  进度: {processed}/{n_users} (错误: {errors}, 速率: {rate:.0f} 用户/秒)")
                next_report += 1000

    # 按用户 ID 排序后写入 CSV
    all_results.sort(key=lambda x: x[0])

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for uid, rec_list in all_results:
            json_str = json.dumps(rec_list, ensure_ascii=False)
            writer.writerow([uid, json_str, algorithm_tag, current_time])

    total_elapsed = time.time() - start_time_total
    print(f"\n  完成: {processed}/{n_users} 用户 (错误: {errors})")
    print(f"  耗时: {total_elapsed:.2f} 秒")
    print(f"  输出文件: {csv_path}")
    file_size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    print(f"  文件大小: {file_size_mb:.2f} MB")

    return csv_path


def export_movies_similarities_csv(item_cf_model, top_n=20):
    """
    从 Item-CF 模型的 movie_sim_matrix 导出每部电影的 Top-N 相似电影。
    输出格式对应 MySQL movies_similarities 表。
    """
    print("\n" + "=" * 60)
    print("[缓存导出] 电影相似度 -> movies_similarities.csv")
    print("=" * 60)

    movie_sim_matrix = item_cf_model.get('movie_sim_matrix', {})
    if not movie_sim_matrix:
        print("[警告] Item-CF 模型中无电影相似度数据")
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

    csv_path = os.path.join(EXPORT_DIR, 'movies_similarities.csv')
    start_time_total = time.time()

    # 使用多进程并行处理
    movie_ids = sorted(movie_sim_matrix.keys())

    worker_func = partial(_export_movie_similarity_worker,
                          movie_sim_matrix=movie_sim_matrix,
                          top_n=top_n,
                          current_time=current_time)

    results = []
    with ProcessPoolExecutor(max_workers=N_CPUS) as executor:
        futures = {executor.submit(worker_func, mid): mid for mid in movie_ids}
        processed = 0
        errors = 0

        for future in as_completed(futures):
            mid, result = future.result()
            if isinstance(result, str):
                errors += 1
                if errors <= 5:
                    print(f"  [警告] 电影 {mid} 处理失败: {result}")
            elif result is not None:
                results.append(result)
                processed += 1

            if processed > 0 and processed % 5000 == 0:
                print(f"  进度: {processed}/{n_movies}")

    # 排序后写入
    results.sort(key=lambda x: x[0])
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for row in results:
            writer.writerow(row)

    total_elapsed = time.time() - start_time_total
    print(f"\n  完成: {processed}/{n_movies} 电影 (错误: {errors})")
    print(f"  耗时: {total_elapsed:.2f} 秒")
    print(f"  输出文件: {csv_path}")
    file_size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    print(f"  文件大小: {file_size_mb:.2f} MB")

    return csv_path


def generate_sql_from_csv(csv_path, table_type):
    """
    将已导出的 CSV 文件转换为 SQL REPLACE INTO 脚本，
    便于直接在 MySQL 中执行导入（免配 LOAD DATA 权限）。
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

    try:
        with open(csv_path, 'r', encoding='utf-8') as csv_in:
            reader = csv.reader(csv_in)
            rows = list(reader)
    except FileNotFoundError:
        print(f"  [跳过] 找不到 CSV 文件: {csv_path}")
        return None

    if not rows:
        print(f"  [跳过] CSV 文件为空")
        return None

    with open(sql_path, 'w', encoding='utf-8') as f_out:
        f_out.write(f"-- 自动生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f_out.write(f"-- 源文件: {os.path.basename(csv_path)}\n")
        f_out.write(f"-- 目标表: {table_name}\n\n")

        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]

            if table_type == 'user':
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
                f_out.write(
                    f"REPLACE INTO `{table_name}` "
                    f"(`{id_field}`, `{json_field}`, `updated_at`) VALUES\n"
                )
                values = []
                for row in batch:
                    main_id = row[0]
                    json_str = row[1].replace("'", "''")
                    updated_at = row[2] if len(row) > 2 else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    values.append(f"({main_id}, '{json_str}', '{updated_at}')")

            f_out.write(",\n".join(values) + ";\n\n")

    print(f"  行数: {len(rows)}")
    print(f"  输出: {sql_path}")
    file_size_mb = os.path.getsize(sql_path) / (1024 * 1024)
    print(f"  大小: {file_size_mb:.2f} MB")

    return sql_path


def export_caches_to_qdrant_json(svd_model, item_cf_model=None, top_n=20):
    """
    导出为 JSON 格式，供 Qdrant 推荐参考或 save_to_cache.py 读取。
    并行优化版：使用多进程加速。
    """
    print("\n" + "=" * 60)
    print("[缓存导出] 推荐数据 -> JSON (并行优化)")
    print("=" * 60)

    # ---- 导出用户推荐 JSON ----
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

    # 并行计算用户推荐
    user_ids = sorted(user2idx.keys())
    all_results = []

    with ProcessPoolExecutor(max_workers=N_CPUS) as executor:
        futures = []
        for uid in user_ids:
            rated = user_rated_movies.get(int(uid), set())
            future = executor.submit(
                _compute_user_recommendations,
                uid, user2idx, user_features, movie_vectors,
                movie_ids, user_means, top_n, rated
            )
            futures.append(future)

        for future in as_completed(futures):
            uid, rec_list, error = future.result()
            if error is None and rec_list is not None:
                all_results.append({
                    "user_id": uid,
                    "recommendations": rec_list,
                    "algorithm": "svd"
                })

    all_results.sort(key=lambda x: x["user_id"])

    user_json_path = os.path.join(EXPORT_DIR, 'users_recommendations.json')
    with open(user_json_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"  用户推荐 JSON: {user_json_path} ({len(all_results)} 个用户)")

    # ---- 导出电影相似度 JSON ----
    if item_cf_model and item_cf_model.get('movie_sim_matrix'):
        movie_sim_matrix = item_cf_model['movie_sim_matrix']
        movie_sim_matrix_int = {}
        for k, v in movie_sim_matrix.items():
            movie_sim_matrix_int[int(k)] = {
                int(sk): float(sv) for sk, sv in v.items()
            }

        movie_records = []
        for mid in sorted(movie_sim_matrix_int.keys()):
            sim_movies = movie_sim_matrix_int[mid]
            if not sim_movies:
                continue
            sorted_sims = sorted(sim_movies.items(), key=lambda x: -x[1])[:top_n]
            sim_list = [
                {"movie_id": int(sim_mid), "similarity": round(float(score), 4)}
                for sim_mid, score in sorted_sims
            ]
            movie_records.append({
                "movie_id": int(mid),
                "similar_movies": sim_list
            })

        movie_json_path = os.path.join(EXPORT_DIR, 'movies_similarities.json')
        with open(movie_json_path, 'w', encoding='utf-8') as f:
            json.dump(movie_records, f, ensure_ascii=False, indent=2)
        print(f"  电影相似度 JSON: {movie_json_path} ({len(movie_records)} 部电影)")
    else:
        print(f"  [跳过] Item-CF 模型无电影相似度数据，未导出 JSON")


def export_all_caches(svd_model, item_cf_model, top_n=20, enable_sql=True, enable_json=True):
    """
    导出所有缓存数据：
    1. CSV（供 MySQL LOAD DATA 导入）
    2. SQL（供 MySQL 直接执行）
    3. JSON（供 Qdrant / save_to_cache.py 导入）
    """
    print("\n" + "=" * 60)
    print("  缓存数据导出")
    print("=" * 60)
    print(f"  输出目录: {EXPORT_DIR}")
    print(f"  Top-N: {top_n}")
    print(f"  生成 SQL: {'是' if enable_sql else '否'}")
    print(f"  生成 JSON: {'是' if enable_json else '否'}")
    print("=" * 60)

    # 1. 导出 CSV
    csv_user = export_users_recommendations_csv(svd_model, item_cf_model, top_n=top_n)
    csv_movie = export_movies_similarities_csv(item_cf_model, top_n=top_n)

    # 2. 导出 SQL（可选）
    sql_user = None
    sql_movie = None
    if enable_sql:
        if csv_user:
            sql_user = generate_sql_from_csv(csv_user, 'user')
        if csv_movie:
            sql_movie = generate_sql_from_csv(csv_movie, 'movie')

    # 3. 导出 JSON（可选）
    if enable_json:
        export_caches_to_qdrant_json(svd_model, item_cf_model, top_n=top_n)

    # 打印导入指引
    print("\n" + "=" * 60)
    print("  导入指引")
    print("=" * 60)
    if csv_user:
        print(f"\n  users_recommendations 表导入方式:")
        print(f"    CSV: {csv_user}")
        if sql_user:
            print(f"    SQL: {sql_user}")
    if csv_movie:
        print(f"\n  movies_similarities 表导入方式:")
        print(f"    CSV: {csv_movie}")
        if sql_movie:
            print(f"    SQL: {sql_movie}")
    print(f"""
  MySQL LOAD DATA 命令:
    LOAD DATA LOCAL INFILE '{csv_user.replace('\\\\', '/') if csv_user else ''}'
    REPLACE INTO TABLE users_recommendations
    FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\\\\n'
    (user_id, recommend_movies, algorithm, updated_at);

    LOAD DATA LOCAL INFILE '{csv_movie.replace('\\\\', '/') if csv_movie else ''}'
    REPLACE INTO TABLE movies_similarities
    FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\\\\n'
    (movie_id, similar_movies, updated_at);

  MySQL SQL 文件导入:
    mysql -u root -p MovieRecommendSystem < {sql_user.replace('\\\\', '/') if sql_user else ''}
    mysql -u root -p MovieRecommendSystem < {sql_movie.replace('\\\\', '/') if sql_movie else ''}

  save_to_cache.py 导入 JSON:
    python scripts/recommend/save_to_cache.py --batch-user export/users_recommendations.json
    python scripts/recommend/save_to_cache.py --input export/movies_similarities.json --mode movie
""")

    return csv_user, csv_movie


# ============================================================
# 7. 主训练流程
# ============================================================

def main():
    """主训练函数"""
    global N_CPUS

    import argparse

    parser = argparse.ArgumentParser(
        description='MovieLens 推荐系统 - 模型训练与缓存导出'
    )
    parser.add_argument('--top-n', type=int, default=20,
                        help='每用户/每电影的推荐数量 (默认: 20)')
    parser.add_argument('--no-sql', action='store_true',
                        help='不生成 SQL 文件')
    parser.add_argument('--no-json', action='store_true',
                        help='不生成 JSON 文件')
    parser.add_argument('--export-only', action='store_true',
                        help='仅从已有模型导出缓存，不重新训练')
    parser.add_argument('--import-db', action='store_true',
                        help='导出完成后自动将结果导入 MySQL（调用 import_recommendations.js）')
    parser.add_argument('--parallel', type=int, default=N_CPUS,
                        help=f'并行进程数 (默认: {N_CPUS})')
    args = parser.parse_args()

    N_CPUS = min(args.parallel, os.cpu_count() or 1)
    print(f"  并行进程数: {N_CPUS}")

    print("=" * 60)
    print("        MovieLens 推荐系统 - 模型训练 (CPU 优化版)")
    print("=" * 60)

    if args.export_only:
        # 仅从已有模型导出
        print("\n[仅导出模式] 从已有模型导出缓存数据...")
        try:
            from export_recommendations import load_model
            svd_model = load_model('svd')
            item_cf_model = load_model('item_cf')
            print("\n模型加载成功，开始导出缓存...")
            export_all_caches(
                svd_model, item_cf_model,
                top_n=args.top_n,
                enable_sql=not args.no_sql,
                enable_json=not args.no_json
            )
            print("\n✅ 导出完成！")
        except Exception as e:
            print(f"\n❌ 导出失败: {e}")
            print("请先训练模型: python train_recommend.py")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    # 1. 加载数据
    t0 = time.time()
    ratings_df, movies_df, user2idx, movie2idx, idx2user, idx2movie = load_data()
    print(f"  数据加载耗时: {time.time() - t0:.2f} 秒")

    # 2. 划分训练/测试集
    train_df, test_df = train_test_split(ratings_df, test_ratio=0.2)

    # 3. 训练三种模型
    models_info = []

    # 3a. SVD 矩阵分解
    print("\n" + "-" * 60)
    t0 = time.time()
    svd_model = train_svd(train_df, n_factors=50, test_df=test_df)
    print(f"  SVD 总耗时: {time.time() - t0:.2f} 秒")
    save_model(svd_model, 'svd_model')
    models_info.append({
        'name': 'svd_model',
        'algorithm': 'svd',
        'n_factors': svd_model['n_factors'],
        'train_rmse': svd_model['train_rmse'],
        'test_rmse': svd_model['test_rmse'],
        'train_time': svd_model['train_time'],
    })

    # 3b. User-Based CF
    print("\n" + "-" * 60)
    t0 = time.time()
    user_cf_model = train_user_cf(train_df, n_neighbors=30, test_df=test_df)
    print(f"  User-CF 总耗时: {time.time() - t0:.2f} 秒")
    save_model(user_cf_model, 'user_cf_model')
    models_info.append({
        'name': 'user_cf_model',
        'algorithm': 'user_cf',
        'n_neighbors': user_cf_model['n_neighbors'],
        'train_rmse': user_cf_model['train_rmse'],
        'test_rmse': user_cf_model['test_rmse'],
        'train_time': user_cf_model['train_time'],
    })

    # 3c. Item-Based CF
    print("\n" + "-" * 60)
    t0 = time.time()
    item_cf_model = train_item_cf(train_df, n_neighbors=30, test_df=test_df)
    print(f"  Item-CF 总耗时: {time.time() - t0:.2f} 秒")
    save_model(item_cf_model, 'item_cf_model')
    models_info.append({
        'name': 'item_cf_model',
        'algorithm': 'item_cf',
        'n_neighbors': item_cf_model['n_neighbors'],
        'train_rmse': item_cf_model['train_rmse'],
        'test_rmse': item_cf_model['test_rmse'],
        'train_time': item_cf_model['train_time'],
    })

    # 4. 保存元数据
    save_metadata(models_info, train_df, test_df)

    # 5. 模型训练结果汇总
    print("\n" + "=" * 60)
    print("                    训练完成！")
    print("=" * 60)
    print(f"{'算法':<20} {'训练RMSE':<12} {'测试RMSE':<12} {'耗时(秒)':<10}")
    print("-" * 60)
    for info in models_info:
        train_r = f"{info['train_rmse']:.4f}" if info['train_rmse'] else 'N/A'
        test_r = f"{info['test_rmse']:.4f}" if info['test_rmse'] else 'N/A'
        time_s = f"{info['train_time']:.1f}"
        print(f"{info['algorithm']:<20} {train_r:<12} {test_r:<12} {time_s:<10}")
    print("=" * 60)
    print(f"模型已保存至: {MODEL_DIR}")

    # ====== 6. 自动导出缓存数据 ======
    print("\n\n")
    print("=" * 60)
    print("  自动导出缓存数据（MySQL/Qdrant 可导入格式）")
    print("=" * 60)

    export_all_caches(
        svd_model, item_cf_model,
        top_n=args.top_n,
        enable_sql=not args.no_sql,
        enable_json=not args.no_json
    )

    print("\n" + "=" * 60)
    print("  全部完成！")
    print("=" * 60)
    print(f"  模型目录: {MODEL_DIR}")
    print(f"  导出目录: {EXPORT_DIR}")

    # ====== 7. 可选：自动导入 MySQL ======
    if args.import_db:
        print("\n" + "=" * 60)
        print("  自动导入 MySQL ...")
        print("=" * 60)
        _auto_import_to_mysql()


def _auto_import_to_mysql():
    """调用 import_to_mysql.py 将 CSV 导入 MySQL"""
    import subprocess
    import sys

    import_script = os.path.join(BASE_DIR, 'scripts', 'import_to_mysql.py')

    if not os.path.exists(import_script):
        print(f"  ❌ 导入脚本不存在: {import_script}")
        print("  请确认 scripts/import_to_mysql.py 已创建")
        print("  可手动创建或从模板生成")
        return

    print(f"  执行: python {import_script}")
    print()

    try:
        result = subprocess.run(
            [sys.executable, import_script],
            capture_output=False,
            check=False
        )
        if result.returncode == 0:
            print("\n  ✅ MySQL 导入完成！")
        else:
            print(f"\n  ⚠️ MySQL 导入未完全成功 (exit code: {result.returncode})")
            print("  可手动执行以下命令查看详细错误:")
            print(f"    python {import_script}")
    except Exception as e:
        print(f"  ❌ 导入过程中出错: {e}")


if __name__ == '__main__':
    main()