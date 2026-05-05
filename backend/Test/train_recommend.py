#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_recommend.py - 推荐算法训练脚本

训练三种推荐算法：
1. SVD (Singular Value Decomposition) 矩阵分解
2. User-Based Collaborative Filtering
3. Item-Based Collaborative Filtering

数据来源: backend/Test/extract_test_subset_test/
模型输出: backend/Test/models/
"""

import os
import sys
import pickle
import json
import time
import math
import random
import numpy as np
from collections import defaultdict
from datetime import datetime

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')

# 确保模型目录存在
os.makedirs(MODEL_DIR, exist_ok=True)


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
    user_ratings = defaultdict(dict)  # user_id -> {movie_id: rating}
    movie_users = defaultdict(set)    # movie_id -> set of user_ids

    for _, row in train_df.iterrows():
        user_ratings[row['user_id']][row['movie_id']] = row['rating']
        movie_users[row['movie_id']].add(row['user_id'])

    # 计算每个用户的平均评分
    user_mean_rating = {}
    for uid, ratings in user_ratings.items():
        user_mean_rating[uid] = np.mean(list(ratings.values()))

    print(f"  用户平均分计算完成, 共 {len(user_mean_rating)} 个用户")

    # ---------- 计算用户相似度矩阵 (Pearson) ----------
    print("  计算用户相似度矩阵...")
    user_ids = list(user_ratings.keys())
    user_sim_matrix = defaultdict(dict)  # uid1 -> {uid2: similarity}

    # 只计算有共同评分电影的用户对
    common_users = defaultdict(set)  # (uid1, uid2) -> common movies
    for mid, uids in movie_users.items():
        uids_list = list(uids)
        for i in range(len(uids_list)):
            for j in range(i + 1, len(uids_list)):
                uid1, uid2 = uids_list[i], uids_list[j]
                if uid1 < uid2:
                    common_users[(uid1, uid2)].add(mid)
                else:
                    common_users[(uid2, uid1)].add(mid)

    print(f"  有共同评分的用户对数: {len(common_users)}")

    pair_count = 0
    for (uid1, uid2), common_movies in common_users.items():
        if len(common_movies) < 3:
            continue  # 共同评分太少，不足以计算相似度

        # Pearson 相关系数
        r1 = [user_ratings[uid1][m] for m in common_movies]
        r2 = [user_ratings[uid2][m] for m in common_movies]
        mean1 = np.mean(r1)
        mean2 = np.mean(r2)

        num = sum((r1[i] - mean1) * (r2[i] - mean2) for i in range(len(r1)))
        den1 = math.sqrt(sum((r1[i] - mean1) ** 2 for i in range(len(r1))))
        den2 = math.sqrt(sum((r2[i] - mean2) ** 2 for i in range(len(r2))))

        if den1 > 0 and den2 > 0:
            sim = num / (den1 * den2)
            if sim > 0:  # 只保留正相关
                user_sim_matrix[uid1][uid2] = sim
                user_sim_matrix[uid2][uid1] = sim
                pair_count += 1

    print(f"  有效相似度用户对: {pair_count}")

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
# 6. 主训练流程
# ============================================================

def main():
    """主训练函数"""
    print("=" * 60)
    print("        MovieLens 推荐系统 - 模型训练")
    print("=" * 60)

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

    # 5. 结果汇总
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


if __name__ == '__main__':
    main()