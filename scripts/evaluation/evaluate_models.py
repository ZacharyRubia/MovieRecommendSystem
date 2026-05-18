#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluate_models.py - 推荐系统全模型评估脚本

实现/调用以下 8 个模型，并对测试集进行预测和评估：
  1. 传统 User-CF（Pearson 相似度 + 等权平均）
  2. 改进 User-CF（公式 3-1 + 稳定性因子 3-3）
  3. 传统 Item-CF（去均值余弦相似度 + 等权平均）
  4. 改进 Item-CF（公式 3-2 加权平均）
  5. 传统 Slope One（全局偏差 公式 3-4）
  6. 改进 Slope One（邻域筛选 + 局域偏差 公式 3-5, 3-6）
  7. SVD（TruncatedSVD n_components=50）
  8. 混合推荐（公式 3-7，先归一化各模型输出）

评估指标：
  - RMSE, MAE
  - Precision@10, Recall@10, F1@10
  - Coverage（物品覆盖率）

用法:
  python evaluate_models.py
  python evaluate_models.py --test-size 5000    # 限制测试样本数（调试用）
"""

import os
import sys
import pickle
import json
import math
import time
import argparse
import warnings
import numpy as np
import pandas as pd
from collections import defaultdict
from scipy.sparse import csr_matrix, lil_matrix
from sklearn.decomposition import TruncatedSVD
from sklearn.neighbors import NearestNeighbors

warnings.filterwarnings('ignore', category=RuntimeWarning)
warnings.filterwarnings('ignore', category=UserWarning)

# ─── 路径 ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
OUTPUT_DIR = os.path.join(BASE_DIR, 'evaluation_results')
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 1. 数据加载
# ═══════════════════════════════════════════════════════════════

def load_all_data():
    """加载原始评分数据（全量用于训练，测试集用于评估）"""
    print("=" * 60)
    print("[数据加载]")

    ratings_df = pd.read_csv(
        os.path.join(DATA_DIR, 'test_ratings.csv'),
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )

    # 读电影信息
    movies_df = None
    movies_path = os.path.join(DATA_DIR, 'test_movies.csv')
    if os.path.exists(movies_path):
        movies_df = pd.read_csv(movies_path)

    print(f"  评分: {len(ratings_df)} 条, "
          f"用户: {ratings_df['user_id'].nunique()}, "
          f"电影: {ratings_df['movie_id'].nunique()}")
    return ratings_df, movies_df


def train_test_split_by_user(ratings_df, test_ratio=0.2, random_state=42):
    """按用户划分训练/测试集（确保每个用户在训练集中至少有一条评分）"""
    print(f"\n[数据划分] 测试比例: {test_ratio}")
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

    print(f"  训练集: {len(train_df)} 条, 用户: {train_df['user_id'].nunique()}")
    print(f"  测试集: {len(test_df)} 条, 用户: {test_df['user_id'].nunique()}")
    return train_df, test_df


def build_rating_matrices(train_df):
    """构建评分数据矩阵和映射，供所有模型使用"""
    all_users = np.sort(train_df['user_id'].unique())
    all_movies = np.sort(train_df['movie_id'].unique())
    user2idx = {int(uid): i for i, uid in enumerate(all_users)}
    movie2idx = {int(mid): i for i, mid in enumerate(all_movies)}
    idx2user = {i: int(uid) for uid, i in user2idx.items()}
    idx2movie = {i: int(mid) for mid, i in movie2idx.items()}
    n_users = len(all_users)
    n_movies = len(all_movies)

    u_idx = np.array([user2idx[int(uid)] for uid in train_df['user_id']], dtype=np.int32)
    m_idx = np.array([movie2idx[int(mid)] for mid in train_df['movie_id']], dtype=np.int32)
    r_val = train_df['rating'].values.astype(np.float32)

    # 完整评分矩阵（稀疏）
    rating_matrix = csr_matrix(
        (r_val, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )

    # 用户均值
    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts

    # 电影均值
    movie_means = np.zeros(n_movies, dtype=np.float64)
    movie_counts = np.zeros(n_movies, dtype=np.int64)
    np.add.at(movie_means, m_idx, r_val)
    np.add.at(movie_counts, m_idx, 1)
    movie_counts[movie_counts == 0] = 1
    movie_means = (movie_means / movie_counts).astype(np.float32)

    # 用户评分字典
    user_ratings_dict = defaultdict(dict)
    for uid, mid, rating in zip(train_df['user_id'], train_df['movie_id'], train_df['rating']):
        user_ratings_dict[int(uid)][int(mid)] = float(rating)

    # 电影评分字典
    movie_ratings_dict = defaultdict(dict)
    for uid, mid, rating in zip(train_df['user_id'], train_df['movie_id'], train_df['rating']):
        movie_ratings_dict[int(mid)][int(uid)] = float(rating)

    ctx = {
        'train_df': train_df,
        'all_users': all_users,
        'all_movies': all_movies,
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'n_users': n_users,
        'n_movies': n_movies,
        'rating_matrix': rating_matrix,
        'user_means': user_means,
        'movie_means': movie_means,
        'u_idx': u_idx,
        'm_idx': m_idx,
        'r_val': r_val,
        'user_ratings_dict': dict(user_ratings_dict),
        'movie_ratings_dict': dict(movie_ratings_dict),
    }
    print(f"  矩阵: ({n_users}, {n_movies}), 非零: {rating_matrix.nnz}")
    return ctx


# ═══════════════════════════════════════════════════════════════
# 2. 模型定义
# ═══════════════════════════════════════════════════════════════

class BaseModel:
    """模型基类"""
    def __init__(self, name):
        self.name = name
        self.trained = False

    def train(self, ctx):
        raise NotImplementedError

    def predict(self, user_id, movie_id):
        """预测单个 (user, movie) 的评分，返回 float"""
        raise NotImplementedError

    def predict_batch(self, user_ids, movie_ids):
        """批量预测，返回 numpy 数组"""
        return np.array([self.predict(uid, mid) for uid, mid in zip(user_ids, movie_ids)])


# ─── 2.1 传统 User-CF：Pearson 相似度 + 等权平均 ──────────────

class TraditionalUserCF(BaseModel):
    """
    传统 User-Based CF
    - 相似度: Pearson 相关系数
    - 预测: 等权平均（取 Top-K 邻居的评分均值偏差）
    """
    def __init__(self, n_neighbors=30, min_sim=0.0):
        super().__init__('traditional_user_cf')
        self.n_neighbors = n_neighbors
        self.min_sim = min_sim

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练: 计算 Pearson 相似度...")
        t0 = time.time()

        self.ctx = ctx
        n_users = ctx['n_users']
        n_movies = ctx['n_movies']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']

        # 去均值: centered = rating_matrix - user_means broadcast
        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        # 计算 top-k 相似用户
        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_users),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(centered)
        distances, indices = nn.kneighbors(centered, return_distance=True)

        # 距离转相似度
        self.user_sim_data = {}  # {uid: [(nb_uid, sim), ...]}
        for i in range(n_users):
            uid = int(ctx['all_users'][i])
            nb_list = []
            for j in range(1, min(self.n_neighbors + 1, n_users)):
                nb_uid = int(ctx['all_users'][indices[i, j]])
                sim = 1.0 - distances[i, j]  # cosine distance → similarity
                if sim > self.min_sim:
                    nb_list.append((nb_uid, sim))
            self.user_sim_data[uid] = nb_list

        self.trained = True
        print(f"  耗时: {time.time() - t0:.2f}s, 邻居: {self.n_neighbors}")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id

        neighbors = self.user_sim_data.get(uid, [])
        if not neighbors:
            return ctx['user_means'][ctx['user2idx'].get(uid, 0)]

        # 等权平均：取所有邻居评分的平均值（不去均值）
        ratings = []
        for nb_uid, sim in neighbors:
            nb_ratings = ctx['user_ratings_dict'].get(nb_uid, {})
            if mid in nb_ratings:
                ratings.append(nb_ratings[mid])

        if not ratings:
            return ctx['user_means'][ctx['user2idx'].get(uid, 0)]

        # 等权平均
        return float(np.mean(ratings))


# ─── 2.2 改进 User-CF：公式 (3-1) + 稳定性因子 (3-3) ────────

class ImprovedUserCF(BaseModel):
    """
    改进 User-Based CF
    - 公式 (3-1): 加权平均预测
        pred(u,i) = mean_u + Σ sim(u,n) * (r_ni - mean_n) / Σ |sim(u,n)|
    - 公式 (3-3): 稳定性因子（惩罚冷门邻居的贡献）
        stability(n) = 1 - 1 / (1 + |I_n|)  其中 |I_n| 是邻居的评分数量
        最终权重 = sim(u,n) * stability(n)
    """
    def __init__(self, n_neighbors=30, use_stability=True, min_sim=0.0):
        super().__init__('improved_user_cf')
        self.n_neighbors = n_neighbors
        self.use_stability = use_stability
        self.min_sim = min_sim

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练: 计算 Pearson 相似度 + 稳定性因子...")
        t0 = time.time()

        self.ctx = ctx
        n_users = ctx['n_users']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']

        # 去均值: centered = rating_matrix - user_means broadcast
        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        # 计算 Pearson 相似度（用稀疏矩阵的余弦距离）
        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_users),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(centered)
        distances, indices = nn.kneighbors(centered, return_distance=True)

        # 稳定性因子 (3-3): 用户评分越多越稳定
        # 计算每个用户的评分数量
        user_rating_counts = np.array([
            len(ctx['user_ratings_dict'].get(int(ctx['all_users'][i]), {}))
            for i in range(n_users)
        ])

        self.user_sim_data = {}
        for i in range(n_users):
            uid = int(ctx['all_users'][i])
            nb_list = []
            for j in range(1, min(self.n_neighbors + 1, n_users)):
                nb_uid = int(ctx['all_users'][indices[i, j]])
                sim = 1.0 - distances[i, j]
                if sim > self.min_sim:
                    # 稳定性因子 (3-3)
                    nb_count = user_rating_counts[indices[i, j]]
                    if self.use_stability:
                        stability = 1.0 - 1.0 / (1.0 + nb_count)
                        sim = sim * stability
                    nb_list.append((nb_uid, sim))
            self.user_sim_data[uid] = nb_list

        self.trained = True
        print(f"  耗时: {time.time() - t0:.2f}s, 稳定性因子: {self.use_stability}")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id

        neighbors = self.user_sim_data.get(uid, [])
        if not neighbors:
            return float(ctx['user_means'][ctx['user2idx'].get(uid, 0)])

        # 公式 (3-1): 加权平均
        uid_mean = float(ctx['user_means'][ctx['user2idx'].get(uid, 0)])
        num = 0.0
        den = 0.0

        for nb_uid, weight in neighbors:
            nb_ratings = ctx['user_ratings_dict'].get(nb_uid, {})
            if mid in nb_ratings:
                nb_mean = float(ctx['user_means'][ctx['user2idx'].get(nb_uid, 0)])
                num += weight * (nb_ratings[mid] - nb_mean)
                den += abs(weight)

        if den > 0:
            return float(uid_mean + num / den)
        return uid_mean


# ─── 2.3 传统 Item-CF：去均值余弦 + 等权平均 ──────────────────

class TraditionalItemCF(BaseModel):
    """
    传统 Item-Based CF
    - 相似度: 去均值余弦相似度（Adjusted Cosine）
    - 预测: 等权平均
    """
    def __init__(self, n_neighbors=30, min_sim=0.0):
        super().__init__('traditional_item_cf')
        self.n_neighbors = n_neighbors
        self.min_sim = min_sim

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练: 计算 Adjusted Cosine 相似度...")
        t0 = time.time()

        self.ctx = ctx
        n_movies = ctx['n_movies']
        n_users = ctx['n_users']
        rating_matrix = ctx['rating_matrix']
        movie_means = ctx['movie_means']

        # 电影-用户矩阵（转置评分矩阵）
        movie_user = rating_matrix.T.tocsr()  # (n_movies, n_users)

        # 去均值
        # 对每行的非零元素减去该行均值（电影均值）
        centered = movie_user.copy()
        for i in range(n_movies):
            row = centered[i]
            if row.nnz > 0:
                row.data -= movie_means[i]

        # 使用余弦相似度（去均值后 = Adjusted Cosine Similarity）
        from sklearn.metrics.pairwise import cosine_similarity
        chunk_size = 2000
        self.movie_sim_data = {}

        chunk_ranges = [(i, min(i + chunk_size, n_movies))
                        for i in range(0, n_movies, chunk_size)]

        for c_start, c_end in chunk_ranges:
            chunk = centered[c_start:c_end]
            sim_chunk = cosine_similarity(chunk, centered)
            np.maximum(sim_chunk, 0, out=sim_chunk)

            for local_i in range(c_end - c_start):
                global_i = c_start + local_i
                sim_chunk[local_i, global_i] = 0.0

                row = sim_chunk[local_i]
                mid = int(ctx['all_movies'][global_i])
                top_k = min(self.n_neighbors, n_movies - 1)

                if top_k > 0 and np.any(row > self.min_sim):
                    top_idx = np.argpartition(row, -top_k)[-top_k:]
                    top_sims = row[top_idx]
                    mask = top_sims > self.min_sim
                    self.movie_sim_data[mid] = [
                        (int(ctx['all_movies'][top_idx[k]]), float(top_sims[k]))
                        for k in np.where(mask)[0]
                    ]
                else:
                    self.movie_sim_data[mid] = []

            if (c_start // chunk_size + 1) % 2 == 0:
                print(f"    相似度进度: {c_end}/{n_movies} 电影")

        self.trained = True
        print(f"  耗时: {time.time() - t0:.2f}s, 邻居: {self.n_neighbors}")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        mid = movie_id

        neighbors = self.movie_sim_data.get(mid, [])
        if not neighbors:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        user_ratings = ctx['user_ratings_dict'].get(user_id, {})

        # 等权平均
        ratings = []
        for nb_mid, sim in neighbors:
            if nb_mid in user_ratings:
                ratings.append(user_ratings[nb_mid])

        if not ratings:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        return float(np.mean(ratings))


# ─── 2.4 改进 Item-CF：公式 (3-2) 加权平均 ────────────────────

class ImprovedItemCF(BaseModel):
    """
    改进 Item-Based CF
    - 公式 (3-2): 加权平均
        pred(u,i) = ( Σ sim(i,j) * r_uj ) / ( Σ |sim(i,j)| )
      其中 j 是用户 u 已评分的与 i 最相似的物品
    """
    def __init__(self, n_neighbors=30, min_sim=0.0):
        super().__init__('improved_item_cf')
        self.n_neighbors = n_neighbors
        self.min_sim = min_sim

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练: 计算 Adjusted Cosine + 加权平均结构...")
        t0 = time.time()

        self.ctx = ctx
        n_movies = ctx['n_movies']
        n_users = ctx['n_users']
        rating_matrix = ctx['rating_matrix']
        movie_means = ctx['movie_means']

        movie_user = rating_matrix.T.tocsr()

        centered = movie_user.copy()
        for i in range(n_movies):
            row = centered[i]
            if row.nnz > 0:
                row.data -= movie_means[i]

        from sklearn.metrics.pairwise import cosine_similarity
        chunk_size = 2000
        self.movie_sim_data = {}

        chunk_ranges = [(i, min(i + chunk_size, n_movies))
                        for i in range(0, n_movies, chunk_size)]

        for c_start, c_end in chunk_ranges:
            chunk = centered[c_start:c_end]
            sim_chunk = cosine_similarity(chunk, centered)
            np.maximum(sim_chunk, 0, out=sim_chunk)

            for local_i in range(c_end - c_start):
                global_i = c_start + local_i
                sim_chunk[local_i, global_i] = 0.0

                row = sim_chunk[local_i]
                mid = int(ctx['all_movies'][global_i])
                top_k = min(self.n_neighbors, n_movies - 1)

                if top_k > 0 and np.any(row > self.min_sim):
                    top_idx = np.argpartition(row, -top_k)[-top_k:]
                    top_sims = row[top_idx]
                    mask = top_sims > self.min_sim
                    self.movie_sim_data[mid] = [
                        (int(ctx['all_movies'][top_idx[k]]), float(top_sims[k]))
                        for k in np.where(mask)[0]
                    ]
                else:
                    self.movie_sim_data[mid] = []

            if (c_start // chunk_size + 1) % 2 == 0:
                print(f"    相似度进度: {c_end}/{n_movies} 电影")

        self.trained = True
        print(f"  耗时: {time.time() - t0:.2f}s, 邻居: {self.n_neighbors}")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        mid = movie_id

        neighbors = self.movie_sim_data.get(mid, [])
        if not neighbors:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        user_ratings = ctx['user_ratings_dict'].get(user_id, {})

        # 公式 (3-2): 加权平均
        num = 0.0
        den = 0.0
        for nb_mid, sim in neighbors:
            if nb_mid in user_ratings:
                num += sim * user_ratings[nb_mid]
                den += abs(sim)

        if den > 0:
            return float(num / den)
        return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])


# ─── 2.5 传统 Slope One：全局偏差公式 (3-4) ────────────────────

class TraditionalSlopeOne(BaseModel):
    """
    传统 Slope One
    - 公式 (3-4): 全局偏差
        dev(j,i) = (1 / |U_ji|) * Σ (r_uj - r_ui)
        pred(u,i) = (1 / |R_i|) * Σ (r_uj + dev(i,j))
      其中 R_i 是用户 u 已评分且与 i 有共同评分者同时评过的物品集合
    """
    def __init__(self):
        super().__init__('traditional_slope_one')

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练: 计算全局偏差矩阵...")
        t0 = time.time()

        self.ctx = ctx
        n_movies = ctx['n_movies']
        train_df = ctx['train_df']

        # 计算所有电影对的偏差 dev(j,i) = avg(r_uj - r_ui)
        # 使用字典存储
        # 为节省内存, 只对共现次数 >= 1 的电影对计算
        self.deviation = {}  # {(mid_j, mid_i): dev, ...}
        self.frequency = {}  # {(mid_j, mid_i): count, ...}

        # 按用户分组计算偏差
        dev_sum = defaultdict(float)
        freq = defaultdict(int)

        for uid, group in train_df.groupby('user_id'):
            movies = group['movie_id'].values
            ratings = group['rating'].values
            n = len(movies)

            if n < 2:
                continue

            for a in range(n):
                for b in range(a + 1, n):
                    mid_a = int(movies[a])
                    mid_b = int(movies[b])
                    diff = ratings[a] - ratings[b]

                    # (mid_a, mid_b)
                    key_ab = (mid_a, mid_b)
                    dev_sum[key_ab] += diff
                    freq[key_ab] += 1

                    # (mid_b, mid_a) = -diff
                    key_ba = (mid_b, mid_a)
                    dev_sum[key_ba] -= diff
                    freq[key_ba] += 1

        # 计算最终偏差
        for key, f in freq.items():
            self.deviation[key] = dev_sum[key] / f
            self.frequency[key] = f

        self.trained = True
        print(f"  电影对偏差: {len(self.deviation)} 对")
        print(f"  耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        mid = movie_id
        user_ratings = ctx['user_ratings_dict'].get(user_id, {})

        if not user_ratings:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        # 公式 (3-4): pred(u,i) = (1/|R_i|) * Σ (r_uj + dev(i,j))
        num = 0.0
        den = 0.0

        for j_mid, r_uj in user_ratings.items():
            if j_mid == mid:
                continue
            key = (mid, j_mid)
            if key in self.deviation:
                num += r_uj + self.deviation[key]
                den += 1.0

        if den > 0:
            return float(num / den)
        return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])


# ─── 2.6 改进 Slope One：邻域筛选 + 局域偏差 (3-5), (3-6) ────

class ImprovedSlopeOne(BaseModel):
    """
    改进 Slope One
    - 邻域筛选: 在 SVD 降维空间中找到用户的 Top-K 相似邻居
    - 局域偏差（预测时按需计算）:
        dev_local(i,j) = avg(r_ui - r_uj), u ∈ U_nb
        pred(u,i) = avg(r_uj + dev_local(i,j)), j ∈ R_nb
      其中 U_nb 是邻居集合，R_nb 是用户 u 已评分且邻居也评过目标物品 i 的物品
    - 按需计算策略:
        * 不预计算局域偏差表（避免 O(U·N·M²) 耗时）
        * 预测时对每个 (i, j) 对实时计算邻居共同评分偏差
        * 使用缓存避免同一用户多次预测时的重复计算
    """
    def __init__(self, n_neighbors=30, svd_factors=50, min_common=1):
        super().__init__('improved_slope_one')
        self.n_neighbors = n_neighbors
        self.svd_factors = svd_factors
        self.min_common = min_common  # 局域偏差所需最低共同评分邻居数

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练: SVD 降维 + 寻找邻居 + 全局偏差...")
        t0 = time.time()

        self.ctx = ctx
        n_users = ctx['n_users']
        n_movies = ctx['n_movies']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']
        all_users = ctx['all_users']
        train_df = ctx['train_df']

        # ── 1. SVD 降维找到用户邻居 ──
        k = min(self.svd_factors, min(n_users, n_movies) - 1)
        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        svd = TruncatedSVD(n_components=k, algorithm='randomized', random_state=42)
        user_features = svd.fit_transform(centered)

        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_users),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(user_features)
        distances, indices = nn.kneighbors(user_features, return_distance=True)

        # 构建邻居集合 U_nb
        self.user_neighbor_sets = {}  # {uid: set(nb_uids)}
        for i in range(n_users):
            uid = int(all_users[i])
            nb_set = set()
            for j in range(1, min(self.n_neighbors + 1, n_users)):
                nb_set.add(int(all_users[indices[i, j]]))
            self.user_neighbor_sets[uid] = nb_set

        print(f"  邻居集合: {len(self.user_neighbor_sets)} 个用户, 各 {self.n_neighbors} 个邻居")

        # ── 2. 预计算全局偏差（作为回退） ──
        print(f"  计算全局偏差矩阵...")
        dev_sum = defaultdict(float)
        freq = defaultdict(int)

        for uid, group in train_df.groupby('user_id'):
            movies = group['movie_id'].values
            ratings = group['rating'].values
            n = len(movies)
            if n < 2:
                continue
            for a in range(n):
                for b in range(a + 1, n):
                    mid_a = int(movies[a])
                    mid_b = int(movies[b])
                    diff = ratings[a] - ratings[b]
                    key_ab = (mid_a, mid_b)
                    dev_sum[key_ab] += diff
                    freq[key_ab] += 1
                    key_ba = (mid_b, mid_a)
                    dev_sum[key_ba] -= diff
                    freq[key_ba] += 1

        self.global_deviation = {}
        for key, f in freq.items():
            self.global_deviation[key] = dev_sum[key] / f

        # ── 3. 局域偏差不预计算，改为预测时按需生成 ──
        # 缓存：{uid: {(target_mid, j_mid): dev}}
        self._local_dev_cache = {}
        self._local_freq_cache = {}

        self.trained = True
        print(f"  全局偏差对: {len(self.global_deviation)} 对（回退用）")
        print(f"  局域偏差: 预测时按需计算（不预计算）")
        print(f"  耗时: {time.time() - t0:.2f}s")

    def _compute_local_dev(self, uid, neighbors, mid, j_mid):
        """
        实时计算单个电影对的局域偏差 dev_local(mid, j_mid)
        只遍历邻居集合，计算平均偏差差
        返回: (dev, count) 或 (None, 0)
        """
        diff_sum = 0.0
        count = 0
        for nb_uid in neighbors:
            nb_ratings = self.ctx['user_ratings_dict'].get(nb_uid, {})
            r_i = nb_ratings.get(mid)
            r_j = nb_ratings.get(j_mid)
            if r_i is not None and r_j is not None:
                diff_sum += r_i - r_j
                count += 1
        if count >= self.min_common:
            return diff_sum / count, count
        return None, count

    def _batch_local_devs(self, neighbors, mid, j_mids):
        """
        批量计算局域偏差：一次扫描所有邻居，
        收集目标电影 mid 和所有 j_mids 的评分
        返回: {(mid, j_mid): (dev, count), ...}
        """
        # 对每个 (mid, j_mid) 累积 diff 和 count
        dev_sum = defaultdict(float)
        freq = defaultdict(int)
        for nb_uid in neighbors:
            nb_ratings = self.ctx['user_ratings_dict'].get(nb_uid, {})
            r_mid = nb_ratings.get(mid)
            if r_mid is None:
                continue
            for j_mid in j_mids:
                r_j = nb_ratings.get(j_mid)
                if r_j is not None:
                    dev_sum[(mid, j_mid)] += r_mid - r_j
                    freq[(mid, j_mid)] += 1
        result = {}
        for key, f in freq.items():
            if f >= self.min_common:
                result[key] = (dev_sum[key] / f, f)
        return result

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id
        user_ratings = ctx['user_ratings_dict'].get(uid, {})

        if not user_ratings:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        # 获取该用户的邻居集合
        nb_set = self.user_neighbor_sets.get(uid, set())

        # 如果没有邻居，直接使用全局偏差（回退到传统 Slope One）
        if not nb_set:
            num = 0.0
            den = 0.0
            for j_mid, r_uj in user_ratings.items():
                if j_mid == mid:
                    continue
                key = (mid, j_mid)
                if key in self.global_deviation:
                    num += r_uj + self.global_deviation[key]
                    den += 1.0
            if den > 0:
                return float(num / den)
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        # 公式 (3-5), (3-6): 邻域筛选 + 局域偏差预测
        # 一次性扫描邻居，批量收集所有 (mid, j_mid) 对的局域偏差
        j_mid_list = [j for j in user_ratings.keys() if j != mid]
        local_devs = self._batch_local_devs(nb_set, mid, j_mid_list)

        num = 0.0
        den = 0.0

        for j_mid, r_uj in user_ratings.items():
            if j_mid == mid:
                continue
            key = (mid, j_mid)

            if key in local_devs:
                dev_val, cnt = local_devs[key]
                # 使用局域偏差
                pass  # dev_val 已就绪
            elif key in self.global_deviation:
                dev_val = self.global_deviation[key]
            else:
                continue

            num += r_uj + dev_val
            den += 1.0

        # 策略 2: 如果正向偏差覆盖不足，尝试反向 (j_mid, mid) 取 -dev
        if den < 1:
            # 反向局域偏差：直接复用已计算的 local_devs
            # dev_local(mid, j_mid) = -dev_local(j_mid, mid)
            for j_mid, r_uj in user_ratings.items():
                if j_mid == mid:
                    continue
                rev_key = (j_mid, mid)

                # 先试局域（取反）
                if rev_key in local_devs:
                    dev_val = -local_devs[rev_key][0]
                elif rev_key in self.global_deviation:
                    dev_val = -self.global_deviation[rev_key]
                else:
                    continue

                num += r_uj + dev_val
                den += 1.0

        if den > 0:
            return float(num / den)

        # 完全无可用偏差时，回退到电影均值
        return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])


# ─── 2.7 Turbo-CF（加载已有模型） ─────────────────────────────

class TurboCFModel(BaseModel):
    """
    Turbo-CF（加速协同过滤）
    - K-Means 用户聚类 + 簇内局部邻居 + 加权平均预测
    - 使用 train_turbocf.py 训练的 turbo_cf_model.pkl

    预测公式:
      pred(u,i) = mean_u + ( Σ sim(u,v) · (r_vi - mean_v) ) / Σ |sim(u,v)|
    """
    def __init__(self, model_path=None):
        super().__init__('turbo_cf')
        self.model_path = model_path

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练/加载 Turbo-CF 模型...")
        t0 = time.time()

        self.ctx = ctx

        # 从已有模型文件加载
        if self.model_path and os.path.exists(self.model_path):
            print(f"  从文件加载: {self.model_path}")
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)
            self.turbo_model = model_data
            n_clusters = model_data.get('n_clusters', '?')
            n_users = model_data.get('n_users', '?')
            nb_count = sum(len(v) for v in model_data.get('user_neighbors', {}).values())
            print(f"  簇数: {n_clusters}  |  用户: {n_users}  |  邻居总数: {nb_count}")
            self.trained = True
            print(f"  耗时: {time.time() - t0:.2f}s")
            return

        print("  [警告] Turbo-CF 模型文件不存在，回退至全量计算...")
        # 如果模型文件不存在，采用全量 User-CF（Pearson）作为回退
        from sklearn.neighbors import NearestNeighbors
        n_users = ctx['n_users']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']
        all_users = ctx['all_users']

        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        nn = NearestNeighbors(
            n_neighbors=min(31, n_users),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(centered)
        distances, indices = nn.kneighbors(centered, return_distance=True)

        # 构建简单用户邻居字典（与 train_turbocf.py 输出结构一致）
        turbo_model = {
            'algorithm': 'turbo_cf',
            'n_clusters': n_users,
            'user_neighbors': {},
            'user_means': {int(uid): float(user_means[i])
                           for i, uid in enumerate(all_users)},
            'all_users': [int(u) for u in all_users],
            'n_users': n_users,
            'n_movies': ctx['n_movies'],
        }
        for i in range(n_users):
            uid = int(all_users[i])
            nb_list = []
            for j in range(1, min(31, n_users)):
                nb_uid = int(all_users[indices[i, j]])
                sim = 1.0 - distances[i, j]
                if sim > 0:
                    total_sim = sum(abs(1.0 - distances[i, k])
                                    for k in range(1, min(31, n_users)))
                    if total_sim > 0:
                        sim_norm = sim / total_sim
                        nb_list.append((nb_uid, sim_norm))
            turbo_model['user_neighbors'][uid] = nb_list

        self.turbo_model = turbo_model
        self.trained = True
        print(f"  回退训练完成（全量 Pearson User-CF） | 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        """预测评分：使用 Turbo-CF 的加权平均公式"""
        model = getattr(self, 'turbo_model', None)
        if model is None:
            ctx = self.ctx
            return float(ctx['user_means'][ctx['user2idx'].get(user_id, 0)])
        ctx = self.ctx

        uid = user_id
        mid = movie_id

        user_means = model.get('user_means', {})
        mean_u = user_means.get(uid)
        if mean_u is None:
            u_idx = ctx['user2idx'].get(uid)
            if u_idx is not None:
                mean_u = float(ctx['user_means'][u_idx])
            else:
                return 3.0

        neighbors = model.get('user_neighbors', {}).get(uid, [])
        if not neighbors:
            return float(mean_u)

        # 预测公式: pred = mean_u + Σ sim·(r_vi - mean_v) / Σ |sim|
        num = 0.0
        den = 0.0
        for nb_uid, sim in neighbors:
            nb_ratings = ctx['user_ratings_dict'].get(nb_uid, {})
            if mid in nb_ratings:
                mean_v = user_means.get(nb_uid, mean_u)
                num += sim * (nb_ratings[mid] - mean_v)
                den += abs(sim)

        if den > 0:
            pred = float(mean_u + num / den)
            return max(1.0, min(5.0, pred))
        return float(mean_u)


# ─── 2.8 SVD 模型（调用已有模型） ──────────────────────────────

class SVDModel(BaseModel):
    """
    SVD 模型
    - 使用已有的 svd_model.pkl
    - 或者传入参数重新计算
    """
    def __init__(self, n_factors=50, model_path=None):
        super().__init__('svd')
        self.n_factors = n_factors
        self.model_path = model_path

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 训练/加载 SVD 模型...")
        t0 = time.time()

        self.ctx = ctx

        # 优先从已有模型文件加载
        if self.model_path and os.path.exists(self.model_path):
            print(f"  从文件加载: {self.model_path}")
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)
            self.svd_model = model_data
            self.trained = True
            print(f"  耗时: {time.time() - t0:.2f}s")
            return

        # 否则从头训练
        print(f"  从头训练 SVD (factors={self.n_factors})...")
        n_users = ctx['n_users']
        n_movies = ctx['n_movies']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']

        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        k = min(self.n_factors, min(n_users, n_movies) - 1)
        svd = TruncatedSVD(n_components=k, algorithm='randomized', random_state=42)
        user_features = svd.fit_transform(centered)
        movie_features = svd.components_.T

        self.svd_model = {
            'user_features': user_features,
            'movie_features': movie_features,
            'user_means': user_means,
            'user2idx': ctx['user2idx'],
            'movie2idx': ctx['movie2idx'],
            'n_factors': k,
        }
        self.trained = True
        print(f"  隐向量: user={user_features.shape}, movie={movie_features.shape}")
        print(f"  耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        model = self.svd_model
        user2idx = model['user2idx']
        movie2idx = model['movie2idx']
        user_features = model['user_features']
        movie_features = model['movie_features']
        user_means = model['user_means']

        u_idx = user2idx.get(user_id)
        m_idx = movie2idx.get(movie_id)

        if u_idx is None or m_idx is None:
            ctx = self.ctx
            return float(ctx['movie_means'][ctx['movie2idx'].get(movie_id, 0)])

        pred = float(np.dot(user_features[u_idx], movie_features[m_idx]) + user_means[u_idx])
        # 限制在评分范围内
        pred = max(1.0, min(5.0, pred))
        return pred


# ─── 2.9 混合推荐：公式 (3-7) ─────────────────────────────────

class HybridModel(BaseModel):
    """
    混合推荐
    - 公式 (3-7): 先归一化各模型输出到 [0,1] 区间，再加权融合
        score(u,i) = Σ w_k * norm_k(u,i)  其中 Σ w_k = 1
    """
    def __init__(self, models_dict, weights=None):
        """
        models_dict: {'name': model_instance, ...}
        weights: {'name': weight, ...} 如果不提供则等权
        """
        super().__init__('hybrid')
        self.models_dict = models_dict
        if weights is None:
            n = len(models_dict)
            self.weights = {name: 1.0 / n for name in models_dict}
        else:
            self.weights = weights
            # 归一化权重
            total = sum(self.weights.values())
            if total > 0:
                for k in self.weights:
                    self.weights[k] /= total

    def train(self, ctx):
        print(f"\n{'=' * 60}")
        print(f"[{self.name}] 初始化: 子模型 {len(self.models_dict)} 个")
        for name in self.models_dict:
            print(f"  - {name}: weight={self.weights.get(name, 0):.3f}")

        # 子模型必须在外部已训练好
        for name, model in self.models_dict.items():
            if not model.trained:
                print(f"  [警告] 子模型 {name} 未训练, 自动训练...")
                model.train(ctx)

        self.ctx = ctx
        self.trained = True
        print(f"  混合模型就绪")

    def predict(self, user_id, movie_id):
        preds = []
        for name, model in self.models_dict.items():
            try:
                p = model.predict(user_id, movie_id)
                preds.append(p)
            except Exception as e:
                print(f"  [警告] {name} 预测失败: {e}")
                preds.append(3.0)

        if not preds:
            return 3.0

        # 公式 (3-7): 先归一化再加权
        # 归一化到 [0,1] 区间（评分 1-5 → 0-1）
        normalized = [(p - 1.0) / 4.0 for p in preds]

        # 加权融合
        final_score = 0.0
        for i, name in enumerate(self.models_dict):
            final_score += self.weights[name] * normalized[i]

        # 转换回 1-5 评分
        return float(final_score * 4.0 + 1.0)


# ═══════════════════════════════════════════════════════════════
# 3. 评估指标
# ═══════════════════════════════════════════════════════════════

def compute_rmse(y_true, y_pred):
    """RMSE = sqrt(mean((y_true - y_pred)^2))"""
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def compute_mae(y_true, y_pred):
    """MAE = mean(|y_true - y_pred|)"""
    return float(np.mean(np.abs(y_true - y_pred)))


def compute_precision_recall_f1(recommended_items, relevant_items):
    """
    计算单个用户的 Precision, Recall, F1
    recommended_items: set of recommended movie_ids (Top-N)
    relevant_items: set of relevant movie_ids (rating >= 4 in test set)
    """
    if not recommended_items:
        return 0.0, 0.0, 0.0

    hits = len(recommended_items & relevant_items)

    precision = hits / len(recommended_items) if recommended_items else 0.0
    recall = hits / len(relevant_items) if relevant_items else 0.0
    f1 = 0.0
    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)

    return precision, recall, f1


def compute_coverage(recommendations_per_user, all_movies):
    """
    物品覆盖率
    recommendations_per_user: {user_id: [movie_id, ...], ...}
    all_movies: set of all movie ids
    """
    recommended_movies = set()
    for uid, movie_list in recommendations_per_user.items():
        recommended_movies.update(movie_list)

    coverage = len(recommended_movies) / len(all_movies) if all_movies else 0.0
    return coverage, recommended_movies


# ═══════════════════════════════════════════════════════════════
# 4. 主评估流程
# ═══════════════════════════════════════════════════════════════

def evaluate_model(model, test_df, train_df, ctx, top_n=10):
    """
    评估单个模型
    返回: {
        'name': model.name,
        'rmse': ...,
        'mae': ...,
        'precision_at_k': ...,
        'recall_at_k': ...,
        'f1_at_k': ...,
        'coverage': ...,
        'predictions': np.array,
        'pred_time': ...,
    }
    """
    print(f"\n{'=' * 50}")
    print(f"[评估] 模型: {model.name}")
    print(f"{'=' * 50}")

    test_user_ids = test_df['user_id'].values
    test_movie_ids = test_df['movie_id'].values
    test_ratings = test_df['rating'].values

    # ── 评分预测 ──
    print(f"  生成评分预测 ({len(test_df)} 条)...")
    t0 = time.time()
    preds = model.predict_batch(test_user_ids, test_movie_ids)
    pred_time = time.time() - t0

    rmse = compute_rmse(test_ratings, preds)
    mae = compute_mae(test_ratings, preds)
    print(f"  评分预测完成: RMSE={rmse:.4f}, MAE={mae:.4f}, 耗时={pred_time:.2f}s")

    # ── Top-N 推荐 ──
    print(f"  生成 Top-{top_n} 推荐...")
    t0 = time.time()

    # 建立训练集用户已评分电影集合
    user_rated_in_train = defaultdict(set)
    for uid, mid in zip(train_df['user_id'], train_df['movie_id']):
        user_rated_in_train[int(uid)].add(int(mid))

    # 测试集中正反馈（评分 >= 4）
    test_positive = defaultdict(set)
    for uid, mid, rating in zip(test_df['user_id'], test_df['movie_id'], test_df['rating']):
        if rating >= 4:
            test_positive[int(uid)].add(int(mid))

    # 获取测试集所有用户
    test_users = set(test_df['user_id'].values)

    # 为每个测试用户生成推荐
    recommendations = {}
    all_movies_set = set(ctx['all_movies'])

    # 只评估测试集中出现的用户
    for user_id in list(test_users)[:200]:  # 限制评估用户数以避免耗时过长
        uid = int(user_id)
        rated_in_train = user_rated_in_train.get(uid, set())

        # 候选电影 = 所有电影 - 训练集中已评分的电影
        candidates = all_movies_set - rated_in_train

        # 对每个候选电影预测评分
        candidate_preds = []
        for mid in candidates:
            try:
                pred = model.predict(uid, mid)
                candidate_preds.append((mid, pred))
            except:
                continue

        # 按评分排序取 Top-N
        candidate_preds.sort(key=lambda x: -x[1])
        top_n_recs = [mid for mid, _ in candidate_preds[:top_n]]
        recommendations[uid] = top_n_recs

    rec_time = time.time() - t0

    # ── 计算 Precision/Recall/F1 ──
    precisions = []
    recalls = []
    f1s = []
    for uid in recommendations:
        rec_items = set(recommendations[uid])
        rel_items = test_positive.get(uid, set())
        if not rel_items:
            continue  # 跳过没有正反馈的用户
        p, r, f = compute_precision_recall_f1(rec_items, rel_items)
        precisions.append(p)
        recalls.append(r)
        f1s.append(f)

    avg_precision = float(np.mean(precisions)) if precisions else 0.0
    avg_recall = float(np.mean(recalls)) if recalls else 0.0
    avg_f1 = float(np.mean(f1s)) if f1s else 0.0

    # ── 计算覆盖率 ──
    coverage, rec_movies = compute_coverage(recommendations, all_movies_set)

    print(f"  Top-{top_n} 推荐完成: "
          f"Precision={avg_precision:.4f}, Recall={avg_recall:.4f}, "
          f"F1={avg_f1:.4f}, Coverage={coverage:.4f}")
    print(f"  推荐耗时={rec_time:.2f}s")

    return {
        'name': model.name,
        'rmse': rmse,
        'mae': mae,
        'precision_at_k': avg_precision,
        'recall_at_k': avg_recall,
        'f1_at_k': avg_f1,
        'coverage': coverage,
        'predictions': preds,
        'ground_truth': test_ratings,
        'pred_time': pred_time,
        'rec_time': rec_time,
    }


def run_evaluation(test_size=None):
    """运行完整评估流程"""
    overall_start = time.time()

    # ── 1. 加载数据 ──
    ratings_df, movies_df = load_all_data()

    # ── 2. 划分训练/测试集 ──
    train_df, test_df = train_test_split_by_user(ratings_df, test_ratio=0.2)

    # 限制测试集大小（调试用）
    if test_size and test_size < len(test_df):
        test_df = test_df.iloc[:test_size]
        print(f"  [限制] 测试集截断至 {test_size} 条")

    # ── 3. 构建评分矩阵上下文 ──
    ctx = build_rating_matrices(train_df)

    # ── 4. 训练/初始化所有模型 ──
    print(f"\n{'=' * 60}")
    print("模型训练阶段")
    print(f"{'=' * 60}")

    models = {}

    # 4.1 传统 User-CF
    models['traditional_user_cf'] = TraditionalUserCF(n_neighbors=30)
    models['traditional_user_cf'].train(ctx)

    # 4.2 改进 User-CF
    models['improved_user_cf'] = ImprovedUserCF(n_neighbors=30, use_stability=True)
    models['improved_user_cf'].train(ctx)

    # 4.3 传统 Item-CF
    models['traditional_item_cf'] = TraditionalItemCF(n_neighbors=30)
    models['traditional_item_cf'].train(ctx)

    # 4.4 改进 Item-CF
    models['improved_item_cf'] = ImprovedItemCF(n_neighbors=30)
    models['improved_item_cf'].train(ctx)

    # 4.5 传统 Slope One
    models['traditional_slope_one'] = TraditionalSlopeOne()
    models['traditional_slope_one'].train(ctx)

    # 4.6 改进 Slope One
    models['improved_slope_one'] = ImprovedSlopeOne(n_neighbors=30, svd_factors=50)
    models['improved_slope_one'].train(ctx)

    # 4.7 SVD
    svd_model_path = os.path.join(MODEL_DIR, 'svd_model.pkl')
    models['svd'] = SVDModel(n_factors=50, model_path=svd_model_path)
    models['svd'].train(ctx)

    # 4.8 Turbo-CF (加速协同过滤)
    turbo_cf_model_path = os.path.join(MODEL_DIR, 'turbo_cf_model.pkl')
    models['turbo_cf'] = TurboCFModel(model_path=turbo_cf_model_path)
    models['turbo_cf'].train(ctx)

    # 4.9 混合推荐 (使用所有已训练模型)
    hybrid_models = {name: models[name] for name in models}
    models['hybrid'] = HybridModel(hybrid_models)
    models['hybrid'].train(ctx)

    # ── 5. 评估所有模型 ──
    print(f"\n{'=' * 60}")
    print("模型评估阶段")
    print(f"{'=' * 60}")

    all_results = {}
    for name, model in models.items():
        result = evaluate_model(model, test_df, train_df, ctx, top_n=10)
        all_results[name] = result

    # ── 6. 汇总结果 ──
    print(f"\n\n{'=' * 80}")
    print(f"           评估结果汇总")
    print(f"{'=' * 80}")

    print(f"\n{'模型':<22} {'RMSE':<10} {'MAE':<10} {'Prec@10':<10} "
          f"{'Rec@10':<10} {'F1@10':<10} {'Coverage':<10} {'耗时(s)':<10}")
    print(f"{'-' * 92}")

    summary_rows = []
    for name, result in all_results.items():
        total_time = result.get('pred_time', 0) + result.get('rec_time', 0)
        print(f"{result['name']:<22} {result['rmse']:<10.4f} {result['mae']:<10.4f} "
              f"{result['precision_at_k']:<10.4f} {result['recall_at_k']:<10.4f} "
              f"{result['f1_at_k']:<10.4f} {result['coverage']:<10.4f} {total_time:<10.2f}")
        summary_rows.append({
            'model': result['name'],
            'rmse': result['rmse'],
            'mae': result['mae'],
            'precision@10': result['precision_at_k'],
            'recall@10': result['recall_at_k'],
            'f1@10': result['f1_at_k'],
            'coverage': result['coverage'],
            'pred_time': result['pred_time'],
            'rec_time': result['rec_time'],
        })

    print(f"{'=' * 92}")

    # ── 7. 保存结果 ──
    # 保存详细结果为 JSON
    output_path = os.path.join(OUTPUT_DIR, 'evaluation_results.json')
    output = {
        'metadata': {
            'dataset': {
                'train_size': len(train_df),
                'test_size': len(test_df),
                'n_users': int(train_df['user_id'].nunique()),
                'n_movies': int(train_df['movie_id'].nunique()),
            },
            'test_size_limit': test_size,
            'total_time': time.time() - overall_start,
        },
        'results': summary_rows,
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  评估结果已保存: {output_path}")

    # 保存汇总 CSV
    csv_path = os.path.join(OUTPUT_DIR, 'evaluation_summary.csv')
    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(csv_path, index=False)
    print(f"  汇总 CSV: {csv_path}")

    # 保存各模型预测值
    preds_path = os.path.join(OUTPUT_DIR, 'test_predictions.npz')
    np.savez_compressed(
        preds_path,
        **{name: result['predictions'] for name, result in all_results.items()},
        ground_truth=test_df['rating'].values,
    )
    print(f"  预测值: {preds_path}")

    total_time = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  评估完成！总耗时: {total_time:.2f} 秒")
    print(f"{'=' * 60}\n")

    return all_results


# ═══════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='推荐系统 - 全模型评估')
    parser.add_argument('--test-size', type=int, default=None,
                        help='限制测试样本数 (调试用)')
    parser.add_argument('--top-n', type=int, default=10,
                        help='推荐列表长度 (默认: 10)')
    args = parser.parse_args()

    run_evaluation(test_size=args.test_size)


if __name__ == '__main__':
    main()