#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_recommend.py - 推荐算法训练脚本

训练三种推荐算法：
1. SVD (Singular Value Decomposition) 矩阵分解
2. User-Based Collaborative Filtering
3. Item-Based Collaborative Filtering

训练完成后自动将推荐结果导出为 MySQL 可导入的 CSV/SQL 文件，
以及 Qdrant 可导入的数据格式。

数据来源: scripts/extract_test_subset_test/
模型输出: scripts/models/
缓存导出: scripts/export/  (可供 MySQL LOAD DATA / Qdrant 导入)
"""

import os
import sys
import pickle
import json
import time
import math
import random
import csv
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


# ============================================================
# 1. 数据加载与预处理
# ============================================================

def load_data():
    """加载评分数据和电影信息"""
    print("=" * 60)
    print("[加载数据] 读取评分数据和电影信息...")

    import pandas as pd

    # 加载评分数据
    ratings_df = pd.read_csv(os.path.join(DATA_DIR, 'test_ratings.csv'))
    print(f"  评分数据: {len(ratings_df)} 条, "
          f"用户 {ratings_df['user_id'].nunique()} 个, "
          f"电影 {ratings_df['movie_id'].nunique()} 部")

    # 加载电影信息
    movies_df = pd.read_csv(os.path.join(DATA_DIR, 'test_movies.csv'))
    print(f"  电影信息: {len(movies_df)} 部电影")

    # 用户映射: 原始 user_id -> 0-based 连续索引
    unique_users = sorted(ratings_df['user_id'].unique())
    unique_movies = sorted(ratings_df['movie_id'].unique())

    user2idx = {uid: i for i, uid in enumerate(unique_users)}
    movie2idx = {mid: i for i, mid in enumerate(unique_movies)}
    idx2user = {i: uid for uid, i in user2idx.items()}
    idx2movie = {i: mid for mid, i in movie2idx.items()}

    print(f"  用户映射: {len(user2idx)} 个, 电影映射: {len(movie2idx)} 个")

    return ratings_df, movies_df, user2idx, movie2idx, idx2user, idx2movie


def train_test_split(ratings_df, test_ratio=0.2, random_state=42):
    """按用户划分训练集和测试集，确保每个用户在训练集中至少有一条记录"""
    print(f"\n[数据划分] 测试集比例: {test_ratio}")

    random.seed(random_state)
    train_data = []
    test_data = []

    for user_id, group in ratings_df.groupby('user_id'):
        group = group.reset_index(drop=True)
        indices = list(range(len(group)))
        random.shuffle(indices)

        # 至少保留一条给训练集
        n_test = max(1, int(len(group) * test_ratio))
        if n_test >= len(group):
            n_test = len(group) - 1

        test_indices = set(indices[:n_test])
        for i, row in group.iterrows():
            if i in test_indices:
                test_data.append(row)
            else:
                train_data.append(row)

    import pandas as pd
    train_df = pd.DataFrame(train_data, columns=ratings_df.columns)
    test_df = pd.DataFrame(test_data, columns=ratings_df.columns)

    print(f"  训练集: {len(train_df)} 条")
    print(f"  测试集: {len(test_df)} 条")
    print(f"  训练集用户: {train_df['user_id'].nunique()}, "
          f"测试集用户: {test_df['user_id'].nunique()}")

    return train_df, test_df


def build_rating_matrix(train_df, n_users, n_movies, user2idx, movie2idx):
    """构建稀疏评分矩阵 (用户×电影)"""
    print("\n[构建矩阵] 构建评分矩阵...")
    matrix = np.zeros((n_users, n_movies), dtype=np.float32)
    global_mean = train_df['rating'].mean()
    matrix.fill(global_mean)  # 用全局均值填充

    count = 0
    for _, row in train_df.iterrows():
        u = user2idx[row['user_id']]
        m = movie2idx[row['movie_id']]
        matrix[u, m] = row['rating']
        count += 1

    print(f"  矩阵大小: {n_users} × {n_movies}")
    print(f"  填充元素: {count} / {n_users * n_movies} ({100 * count / (n_users * n_movies):.4f}%)")
    print(f"  全局评分均值: {global_mean:.4f}")

    return matrix, global_mean


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

    # 构建评分矩阵
    all_users = sorted(set(train_df['user_id'].unique()))
    all_movies = sorted(set(train_df['movie_id'].unique()))

    user2idx = {uid: i for i, uid in enumerate(all_users)}
    movie2idx = {mid: i for i, mid in enumerate(all_movies)}
    idx2user = {i: uid for uid, i in user2idx.items()}
    idx2movie = {i: mid for mid, i in movie2idx.items()}

    n_users = len(all_users)
    n_movies = len(all_movies)

    matrix, global_mean = build_rating_matrix(train_df, n_users, n_movies,
                                               user2idx, movie2idx)

    # 均值中心化
    user_means = np.zeros(n_users)
    user_counts = np.zeros(n_users)
    for _, row in train_df.iterrows():
        u = user2idx[row['user_id']]
        user_means[u] += row['rating']
        user_counts[u] += 1
    for u in range(n_users):
        if user_counts[u] > 0:
            user_means[u] /= user_counts[u]

    # 去均值
    centered = matrix.copy()
    for u in range(n_users):
        if user_counts[u] > 0:
            centered[u, :] -= user_means[u]
    # 保留未评分位置的均值为0 (去均值后自然为0)
    for _, row in train_df.iterrows():
        u = user2idx[row['user_id']]
        m = movie2idx[row['movie_id']]
        centered[u, m] = matrix[u, m] - user_means[u]

    # 使用 truncated SVD
    print(f"  运行 Truncated SVD (因子数={n_factors})...")
    from scipy.sparse.linalg import svds
    from scipy.sparse import csr_matrix

    # 转为稀疏矩阵加速
    sparse_centered = csr_matrix(centered)
    u_svd, s_svd, vt_svd = svds(sparse_centered, k=min(n_factors, min(n_users, n_movies) - 1))

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

    # ---------- 计算训练 RMSE ----------
    train_rmse = _compute_svd_rmse(train_df, user_features, movie_features,
                                   user2idx, movie2idx, user_means, global_mean)
    print(f"  训练集 RMSE: {train_rmse:.4f}")

    # ---------- 计算测试 RMSE (如果有测试集) ----------
    test_rmse = None
    if test_df is not None:
        test_rmse = _compute_svd_rmse(test_df, user_features, movie_features,
                                       user2idx, movie2idx, user_means, global_mean)
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


def _compute_svd_rmse(df, user_features, movie_features, user2idx, movie2idx,
                      user_means, global_mean):
    """计算 SVD 预测的 RMSE"""
    errors = []
    for _, row in df.iterrows():
        u = user2idx.get(row['user_id'])
        m = movie2idx.get(row['movie_id'])
        if u is None or m is None:
            continue
        pred = np.dot(user_features[u], movie_features[m]) + user_means[u]
        true_rating = row['rating']
        errors.append((pred - true_rating) ** 2)
    return math.sqrt(np.mean(errors)) if errors else float('inf')


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

    all_users = sorted(set(train_df['user_id'].unique()))
    all_movies = sorted(set(train_df['movie_id'].unique()))

    user2idx = {uid: i for i, uid in enumerate(all_users)}
    movie2idx = {mid: i for i, mid in enumerate(all_movies)}
    idx2user = {i: uid for uid, i in user2idx.items()}
    idx2movie = {i: mid for mid, i in movie2idx.items()}

    n_users = len(all_users)
    n_movies = len(all_movies)

    # 构建用户-电影评分字典
    import pandas as pd
    user_ratings = defaultdict(dict)  # user_id -> {movie_id: rating}

    for _, row in train_df.iterrows():
        user_ratings[row['user_id']][row['movie_id']] = row['rating']

    # 计算每个用户的平均评分
    user_mean_rating = {}
    for uid, ratings in user_ratings.items():
        user_mean_rating[uid] = np.mean(list(ratings.values()))

    print(f"  用户平均分计算完成, 共 {len(user_mean_rating)} 个用户")

    # ---------- 计算用户相似度矩阵 (Pearson) 优化版 ----------
    print("  [优化版] 正在构建 User-Movie 评分矩阵...")
    start_sim_time = time.time()

    # 1. 利用 pandas 直接透视出 用户-电影 矩阵 (未评分的地方会自动填充 NaN)
    # 这一步非常快，行是 user_id，列是 movie_id
    user_movie_matrix = train_df.pivot(index='user_id', columns='movie_id', values='rating')

    print("  [优化版] 正在计算 Pearson 相似度矩阵 (向量化计算)...")
    # 2. 直接调用 pandas 的 .corr() 方法，按行(需要转置.T)计算 Pearson 相关系数。
    # 它会自动忽略 NaN，只计算两个用户共同评分的电影！(底层是优化的 C 语言代码)
    sim_df = user_movie_matrix.T.corr(method='pearson', min_periods=5)
    # 注意：min_periods=5 表示只有两个用户共同评分过至少 5 部电影，才计算相关系数，否则填 NaN。

    # 3. 将 Pandas DataFrame 转换回原来代码需要的字典格式
    user_sim_matrix = defaultdict(dict)
    pair_count = 0

    # 将矩阵中有效的值提取出来
    sim_stacked = sim_df.stack()
    for (uid1, uid2), sim in sim_stacked.items():
        if uid1 != uid2 and sim > 0:  # 排除自己和负相关
            user_sim_matrix[uid1][uid2] = float(sim)
            pair_count += 1

    # 因为是对称矩阵，pair_count 会计算两遍，真实对数除以 2
    print(f"  有效相似度用户对: {pair_count // 2}")
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
        'all_users': all_users,
        'all_movies': all_movies,
        'train_rmse': train_rmse,
        'test_rmse': test_rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }

    return model


def _compute_user_cf_rmse(df, user_ratings, user_sim_matrix,
                          user_mean_rating, n_neighbors):
    """计算 User-CF 预测的 RMSE"""
    errors = []
    for _, row in df.iterrows():
        uid, mid, true_rating = row['user_id'], row['movie_id'], row['rating']
        pred = _predict_user_cf(uid, mid, user_ratings, user_sim_matrix,
                                user_mean_rating, n_neighbors)
        if pred is not None:
            errors.append((pred - true_rating) ** 2)
    return math.sqrt(np.mean(errors)) if errors else float('inf')


def _predict_user_cf(uid, mid, user_ratings, user_sim_matrix,
                     user_mean_rating, n_neighbors):
    """User-CF 单条预测"""
    if uid not in user_ratings:
        return user_mean_rating.get(uid, 3.5)

    # 获取邻居
    sim_users = user_sim_matrix.get(uid, {})
    if not sim_users:
        return user_mean_rating.get(uid, 3.5)

    # 过滤出对该电影有评分的邻居
    neighbors = []
    for nuid, sim in sim_users.items():
        if mid in user_ratings.get(nuid, {}):
            neighbors.append((nuid, sim, user_ratings[nuid][mid]))

    if not neighbors:
        return user_mean_rating.get(uid, 3.5)

    # 按相似度排序取 Top-K
    neighbors.sort(key=lambda x: -x[1])
    neighbors = neighbors[:n_neighbors]

    # 加权平均 (去均值)
    uid_mean = user_mean_rating.get(uid, 3.5)
    num = 0.0
    den = 0.0
    for nuid, sim, rating in neighbors:
        n_mean = user_mean_rating.get(nuid, 3.5)
        num += sim * (rating - n_mean)
        den += abs(sim)

    if den > 0:
        return uid_mean + num / den
    return uid_mean


# ============================================================
# 4. Item-Based Collaborative Filtering
# ============================================================

def train_item_cf(train_df, n_neighbors=30, test_df=None):
    """
    训练 Item-Based Collaborative Filtering

    原理: 对于目标用户已评分的每部电影，
          找到与它最相似的 K 部电影，
          用相似度和评分的加权平均作为预测
    相似度: Cosine 相似度
    """
    print("\n" + "=" * 60)
    print(f"[Item-CF 训练] 邻居数: {n_neighbors}")

    start_time = time.time()

    all_users = sorted(set(train_df['user_id'].unique()))
    all_movies = sorted(set(train_df['movie_id'].unique()))

    user2idx = {uid: i for i, uid in enumerate(all_users)}
    movie2idx = {mid: i for i, mid in enumerate(all_movies)}
    idx2user = {i: uid for uid, i in user2idx.items()}
    idx2movie = {i: mid for mid, i in movie2idx.items()}

    # 构建电影-用户评分矩阵 (用于计算电影相似度)
    movie_ratings = defaultdict(dict)  # movie_id -> {user_id: rating}
    user_movies = defaultdict(set)     # user_id -> set of movie_ids

    for _, row in train_df.iterrows():
        movie_ratings[row['movie_id']][row['user_id']] = row['rating']
        user_movies[row['user_id']].add(row['movie_id'])

    # 计算每部电影的评分均值
    movie_mean_rating = {}
    for mid, ratings in movie_ratings.items():
        movie_mean_rating[mid] = np.mean(list(ratings.values()))

    print(f"  电影平均分计算完成, 共 {len(movie_mean_rating)} 部电影")

    # ---------- 计算电影相似度矩阵 (Cosine) ----------
    print("  计算电影相似度矩阵...")
    movie_ids = list(movie_ratings.keys())

    # 只计算有共同评分用户的电影对
    common_movies = defaultdict(set)  # (mid1, mid2) -> common users
    for uid, mids in user_movies.items():
        mids_list = list(mids)
        for i in range(len(mids_list)):
            for j in range(i + 1, len(mids_list)):
                mid1, mid2 = mids_list[i], mids_list[j]
                if mid1 < mid2:
                    common_movies[(mid1, mid2)].add(uid)
                else:
                    common_movies[(mid2, mid1)].add(uid)

    print(f"  有共同评分用户的电影对数: {len(common_movies)}")

    movie_sim_matrix = defaultdict(dict)
    pair_count = 0

    for (mid1, mid2), common_users_set in common_movies.items():
        if len(common_users_set) < 3:
            continue

        # Cosine 相似度（去均值）
        r1 = [movie_ratings[mid1][u] for u in common_users_set]
        r2 = [movie_ratings[mid2][u] for u in common_users_set]
        mean1 = np.mean(r1)
        mean2 = np.mean(r2)

        r1_centered = [r - mean1 for r in r1]
        r2_centered = [r - mean2 for r in r2]

        dot = sum(a * b for a, b in zip(r1_centered, r2_centered))
        norm1 = math.sqrt(sum(a * a for a in r1_centered))
        norm2 = math.sqrt(sum(b * b for b in r2_centered))

        if norm1 > 0 and norm2 > 0:
            sim = dot / (norm1 * norm2)
            if sim > 0:
                movie_sim_matrix[mid1][mid2] = sim
                movie_sim_matrix[mid2][mid1] = sim
                pair_count += 1

    print(f"  有效相似度电影对: {pair_count}")

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
        'all_users': all_users,
        'all_movies': all_movies,
        'train_rmse': train_rmse,
        'test_rmse': test_rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
    }

    return model


def _compute_item_cf_rmse(df, movie_ratings, movie_sim_matrix,
                          movie_mean_rating, user_movies, n_neighbors):
    errors = []
    for _, row in df.iterrows():
        uid, mid, true_rating = row['user_id'], row['movie_id'], row['rating']
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
            if sim > 0 and rmid in movie_ratings:
                rating = movie_ratings[rmid].get(uid)
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

    # SVD 的 numpy 数组需要特殊处理
    if model['algorithm'] == 'svd':
        model_copy = model.copy()
        with open(filepath, 'wb') as f:
            pickle.dump(model_copy, f)
    else:
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
            'n_users': train_df['user_id'].nunique(),
            'n_movies': train_df['movie_id'].nunique(),
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

def export_users_recommendations_csv(svd_model, item_cf_model=None, top_n=20):
    """
    使用 SVD 模型为所有训练用户生成 Top-N 推荐，导出为 CSV。
    输出格式对应 MySQL users_recommendations 表:
      user_id, recommend_movies(JSON), algorithm, updated_at
    """
    print("\n" + "=" * 60)
    print("[缓存导出] 用户推荐 -> users_recommendations.csv")
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

    # 获取用户已评分的电影列表（用于排除已评分项）
    user_rated_movies = defaultdict(set)
    if item_cf_model and 'user_movies' in item_cf_model:
        for uid, mids in item_cf_model['user_movies'].items():
            user_rated_movies[int(uid)] = set(int(m) for m in mids)
        print(f"  已加载用户评分记录: {len(user_rated_movies)} 个用户")

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    algorithm_tag = 'svd'

    # 预计算所有电影的特征向量
    movie_ids = []
    movie_vectors = []
    for mid, m_idx in movie2idx.items():
        movie_ids.append(int(mid))
        movie_vectors.append(movie_features[m_idx])
    movie_vectors = np.array(movie_vectors)

    csv_path = os.path.join(EXPORT_DIR, 'users_recommendations.csv')
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

                # 取 Top-N
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

                json_str = json.dumps(rec_list, ensure_ascii=False)
                writer.writerow([int(uid), json_str, algorithm_tag, current_time])
                processed += 1

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  [警告] 用户 {uid} 处理失败: {e}")

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


def export_movies_similarities_csv(item_cf_model, top_n=20):
    """
    从 Item-CF 模型的 movie_sim_matrix 导出每部电影的 Top-N 相似电影。
    输出格式对应 MySQL movies_similarities 表:
      movie_id, similar_movies(JSON), updated_at
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

                sim_list = [
                    {"movie_id": int(sim_mid), "score": round(float(score), 4)}
                    for sim_mid, score in sorted_sims
                ]

                json_str = json.dumps(sim_list, ensure_ascii=False)
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
                # movies_similarities: movie_id, similar_movies, updated_at
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
    生成两个文件：
      - users_recommendations.json（用户推荐）
      - movies_similarities.json（电影相似度）
    """
    print("\n" + "=" * 60)
    print("[缓存导出] 推荐数据 -> JSON (供 Qdrant/save_to_cache 使用)")
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

    movie_ids = []
    movie_vectors = []
    for mid, m_idx in movie2idx.items():
        movie_ids.append(int(mid))
        movie_vectors.append(movie_features[m_idx])
    movie_vectors = np.array(movie_vectors)

    user_records = []
    for uid in sorted(user2idx.keys()):
        u_idx = user2idx[uid]
        user_mean = user_means[u_idx]
        scores = np.dot(user_features[u_idx], movie_vectors.T) + user_mean

        rated = user_rated_movies.get(int(uid), set())
        if rated:
            valid_indices = [i for i, mid in enumerate(movie_ids) if mid not in rated]
            if valid_indices:
                filtered_scores = scores[valid_indices]
                filtered_mids = [movie_ids[i] for i in valid_indices]
            else:
                filtered_scores = scores
                filtered_mids = movie_ids
        else:
            filtered_scores = scores
            filtered_mids = movie_ids

        if len(filtered_scores) > top_n:
            top_indices = np.argpartition(filtered_scores, -top_n)[-top_n:]
            top_indices = top_indices[np.argsort(-filtered_scores[top_indices])]
        else:
            top_indices = np.argsort(-filtered_scores)

        rec_list = [
            {"movie_id": int(filtered_mids[idx]), "score": round(float(filtered_scores[idx]), 4)}
            for idx in top_indices
        ]
        user_records.append({
            "user_id": int(uid),
            "recommendations": rec_list,
            "algorithm": "svd"
        })

    user_json_path = os.path.join(EXPORT_DIR, 'users_recommendations.json')
    with open(user_json_path, 'w', encoding='utf-8') as f:
        json.dump(user_records, f, ensure_ascii=False, indent=2)
    print(f"  用户推荐 JSON: {user_json_path} ({len(user_records)} 个用户)")

    # ---- 导出电影相似度 JSON ----
    if item_cf_model and item_cf_model.get('movie_sim_matrix'):
        movie_sim_matrix = item_cf_model['movie_sim_matrix']
        movie_sim_matrix_int = {}
        for k, v in movie_sim_matrix.items():
            movie_sim_matrix_int[int(k)] = {
                int(sk): float(sv) for sk, sv in v.items()
            }
        movie_sim_matrix = movie_sim_matrix_int

        movie_records = []
        for mid in sorted(movie_sim_matrix.keys()):
            sim_movies = movie_sim_matrix[mid]
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
    args = parser.parse_args()

    print("=" * 60)
    print("        MovieLens 推荐系统 - 模型训练")
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
    ratings_df, movies_df, user2idx, movie2idx, idx2user, idx2movie = load_data()

    # 2. 划分训练/测试集
    train_df, test_df = train_test_split(ratings_df, test_ratio=0.2)

    # 3. 训练三种模型
    models_info = []

    # 3a. SVD 矩阵分解
    print("\n" + "-" * 60)
    svd_model = train_svd(train_df, n_factors=50, test_df=test_df)
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
    user_cf_model = train_user_cf(train_df, n_neighbors=30, test_df=test_df)
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
    item_cf_model = train_item_cf(train_df, n_neighbors=30, test_df=test_df)
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
