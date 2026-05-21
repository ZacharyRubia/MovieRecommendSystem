#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluate_hybrid_weights.py - 混合推荐算法权重优化评估脚本

================================================================
功能说明
================================================================

1. 权重优化搜索
   - 双模型模式 (mode=2, 默认): 对 User-CF + Item-CF 进行 [0,1] 网格搜索
   - 全模型模式 (mode=all): 对所有子模型进行权重搜索

2. 评估指标
   - 评分预测: RMSE, MAE
   - Top-N 推荐: Precision@N, Recall@N, F1@N
   - 物品覆盖率: Coverage

3. 最优权重选取
   - 基于 RMSE 最小化
   - 基于 F1 最大化
   - 基于综合得分 (RMSE + F1 加权)
   - 当前系统默认权重 (0.3, 0.5, 0.7)

4. 分析点导出
   - Pareto 前沿配置
   - 最优配置的详细用户级结果
   - 各指标随权重变化曲线数据
   - 代表性权重点 (均匀采样若干个)

================================================================
用法 (PowerShell)
================================================================

  # 默认运行（双模型模式）
  python scripts/evaluation/evaluate_hybrid_weights.py

  # 限制测试集大小（快速调试）
  python scripts/evaluation/evaluate_hybrid_weights.py --test-size 5000

  # 全模型模式 + 随机搜索
  python scripts/evaluation/evaluate_hybrid_weights.py --mode all --n-random 200

  # 自定义 Top-N 和网格步长
  python scripts/evaluation/evaluate_hybrid_weights.py --top-n 20 --grid-step 0.02

  # 指定输出目录
  python scripts/evaluation/evaluate_hybrid_weights.py --output-dir custom_results

================================================================
输出文件
================================================================

  {output_dir}/
  ├── evaluation_results.json        # 完整评估结果
  ├── weight_optimization.csv        # 各权重组合指标汇总
  ├── optimal_weights.json           # 最优权重摘要
  ├── analysis_points/               # 选取的分析点详细结果
  │   ├── point_001_weights.json
  │   ├── point_002_weights.json
  │   └── ...
  └── pareto_frontier.csv            # Pareto 前沿配置
"""

import os
import sys
import math
import json
import time
import copy
import pickle
import random
import argparse
import warnings
import itertools
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD
from sklearn.neighbors import NearestNeighbors

warnings.filterwarnings('ignore', category=RuntimeWarning)
warnings.filterwarnings('ignore', category=UserWarning)

# ─── 路径 ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, 'evaluation_results')

# ─── 常量 ────────────────────────────────────────────────────
DEFAULT_GRID_STEP = 0.05       # 双模型网格搜索步长
DEFAULT_N_RANDOM = 200         # 全模型随机搜索次数
DEFAULT_TOP_N = 10             # 推荐列表长度
DEFAULT_N_NEIGHBORS = 30       # CF 邻居数
MIN_SIM = 0.0                  # 最小相似度阈值
RANDOM_SEED = 42               # 随机种子

CURRENT_WEIGHTS = {            # 当前系统使用的离散权重
    'low':   {'user_cf': 0.3, 'item_cf': 0.7},
    'mid':   {'user_cf': 0.5, 'item_cf': 0.5},
    'high':  {'user_cf': 0.7, 'item_cf': 0.3},
}

# 全模型模式各算法名称
ALL_MODEL_NAMES = [
    'traditional_user_cf',
    'improved_user_cf',
    'traditional_item_cf',
    'improved_item_cf',
    'traditional_slope_one',
    'improved_slope_one',
    'svd',
]

# improved 模式: 仅改进版本 + SVD + Turbo-CF
IMPROVED_MODEL_NAMES = [
    'improved_user_cf',
    'improved_item_cf',
    'improved_slope_one',
    'svd',
    'turbo_cf',
]

# 分析点选取数量
N_ANALYSIS_POINTS = 5


# ═══════════════════════════════════════════════════════════════
# 1. 数据加载与预处理
# ═══════════════════════════════════════════════════════════════

def load_all_data():
    """加载原始评分数据"""
    print("=" * 60)
    print("[数据加载]")
    print("=" * 60)

    ratings_df = pd.read_csv(
        os.path.join(DATA_DIR, 'test_ratings.csv'),
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )

    movies_df = None
    movies_path = os.path.join(DATA_DIR, 'test_movies.csv')
    if os.path.exists(movies_path):
        movies_df = pd.read_csv(movies_path)

    print(f"  评分总数: {len(ratings_df)} 条")
    print(f"  用户数:   {ratings_df['user_id'].nunique()}")
    print(f"  电影数:   {ratings_df['movie_id'].nunique()}")

    return ratings_df, movies_df


def train_test_split_by_user(ratings_df, test_ratio=0.2, random_state=RANDOM_SEED):
    """按用户划分训练/测试集"""
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
    """构建评分矩阵和映射字典"""
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

    print(f"  评分矩阵: ({n_users}, {n_movies}), 非零元素: {rating_matrix.nnz}")
    return ctx


# ═══════════════════════════════════════════════════════════════
# 2. 子模型定义（与 evaluate_models.py 一致）
# ═══════════════════════════════════════════════════════════════

class BaseModel:
    """模型基类"""
    def __init__(self, name):
        self.name = name
        self.trained = False

    def train(self, ctx):
        raise NotImplementedError

    def predict(self, user_id, movie_id):
        raise NotImplementedError

    def predict_batch(self, user_ids, movie_ids):
        return np.array([self.predict(uid, mid) for uid, mid in zip(user_ids, movie_ids)])


# ─── 2.1 传统 User-CF ─────────────────────────────────────────

class TraditionalUserCF(BaseModel):
    """
    传统 User-Based CF
    - 相似度: Pearson 相关系数
    - 预测: 等权平均偏差
    """
    def __init__(self, n_neighbors=DEFAULT_N_NEIGHBORS, min_sim=MIN_SIM):
        super().__init__('traditional_user_cf')
        self.n_neighbors = n_neighbors
        self.min_sim = min_sim

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx
        n_users = ctx['n_users']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']

        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_users),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(centered)
        distances, indices = nn.kneighbors(centered, return_distance=True)

        self.user_sim_data = {}
        for i in range(n_users):
            uid = int(ctx['all_users'][i])
            nb_list = []
            for j in range(1, min(self.n_neighbors + 1, n_users)):
                nb_uid = int(ctx['all_users'][indices[i, j]])
                sim = 1.0 - distances[i, j]
                if sim > self.min_sim:
                    nb_list.append((nb_uid, sim))
            self.user_sim_data[uid] = nb_list

        self.trained = True
        print(f"    [{self.name}] 训练完成, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id

        if uid not in self.user_sim_data or not self.user_sim_data[uid]:
            return float(ctx['user_means'][ctx['user2idx'].get(uid, 0)])

        neighbors = self.user_sim_data[uid]
        ratings = []
        for nb_uid, sim in neighbors:
            nb_ratings = ctx['user_ratings_dict'].get(nb_uid, {})
            if mid in nb_ratings:
                ratings.append(nb_ratings[mid])

        if not ratings:
            return float(ctx['user_means'][ctx['user2idx'].get(uid, 0)])

        return float(np.mean(ratings))


# ─── 2.2 改进 User-CF ─────────────────────────────────────────

class ImprovedUserCF(BaseModel):
    """
    改进 User-Based CF
    - 相似度: Pearson 相关系数
    - 预测: 加权平均 (公式 3-1) + 稳定性因子 (公式 3-3)
    """
    def __init__(self, n_neighbors=DEFAULT_N_NEIGHBORS, use_stability=True, min_sim=MIN_SIM):
        super().__init__('improved_user_cf')
        self.n_neighbors = n_neighbors
        self.use_stability = use_stability
        self.min_sim = min_sim

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx
        n_users = ctx['n_users']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']

        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_users),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(centered)
        distances, indices = nn.kneighbors(centered, return_distance=True)

        self.user_sim_data = {}
        for i in range(n_users):
            uid = int(ctx['all_users'][i])
            nb_list = []
            for j in range(1, min(self.n_neighbors + 1, n_users)):
                nb_uid = int(ctx['all_users'][indices[i, j]])
                sim = 1.0 - distances[i, j]
                if sim > self.min_sim:
                    nb_list.append((nb_uid, sim))
            self.user_sim_data[uid] = nb_list

        self.trained = True
        print(f"    [{self.name}] 训练完成, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id

        if uid not in self.user_sim_data or not self.user_sim_data[uid]:
            return float(ctx['user_means'][ctx['user2idx'].get(uid, 0)])

        user_mean = float(ctx['user_means'][ctx['user2idx'].get(uid, 0)])
        neighbors = self.user_sim_data[uid]

        weighted_sum = 0.0
        sim_sum = 0.0

        for nb_uid, sim in neighbors:
            nb_ratings = ctx['user_ratings_dict'].get(nb_uid, {})
            if mid in nb_ratings:
                nb_mean = float(ctx['user_means'][ctx['user2idx'].get(nb_uid, 0)])
                nb_n_items = len(nb_ratings)

                # 稳定性因子 (公式 3-3)
                if self.use_stability:
                    stability = 1.0 - 1.0 / (1.0 + nb_n_items)
                else:
                    stability = 1.0

                final_weight = sim * stability
                weighted_sum += final_weight * (nb_ratings[mid] - nb_mean)
                sim_sum += final_weight

        if sim_sum <= 0:
            return user_mean

        pred = user_mean + weighted_sum / sim_sum
        return float(np.clip(pred, 1.0, 5.0))


# ─── 2.3 传统 Item-CF ─────────────────────────────────────────

class TraditionalItemCF(BaseModel):
    """
    传统 Item-Based CF
    - 相似度: Cosine 相似度 (未去均值)
    - 预测: 等权平均
    """
    def __init__(self, n_neighbors=DEFAULT_N_NEIGHBORS, min_sim=MIN_SIM):
        super().__init__('traditional_item_cf')
        self.n_neighbors = n_neighbors
        self.min_sim = min_sim

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx
        n_movies = ctx['n_movies']
        rating_matrix = ctx['rating_matrix']
        # Item-CF: 转置矩阵，按行（物品）计算相似度
        item_vectors = rating_matrix.T.tocsr()

        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_movies),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(item_vectors)
        distances, indices = nn.kneighbors(item_vectors, return_distance=True)

        self.item_sim_data = {}
        for i in range(n_movies):
            mid = int(ctx['all_movies'][i])
            nb_list = []
            for j in range(1, min(self.n_neighbors + 1, n_movies)):
                nb_mid = int(ctx['all_movies'][indices[i, j]])
                sim = 1.0 - distances[i, j]
                if sim > self.min_sim:
                    nb_list.append((nb_mid, sim))
            self.item_sim_data[mid] = nb_list

        self.trained = True
        print(f"    [{self.name}] 训练完成, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id
        user_ratings = ctx['user_ratings_dict'].get(uid, {})

        if mid not in self.item_sim_data:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        neighbors = self.item_sim_data[mid]
        ratings = []
        for nb_mid, sim in neighbors:
            if nb_mid in user_ratings:
                ratings.append(user_ratings[nb_mid])

        if not ratings:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        return float(np.mean(ratings))


# ─── 2.4 改进 Item-CF ─────────────────────────────────────────

class ImprovedItemCF(BaseModel):
    """
    改进 Item-Based CF
    - 相似度: Cosine 相似度
    - 预测: 加权平均 (公式 3-2)
    """
    def __init__(self, n_neighbors=DEFAULT_N_NEIGHBORS, min_sim=MIN_SIM):
        super().__init__('improved_item_cf')
        self.n_neighbors = n_neighbors
        self.min_sim = min_sim

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx
        n_movies = ctx['n_movies']
        rating_matrix = ctx['rating_matrix']
        item_vectors = rating_matrix.T.tocsr()

        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_movies),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(item_vectors)
        distances, indices = nn.kneighbors(item_vectors, return_distance=True)

        self.item_sim_data = {}
        for i in range(n_movies):
            mid = int(ctx['all_movies'][i])
            nb_list = []
            for j in range(1, min(self.n_neighbors + 1, n_movies)):
                nb_mid = int(ctx['all_movies'][indices[i, j]])
                sim = 1.0 - distances[i, j]
                if sim > self.min_sim:
                    nb_list.append((nb_mid, sim))
            self.item_sim_data[mid] = nb_list

        self.trained = True
        print(f"    [{self.name}] 训练完成, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id
        user_ratings = ctx['user_ratings_dict'].get(uid, {})

        if mid not in self.item_sim_data:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        neighbors = self.item_sim_data[mid]
        weighted_sum = 0.0
        sim_sum = 0.0

        for nb_mid, sim in neighbors:
            if nb_mid in user_ratings:
                weighted_sum += sim * user_ratings[nb_mid]
                sim_sum += sim

        if sim_sum <= 0:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        return float(weighted_sum / sim_sum)


# ─── 2.5 传统 Slope One ───────────────────────────────────────

class TraditionalSlopeOne(BaseModel):
    """
    传统 Slope One (公式 3-4)
    - 全局偏差: dev(j,i) = avg(r_uj - r_ui)
    - 预测: pred(u,i) = avg(r_uj + dev(i,j))
    """
    def __init__(self):
        super().__init__('traditional_slope_one')

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx
        all_movies = list(ctx['movie2idx'].keys())

        # 计算所有电影对的评分偏差
        self.dev_matrix = {}  # {mid_i: {mid_j: dev}}
        movie_ratings = ctx['movie_ratings_dict']

        for i, mid_i in enumerate(all_movies):
            if i % 100 == 0 and i > 0:
                print(f"    [{self.name}] 进度: {i}/{len(all_movies)}", end='\r')
            raters_i = set(movie_ratings.get(mid_i, {}).keys())
            self.dev_matrix[mid_i] = {}
            for mid_j in all_movies:
                if mid_i == mid_j:
                    continue
                raters_j = set(movie_ratings.get(mid_j, {}).keys())
                common = raters_i & raters_j
                if len(common) < 2:
                    continue
                diffs = []
                for u in common:
                    diffs.append(movie_ratings[mid_j][u] - movie_ratings[mid_i][u])
                self.dev_matrix[mid_i][mid_j] = float(np.mean(diffs))

        self.trained = True
        print(f"    [{self.name}] 训练完成, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id
        user_ratings = ctx['user_ratings_dict'].get(uid, {})

        if not user_ratings:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        if mid not in self.dev_matrix:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        preds = []
        for r_mid, r_val in user_ratings.items():
            if r_mid in self.dev_matrix.get(mid, {}):
                pred = r_val + self.dev_matrix[mid][r_mid]
                preds.append(pred)

        if not preds:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        return float(np.clip(np.mean(preds), 1.0, 5.0))


# ─── 2.6 改进 Slope One ───────────────────────────────────────

class ImprovedSlopeOne(BaseModel):
    """
    改进 Slope One (公式 3-5, 3-6)
    - 局域偏差 + 邻域预测
    - 先用 SVD 找到相似用户，再用邻域计算偏差
    """
    def __init__(self, n_neighbors=DEFAULT_N_NEIGHBORS, svd_factors=50):
        super().__init__('improved_slope_one')
        self.n_neighbors = n_neighbors
        self.svd_factors = svd_factors

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx
        n_users = ctx['n_users']
        rating_matrix = ctx['rating_matrix']

        # 使用 SVD 降维找邻居
        svd = TruncatedSVD(n_components=min(self.svd_factors, n_users - 1, ctx['n_movies'] - 1),
                           random_state=RANDOM_SEED)
        user_factors = svd.fit_transform(rating_matrix)

        nn = NearestNeighbors(
            n_neighbors=min(self.n_neighbors + 1, n_users),
            metric='cosine',
            algorithm='brute',
            n_jobs=-1,
        )
        nn.fit(user_factors)
        distances, indices = nn.kneighbors(user_factors, return_distance=True)

        self.user_neighbors = {}
        for i in range(n_users):
            uid = int(ctx['all_users'][i])
            nb_list = []
            for j in range(1, min(self.n_neighbors + 1, n_users)):
                nb_uid = int(ctx['all_users'][indices[i, j]])
                sim = 1.0 - distances[i, j]
                if sim > 0:
                    nb_list.append(nb_uid)
            self.user_neighbors[uid] = nb_list

        self.trained = True
        print(f"    [{self.name}] 训练完成, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id
        user_ratings = ctx['user_ratings_dict'].get(uid, {})

        if not user_ratings:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        neighbors = self.user_neighbors.get(uid, [])
        if not neighbors:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        preds = []
        for nb_uid in neighbors:
            nb_ratings = ctx['user_ratings_dict'].get(nb_uid, {})
            if mid not in nb_ratings:
                continue
            # 局域偏差: 用邻域的评分偏差
            for r_mid, r_val in user_ratings.items():
                if r_mid in nb_ratings:
                    dev = nb_ratings[mid] - nb_ratings[r_mid]
                    preds.append(r_val + dev)

        if not preds:
            return float(ctx['movie_means'][ctx['movie2idx'].get(mid, 0)])

        return float(np.clip(np.mean(preds), 1.0, 5.0))


# ─── 2.7 SVD ──────────────────────────────────────────────────

class SVDModel(BaseModel):
    """
    SVD 矩阵分解模型
    - 使用 TruncatedSVD
    """
    def __init__(self, n_factors=50):
        super().__init__('svd')
        self.n_factors = n_factors

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx
        n_users = ctx['n_users']
        n_movies = ctx['n_movies']
        rating_matrix = ctx['rating_matrix']
        user_means = ctx['user_means']

        # 去均值
        centered = rating_matrix.copy().astype(np.float32)
        row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
        centered.data -= user_means[row_indices.astype(np.int32)]

        n_components = min(self.n_factors, n_users - 1, n_movies - 1)
        self.svd = TruncatedSVD(n_components=n_components, random_state=RANDOM_SEED)
        self.user_factors = self.svd.fit_transform(centered)
        self.movie_factors = self.svd.components_.T

        self.trained = True
        print(f"    [{self.name}] 训练完成, 因子数={n_components}, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
        ctx = self.ctx
        uid = user_id
        mid = movie_id

        if uid not in ctx['user2idx'] or mid not in ctx['movie2idx']:
            return float(ctx['movie_means'].get(mid, 3.0)
                         if hasattr(ctx['movie_means'], 'get')
                         else 3.0)

        u_idx = ctx['user2idx'][uid]
        m_idx = ctx['movie2idx'][mid]
        user_mean = float(ctx['user_means'][u_idx])

        pred = user_mean + np.dot(self.user_factors[u_idx], self.movie_factors[m_idx])
        return float(np.clip(pred, 1.0, 5.0))


# ─── 2.8 Turbo-CF ──────────────────────────────────────────────

class TurboCFModel(BaseModel):
    """
    Turbo-CF（加速协同过滤）
    - 从 train_turbocf.py 训练的 turbo_cf_model.pkl 加载
    - 预测公式: pred(u,i) = mean_u + ( Σ sim·(r_vi - mean_v) ) / Σ |sim|
    """
    def __init__(self, model_path=None):
        super().__init__('turbo_cf')
        self.model_path = model_path

    def train(self, ctx):
        t0 = time.time()
        self.ctx = ctx

        if self.model_path and os.path.exists(self.model_path):
            print(f"    [{self.name}] 从文件加载: {os.path.basename(self.model_path)}")
            with open(self.model_path, 'rb') as f:
                model_data = pickle.load(f)
            self.turbo_model = model_data
            n_clusters = model_data.get('n_clusters', '?')
            n_users = model_data.get('n_users', '?')
            nb_count = sum(len(v) for v in model_data.get('user_neighbors', {}).values())
            print(f"    [{self.name}] 簇数={n_clusters}, 用户={n_users}, 邻居总数={nb_count}")
            self.trained = True
            print(f"    [{self.name}] 加载完成, 耗时: {time.time() - t0:.2f}s")
            return

        print(f"    [{self.name}] 模型文件不存在, 使用全量 User-CF 回退...")
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
        print(f"    [{self.name}] 回退训练完成, 耗时: {time.time() - t0:.2f}s")

    def predict(self, user_id, movie_id):
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
    """计算单个用户的 Precision, Recall, F1"""
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
    """物品覆盖率"""
    recommended_movies = set()
    for uid, movie_list in recommendations_per_user.items():
        recommended_movies.update(movie_list)

    coverage = len(recommended_movies) / len(all_movies) if all_movies else 0.0
    return coverage, recommended_movies


# ═══════════════════════════════════════════════════════════════
# 4. 混合模型与权重评估
# ═══════════════════════════════════════════════════════════════

class HybridPredictor:
    """
    混合推荐预测器
    对给定的权重组合，计算混合模型的预测值
    """
    def __init__(self, models_dict, model_names):
        """
        models_dict: {name: model_object, ...}
        model_names: list of model names to include in hybrid
        """
        self.models_dict = models_dict
        self.model_names = model_names

    def predict(self, user_id, movie_id, weights):
        """
        使用给定权重预测评分
        weights: {name: weight_value, ...}  所有 name 的权重之和应为 1

        公式 (3-7): score(u,i) = Σ w_k · norm_k(u,i)
        归一化: 评分 1-5 → [0,1]
        """
        preds = []
        for name in self.model_names:
            w = weights.get(name, 0.0)
            if w <= 0:
                continue
            model = self.models_dict[name]
            try:
                p = model.predict(user_id, movie_id)
            except Exception:
                p = 3.0  # fallback
            preds.append(p)

        if not preds:
            return 3.0

        # 归一化到 [0,1] 区间
        normalized = [(p - 1.0) / 4.0 for p in preds]

        # 加权融合
        final_score = 0.0
        for i, name in enumerate(self.model_names):
            w = weights.get(name, 0.0)
            if w > 0 and i < len(normalized):
                final_score += w * normalized[i]

        # 转换回 1-5 评分
        return float(final_score * 4.0 + 1.0)

    def predict_batch(self, user_ids, movie_ids, weights):
        """批量预测"""
        return np.array([
            self.predict(uid, mid, weights)
            for uid, mid in zip(user_ids, movie_ids)
        ])


def evaluate_hybrid_weights(
    hybrid_predictor,
    test_df,
    train_df,
    ctx,
    weights,
    top_n=DEFAULT_TOP_N,
    max_users_for_rec=200,
):
    """
    评估给定权重组合下的混合推荐性能

    参数:
        hybrid_predictor: HybridPredictor 实例
        test_df: 测试集 DataFrame
        train_df: 训练集 DataFrame
        ctx: 数据上下文
        weights: {model_name: weight_value}
        top_n: 推荐列表长度
        max_users_for_rec: 评估 Top-N 时最大用户数

    返回:
        dict: 评估结果
    """
    result = {
        'weights': dict(weights),
    }

    # ── 评分预测 ──
    test_user_ids = test_df['user_id'].values
    test_movie_ids = test_df['movie_id'].values
    test_ratings = test_df['rating'].values

    t0 = time.time()
    preds = hybrid_predictor.predict_batch(test_user_ids, test_movie_ids, weights)
    pred_time = time.time() - t0

    result['rmse'] = compute_rmse(test_ratings, preds)
    result['mae'] = compute_mae(test_ratings, preds)
    result['pred_time'] = pred_time

    # ── Top-N 推荐 ──
    t0 = time.time()

    user_rated_in_train = defaultdict(set)
    for uid, mid in zip(train_df['user_id'], train_df['movie_id']):
        user_rated_in_train[int(uid)].add(int(mid))

    test_positive = defaultdict(set)
    for uid, mid, rating in zip(test_df['user_id'], test_df['movie_id'], test_df['rating']):
        if rating >= 4:
            test_positive[int(uid)].add(int(mid))

    test_users = set(test_df['user_id'].values)
    all_movies_set = set(ctx['all_movies'])

    recommendations = {}
    for user_id in list(test_users)[:max_users_for_rec]:
        uid = int(user_id)
        rated_in_train = user_rated_in_train.get(uid, set())
        candidates = all_movies_set - rated_in_train

        candidate_preds = []
        for mid in candidates:
            try:
                pred = hybrid_predictor.predict(uid, mid, weights)
                candidate_preds.append((mid, pred))
            except Exception:
                continue

        candidate_preds.sort(key=lambda x: -x[1])
        top_n_recs = [mid for mid, _ in candidate_preds[:top_n]]
        recommendations[uid] = top_n_recs

    rec_time = time.time() - t0
    result['rec_time'] = rec_time

    # ── Precision/Recall/F1 ──
    precisions, recalls, f1s = [], [], []
    for uid in recommendations:
        rec_items = set(recommendations[uid])
        rel_items = test_positive.get(uid, set())
        if not rel_items:
            continue
        p, r, f = compute_precision_recall_f1(rec_items, rel_items)
        precisions.append(p)
        recalls.append(r)
        f1s.append(f)

    result['precision_at_k'] = float(np.mean(precisions)) if precisions else 0.0
    result['recall_at_k'] = float(np.mean(recalls)) if recalls else 0.0
    result['f1_at_k'] = float(np.mean(f1s)) if f1s else 0.0

    # ── 覆盖率 ──
    coverage, rec_movies = compute_coverage(recommendations, all_movies_set)
    result['coverage'] = coverage

    # ── 综合得分 (RMSE 越小越好, F1 越大越好) ──
    # 归一化综合: z = (1 - rmse/5) * 0.5 + f1 * 0.5
    result['composite_score'] = (1.0 - result['rmse'] / 5.0) * 0.5 + result['f1_at_k'] * 0.5

    return result


def get_weight_grid(mode='2', grid_step=DEFAULT_GRID_STEP, n_random=DEFAULT_N_RANDOM):
    """
    生成待搜索的权重组合

    参数:
        mode: '2' | 'all' | 'improved'
        grid_step: 双模型网格步长
        n_random: 随机搜索次数 (all 和 improved 模式使用)

    返回:
        list of dict: 每个 dict 为 {model_name: weight}
    """
    weight_configs = []

    if mode == '2':
        # 双模型: User-CF + Item-CF, 网格搜索 w_user ∈ [0, 1]
        # 包含当前系统使用的离散权重点
        current_points = [
            ('current_low',  0.3),
            ('current_mid',  0.5),
            ('current_high', 0.7),
        ]
        w_values = np.arange(0.0, 1.0 + grid_step, grid_step)
        for w_user in w_values:
            w_user = round(w_user, 4)
            if w_user > 1.0:
                continue
            config = {
                'tag': f'grid_w{int(w_user*100):03d}',
                'weights': {
                    'user_cf': w_user,
                    'item_cf': round(1.0 - w_user, 4),
                }
            }
            weight_configs.append(config)

        # 添加当前系统点（确保它们存在且标记清晰）
        for tag, w_user in current_points:
            found = False
            for cfg in weight_configs:
                if abs(cfg['weights']['user_cf'] - w_user) < 1e-6:
                    cfg['tag'] = tag  # 替换 tag
                    found = True
                    break
            if not found:
                weight_configs.append({
                    'tag': tag,
                    'weights': {
                        'user_cf': w_user,
                        'item_cf': round(1.0 - w_user, 4),
                    }
                })

    elif mode == 'improved':
        # 改进版模式: 5 个子模型，随机搜索
        model_names = IMPROVED_MODEL_NAMES
        n_models = len(model_names)

        # 均匀权重 baseline
        uniform_w = 1.0 / n_models
        weight_configs.append({
            'tag': 'uniform',
            'weights': {name: uniform_w for name in model_names},
        })

        # 单模型权重 (消融参考)
        for name in model_names:
            w = {n: 0.0 for n in model_names}
            w[name] = 1.0
            weight_configs.append({
                'tag': f'single_{name}',
                'weights': w,
            })

        # 随机搜索
        rng = np.random.default_rng(RANDOM_SEED)
        for i in range(n_random):
            raw = rng.random(n_models)
            w = raw / raw.sum()
            weight_configs.append({
                'tag': f'random_{i:04d}',
                'weights': {name: round(float(w[j]), 6)
                           for j, name in enumerate(model_names)},
            })

    else:
        # 全模型模式: 所有子模型，随机搜索
        # 先添加均匀权重作为 baseline
        n_models = len(ALL_MODEL_NAMES)
        uniform_w = 1.0 / n_models
        weight_configs.append({
            'tag': 'uniform',
            'weights': {name: uniform_w for name in ALL_MODEL_NAMES},
        })

        # 添加单模型权重（全部给一个模型）
        for name in ALL_MODEL_NAMES:
            w = {n: 0.0 for n in ALL_MODEL_NAMES}
            w[name] = 1.0
            weight_configs.append({
                'tag': f'single_{name}',
                'weights': w,
            })

        # 随机搜索
        rng = np.random.default_rng(RANDOM_SEED)
        for i in range(n_random):
            raw = rng.random(n_models)
            w = raw / raw.sum()
            weight_configs.append({
                'tag': f'random_{i:04d}',
                'weights': {name: round(float(w[j]), 6)
                           for j, name in enumerate(ALL_MODEL_NAMES)},
            })

    return weight_configs


def select_analysis_points(results, n_points=N_ANALYSIS_POINTS):
    """
    从所有评估结果中选取 N 个代表性分析点

    选取策略:
        1. 最优 RMSE 点
        2. 最优 F1 点
        3. 最优综合得分点
        4. Pareto 前沿中点
        5. 当前系统默认权重点

    返回:
        list of dict: 选取的分析点
    """
    if not results:
        return []

    analysis_points = []
    selected_tags = set()

    # 1. 最优 RMSE 点
    best_rmse = min(results, key=lambda r: r['rmse'])
    if best_rmse['tag'] not in selected_tags:
        analysis_points.append({**best_rmse, 'selection_reason': '最优 RMSE'})
        selected_tags.add(best_rmse['tag'])

    # 2. 最优 F1 点
    best_f1 = max(results, key=lambda r: r['f1_at_k'])
    if best_f1['tag'] not in selected_tags:
        analysis_points.append({**best_f1, 'selection_reason': '最优 F1@K'})
        selected_tags.add(best_f1['tag'])

    # 3. 最优综合得分点
    best_composite = max(results, key=lambda r: r['composite_score'])
    if best_composite['tag'] not in selected_tags:
        analysis_points.append({**best_composite, 'selection_reason': '最优综合得分'})
        selected_tags.add(best_composite['tag'])

    # 4. 找 Pareto 前沿点
    pareto_points = find_pareto_frontier(results)
    for p in pareto_points:
        if len(analysis_points) >= n_points:
            break
        if p['tag'] not in selected_tags:
            analysis_points.append({**p, 'selection_reason': 'Pareto 前沿'})
            selected_tags.add(p['tag'])

    # 5. 补充当前系统点
    for tag_suffix in ['current_low', 'current_mid', 'current_high']:
        if len(analysis_points) >= n_points:
            break
        for r in results:
            if r.get('tag', '').startswith(tag_suffix) and r['tag'] not in selected_tags:
                analysis_points.append({**r, 'selection_reason': f'系统默认 ({tag_suffix})'})
                selected_tags.add(r['tag'])
                break

    # 如果还不够，补充均匀采样点
    if len(analysis_points) < n_points:
        sorted_results = sorted(results, key=lambda r: r['composite_score'], reverse=True)
        for r in sorted_results:
            if len(analysis_points) >= n_points:
                break
            if r['tag'] not in selected_tags:
                analysis_points.append({**r, 'selection_reason': '高综合得分'})
                selected_tags.add(r['tag'])

    return analysis_points[:n_points]


def find_pareto_frontier(results):
    """
    寻找 Pareto 前沿 (最小化 RMSE, 最大化 F1)
    返回非支配解列表
    """
    pareto = []
    for i, r1 in enumerate(results):
        dominated = False
        for j, r2 in enumerate(results):
            if i == j:
                continue
            # r2 支配 r1 的条件: r2 的 RMSE <= r1 的 RMSE 且 r2 的 F1 >= r1 的 F1
            # (至少一个严格)
            if (r2['rmse'] <= r1['rmse'] and r2['f1_at_k'] >= r1['f1_at_k'] and
                (r2['rmse'] < r1['rmse'] or r2['f1_at_k'] > r1['f1_at_k'])):
                dominated = True
                break
        if not dominated:
            pareto.append(r1)

    return pareto


# ═══════════════════════════════════════════════════════════════
# 5. 模型初始化
# ═══════════════════════════════════════════════════════════════

def init_models(mode, ctx):
    """
    初始化并训练所有子模型

    参数:
        mode: '2' | 'all' | 'improved'
        ctx: 数据上下文

    返回:
        dict: {model_name: model_object}
    """
    models = {}

    if mode == '2':
        # 双模型模式: 只需要 User-CF 和 Item-CF 各一个
        # 使用改进版本（效果更好）
        print(f"\n{'=' * 60}")
        print(f"[模型初始化] 双模型模式 (User-CF + Item-CF)")
        print(f"{'=' * 60}")

        models['user_cf'] = ImprovedUserCF(n_neighbors=DEFAULT_N_NEIGHBORS, use_stability=True)
        models['user_cf'].train(ctx)

        models['item_cf'] = ImprovedItemCF(n_neighbors=DEFAULT_N_NEIGHBORS)
        models['item_cf'].train(ctx)

    elif mode == 'improved':
        # 改进版模式: 改进 User-CF + 改进 Item-CF + 改进 Slope One + SVD + Turbo-CF
        print(f"\n{'=' * 60}")
        print(f"[模型初始化] 改进版模式 ({len(IMPROVED_MODEL_NAMES)} 个子模型)")
        print(f"{'=' * 60}")

        models['improved_user_cf'] = ImprovedUserCF(
            n_neighbors=DEFAULT_N_NEIGHBORS, use_stability=True
        )
        models['improved_user_cf'].train(ctx)

        models['improved_item_cf'] = ImprovedItemCF(n_neighbors=DEFAULT_N_NEIGHBORS)
        models['improved_item_cf'].train(ctx)

        models['improved_slope_one'] = ImprovedSlopeOne(
            n_neighbors=DEFAULT_N_NEIGHBORS, svd_factors=50
        )
        models['improved_slope_one'].train(ctx)

        models['svd'] = SVDModel(n_factors=50)
        models['svd'].train(ctx)

        turbo_cf_model_path = os.path.join(MODEL_DIR, 'turbo_cf_model.pkl')
        models['turbo_cf'] = TurboCFModel(model_path=turbo_cf_model_path)
        models['turbo_cf'].train(ctx)

    else:
        # 全模型模式: 训练所有 7 个子模型
        print(f"\n{'=' * 60}")
        print(f"[模型初始化] 全模型模式 ({len(ALL_MODEL_NAMES)} 个子模型)")
        print(f"{'=' * 60}")

        models['traditional_user_cf'] = TraditionalUserCF(n_neighbors=DEFAULT_N_NEIGHBORS)
        models['traditional_user_cf'].train(ctx)

        models['improved_user_cf'] = ImprovedUserCF(
            n_neighbors=DEFAULT_N_NEIGHBORS, use_stability=True
        )
        models['improved_user_cf'].train(ctx)

        models['traditional_item_cf'] = TraditionalItemCF(n_neighbors=DEFAULT_N_NEIGHBORS)
        models['traditional_item_cf'].train(ctx)

        models['improved_item_cf'] = ImprovedItemCF(n_neighbors=DEFAULT_N_NEIGHBORS)
        models['improved_item_cf'].train(ctx)

        models['traditional_slope_one'] = TraditionalSlopeOne()
        models['traditional_slope_one'].train(ctx)

        models['improved_slope_one'] = ImprovedSlopeOne(
            n_neighbors=DEFAULT_N_NEIGHBORS, svd_factors=50
        )
        models['improved_slope_one'].train(ctx)

        models['svd'] = SVDModel(n_factors=50)
        models['svd'].train(ctx)

    return models


# ═══════════════════════════════════════════════════════════════
# 6. 结果导出
# ═══════════════════════════════════════════════════════════════

def save_results(all_results, analysis_points, pareto_frontier, metadata, output_dir):
    """保存所有评估结果到文件"""
    os.makedirs(output_dir, exist_ok=True)
    analysis_dir = os.path.join(output_dir, 'analysis_points')
    os.makedirs(analysis_dir, exist_ok=True)

    # ── 1. 完整评估结果 JSON ──
    output = {
        'metadata': metadata,
        'optimization_summary': {
            'total_configurations': len(all_results),
            'pareto_frontier_size': len(pareto_frontier),
            'selected_analysis_points': len(analysis_points),
            'best_by_rmse': {
                'tag': min(all_results, key=lambda r: r['rmse']).get('tag', ''),
                'rmse': min(all_results, key=lambda r: r['rmse'])['rmse'],
                'weights': min(all_results, key=lambda r: r['rmse'])['weights'],
            },
            'best_by_f1': {
                'tag': max(all_results, key=lambda r: r['f1_at_k']).get('tag', ''),
                'f1': max(all_results, key=lambda r: r['f1_at_k'])['f1_at_k'],
                'weights': max(all_results, key=lambda r: r['f1_at_k'])['weights'],
            },
            'best_by_composite': {
                'tag': max(all_results, key=lambda r: r['composite_score']).get('tag', ''),
                'score': max(all_results, key=lambda r: r['composite_score'])['composite_score'],
                'weights': max(all_results, key=lambda r: r['composite_score'])['weights'],
            },
        },
        'current_system_weights': CURRENT_WEIGHTS,
        'all_results': all_results,
        'pareto_frontier': pareto_frontier,
        'analysis_points': analysis_points,
    }

    json_path = os.path.join(output_dir, 'evaluation_results.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  评估结果: {json_path}")

    # ── 2. 权重优化 CSV ──
    csv_rows = []
    for r in all_results:
        w_str = '; '.join([f'{k}={v:.4f}' for k, v in r['weights'].items()])
        csv_rows.append({
            'tag': r.get('tag', ''),
            'weights': w_str,
            'rmse': r['rmse'],
            'mae': r['mae'],
            'precision@10': r['precision_at_k'],
            'recall@10': r['recall_at_k'],
            'f1@10': r['f1_at_k'],
            'coverage': r['coverage'],
            'composite_score': r['composite_score'],
            'pred_time': r['pred_time'],
            'rec_time': r['rec_time'],
        })

    df = pd.DataFrame(csv_rows)
    csv_path = os.path.join(output_dir, 'weight_optimization.csv')
    df.to_csv(csv_path, index=False)
    print(f"  权重优化 CSV: {csv_path}")

    # ── 3. 最优权重摘要 JSON ──
    best_weights = {
        'metadata': {
            'dataset': metadata.get('dataset', {}),
            'mode': metadata.get('mode', ''),
        },
        'optimal_weights': {
            'by_rmse': {
                'weights': min(all_results, key=lambda r: r['rmse'])['weights'],
                'rmse': min(all_results, key=lambda r: r['rmse'])['rmse'],
                'tag': min(all_results, key=lambda r: r['rmse']).get('tag', ''),
            },
            'by_f1': {
                'weights': max(all_results, key=lambda r: r['f1_at_k'])['weights'],
                'f1': max(all_results, key=lambda r: r['f1_at_k'])['f1_at_k'],
                'tag': max(all_results, key=lambda r: r['f1_at_k']).get('tag', ''),
            },
            'by_composite': {
                'weights': max(all_results, key=lambda r: r['composite_score'])['weights'],
                'score': max(all_results, key=lambda r: r['composite_score'])['composite_score'],
                'tag': max(all_results, key=lambda r: r['composite_score']).get('tag', ''),
            },
        },
        'current_system_weights': CURRENT_WEIGHTS,
        'recommendation': None,
    }

    # 给一个推荐权重
    best_composite = max(all_results, key=lambda r: r['composite_score'])
    best_weights['recommendation'] = best_composite['weights']

    optimal_path = os.path.join(output_dir, 'optimal_weights.json')
    with open(optimal_path, 'w', encoding='utf-8') as f:
        json.dump(best_weights, f, indent=2, ensure_ascii=False)
    print(f"  最优权重: {optimal_path}")

    # ── 4. Pareto 前沿 CSV ──
    pareto_rows = []
    for r in pareto_frontier:
        w_str = '; '.join([f'{k}={v:.4f}' for k, v in r['weights'].items()])
        pareto_rows.append({
            'tag': r.get('tag', ''),
            'weights': w_str,
            'rmse': r['rmse'],
            'mae': r['mae'],
            'f1@10': r['f1_at_k'],
            'precision@10': r['precision_at_k'],
            'recall@10': r['recall_at_k'],
            'coverage': r['coverage'],
            'composite_score': r['composite_score'],
        })

    if pareto_rows:
        df_pareto = pd.DataFrame(pareto_rows)
        pareto_csv = os.path.join(output_dir, 'pareto_frontier.csv')
        df_pareto.to_csv(pareto_csv, index=False)
        print(f"  Pareto 前沿: {pareto_csv}")

    # ── 5. 分析点详细 JSON ──
    for i, point in enumerate(analysis_points):
        point_path = os.path.join(
            analysis_dir,
            f'point_{i+1:03d}_{point.get("tag", "unknown")}.json'
        )
        with open(point_path, 'w', encoding='utf-8') as f:
            json.dump(point, f, indent=2, ensure_ascii=False)

    print(f"  分析点: {len(analysis_points)} 个 -> {analysis_dir}")

    # ── 6. 简化版权重-指标关系图数据 ──
    # 输出一个 JSON 专门给图表使用
    if len(all_results) > 0 and 'user_cf' in all_results[0].get('weights', {}):
        chart_data = []
        for r in sorted(all_results, key=lambda x: x['weights'].get('user_cf', 0)):
            chart_data.append({
                'w_user': r['weights'].get('user_cf', None),
                'w_item': r['weights'].get('item_cf', None),
                'rmse': r['rmse'],
                'mae': r['mae'],
                'precision@10': r['precision_at_k'],
                'recall@10': r['recall_at_k'],
                'f1@10': r['f1_at_k'],
                'coverage': r['coverage'],
                'composite_score': r['composite_score'],
            })

        chart_path = os.path.join(output_dir, 'weight_vs_metrics.json')
        with open(chart_path, 'w', encoding='utf-8') as f:
            json.dump(chart_data, f, indent=2, ensure_ascii=False)
        print(f"  图表数据: {chart_path}")

    return output


def print_summary(all_results, analysis_points, elapsed, metadata, mode):
    """打印评估结果摘要到控制台"""
    print(f"\n\n{'=' * 80}")
    print(f"     混合推荐权重优化评估完成")
    print(f"{'=' * 80}")

    print(f"\n数据集: {metadata['dataset']['train_size']:,} 训练 / "
          f"{metadata['dataset']['test_size']:,} 测试")
    if mode == '2':
        mode_str = '双模型 (User-CF + Item-CF)'
    elif mode == 'improved':
        mode_str = f'改进版 ({len(IMPROVED_MODEL_NAMES)} 个子模型: ' + ', '.join(IMPROVED_MODEL_NAMES) + ')'
    else:
        mode_str = f'全模型 ({len(ALL_MODEL_NAMES)} 个子模型)'
    print(f"模式: {mode_str}")
    print(f"权重配置数: {len(all_results)}")
    print(f"总耗时: {elapsed:.2f} 秒")

    print(f"\n{'─' * 80}")
    print(f"{'最优结果对比':^80}")
    print(f"{'─' * 80}")
    if mode == '2':
        print(f"{'标准':<20} {'w_user':<12} {'w_item':<12} {'RMSE':<10} {'MAE':<10} {'F1@10':<10} {'Coverage':<10}")
    else:
        print(f"{'标准':<20} {'权重':<24} {'RMSE':<10} {'MAE':<10} {'F1@10':<10} {'Coverage':<10}")
    print(f"{'─' * 80}")

    # 找到几个关键点打印
    if mode == '2':
        key_tags = ['current_low', 'current_mid', 'current_high']
        display_points = [
            ('当前(低活跃)', 'current_low'),
            ('当前(一般)', 'current_mid'),
            ('当前(高活跃)', 'current_high'),
            ('最优(RMSE)', 'opt_rmse'),
            ('最优(F1)', 'opt_f1'),
        ]
    else:
        key_tags = ['uniform']
        display_points = [
            ('均匀权重', 'uniform'),
            ('最优(RMSE)', 'opt_rmse'),
            ('最优(F1)', 'opt_f1'),
        ]
    key_results = {}

    for r in all_results:
        tag = r.get('tag', '')
        if mode == '2':
            w_user = r['weights'].get('user_cf', 'N/A')
            w_item = r['weights'].get('item_cf', 'N/A')
        else:
            w_str = ', '.join([f'{k}={v:.2f}' for k, v in sorted(r['weights'].items()) if v > 0.1])
            w_user = w_str[:24]
            w_item = ''

        # 记录关键点
        if tag in key_tags:
            key_results[tag] = r
        if tag == 'uniform':
            key_results['uniform'] = r
        if r == min(all_results, key=lambda x: x['rmse']):
            key_results['opt_rmse'] = r
        if r == max(all_results, key=lambda x: x['f1_at_k']):
            key_results['opt_f1'] = r

    for label, key in display_points:
        r = key_results.get(key)
        if r is None:
            continue

        if mode == '2':
            w_user_val = r['weights'].get('user_cf', 0)
            w_item_val = r['weights'].get('item_cf', 0)
            print(f"{label:<20} {w_user_val:<12.4f} {w_item_val:<12.4f} "
                  f"{r['rmse']:<10.4f} {r['mae']:<10.4f} "
                  f"{r['f1_at_k']:<10.4f} {r['coverage']:<10.4f}")
        else:
            w_str = ', '.join([f'{k}={v:.2f}' for k, v in sorted(r['weights'].items()) if v > 0.05])
            print(f"{label:<20} {w_str:<24} "
                  f"{r['rmse']:<10.4f} {r['mae']:<10.4f} "
                  f"{r['f1_at_k']:<10.4f} {r['coverage']:<10.4f}")

    print(f"{'─' * 80}")

    print(f"\n最优权重（基于综合得分）:")
    best = max(all_results, key=lambda r: r['composite_score'])
    for k, v in sorted(best['weights'].items()):
        print(f"  {k}: {v:.4f}")
    print(f"  → RMSE={best['rmse']:.4f}, F1@10={best['f1_at_k']:.4f}")

    print(f"\n分析点 ({len(analysis_points)} 个):")
    for i, p in enumerate(analysis_points):
        print(f"  {i+1}. [{p.get('tag', 'N/A')}] {p.get('selection_reason', '')}")

    print(f"\n{'=' * 80}\n")


# ═══════════════════════════════════════════════════════════════
# 7. 主流程
# ═══════════════════════════════════════════════════════════════

def run_weight_optimization(
    mode='2',
    test_size=None,
    top_n=DEFAULT_TOP_N,
    grid_step=DEFAULT_GRID_STEP,
    n_random=DEFAULT_N_RANDOM,
    output_dir=None,
):
    """
    运行权重优化评估主流程

    参数:
        mode: '2' 或 'all'
        test_size: 限制测试集数量（调试用）
        top_n: Top-N 推荐列表长度
        grid_step: 双模型网格步长
        n_random: 全模型随机搜索次数
        output_dir: 输出目录（默认为 evaluation_results/hybrid_weights/）

    返回:
        (all_results, analysis_points, pareto_frontier)
    """
    if output_dir is None:
        output_dir = os.path.join(DEFAULT_OUTPUT_DIR, 'hybrid_weights')

    overall_start = time.time()

    # ── 1. 加载数据 ──
    ratings_df, movies_df = load_all_data()

    # ── 2. 划分训练/测试集 ──
    train_df, test_df = train_test_split_by_user(ratings_df, test_ratio=0.2)

    if test_size and test_size < len(test_df):
        test_df = test_df.iloc[:test_size]
        print(f"  [限制] 测试集截断至 {test_size} 条")

    # ── 3. 构建数据上下文 ──
    ctx = build_rating_matrices(train_df)

    # ── 4. 训练子模型 ──
    models = init_models(mode, ctx)

    # ── 5. 构建混合预测器 ──
    if mode == '2':
        model_names = ['user_cf', 'item_cf']
    elif mode == 'improved':
        model_names = IMPROVED_MODEL_NAMES
    else:
        model_names = ALL_MODEL_NAMES

    hybrid = HybridPredictor(models, model_names)

    # ── 6. 生成权重搜索网格 ──
    weight_configs = get_weight_grid(mode=mode, grid_step=grid_step, n_random=n_random)
    print(f"\n{'=' * 60}")
    print(f"[权重搜索] 模式={mode}, 配置数={len(weight_configs)}")
    print(f"{'=' * 60}")

    # ── 7. 评估各权重组合 ──
    all_results = []
    n_configs = len(weight_configs)

    for idx, config in enumerate(weight_configs):
        tag = config['tag']
        weights = config['weights']

        if (idx + 1) % max(1, n_configs // 20) == 0 or idx == 0 or idx == n_configs - 1:
            print(f"\n  权重 [{idx+1}/{n_configs}] {tag}: ", end='')
            for k, v in sorted(weights.items()):
                print(f"{k}={v:.3f} ", end='')
            print()

        t0 = time.time()
        result = evaluate_hybrid_weights(
            hybrid, test_df, train_df, ctx, weights,
            top_n=top_n,
        )
        result['tag'] = tag
        result['eval_time'] = time.time() - t0
        all_results.append(result)

    # ── 8. 选取分析点 ──
    analysis_points = select_analysis_points(all_results, n_points=N_ANALYSIS_POINTS)

    # ── 9. 找出 Pareto 前沿 ──
    pareto_frontier = find_pareto_frontier(all_results)

    # ── 10. 构建元数据 ──
    metadata = {
        'dataset': {
            'train_size': len(train_df),
            'test_size': len(test_df),
            'n_users': int(train_df['user_id'].nunique()),
            'n_movies': int(train_df['movie_id'].nunique()),
        },
        'mode': mode,
        'test_size_limit': test_size,
        'top_n': top_n,
        'grid_step': grid_step,
        'n_random': n_random if mode != '2' else None,
        'n_weight_configs': len(weight_configs),
        'total_time': time.time() - overall_start,
        'parameters': {
            'n_neighbors': DEFAULT_N_NEIGHBORS,
            'n_analysis_points': N_ANALYSIS_POINTS,
        },
    }

    total_elapsed = time.time() - overall_start

    # ── 11. 保存结果 ──
    save_results(all_results, analysis_points, pareto_frontier, metadata, output_dir)

    # ── 12. 打印摘要 ──
    print_summary(all_results, analysis_points, total_elapsed, metadata, mode)

    return all_results, analysis_points, pareto_frontier


def main():
    parser = argparse.ArgumentParser(
        description='混合推荐算法权重优化评估脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/evaluation/evaluate_hybrid_weights.py
  python scripts/evaluation/evaluate_hybrid_weights.py --mode 2 --grid-step 0.02
  python scripts/evaluation/evaluate_hybrid_weights.py --mode all --n-random 500
  python scripts/evaluation/evaluate_hybrid_weights.py --test-size 5000 --top-n 20
        """,
    )
    parser.add_argument('--mode', type=str, choices=['2', 'all', 'improved'], default='2',
                        help='混合模式: 2 = User-CF+Item-CF (默认), all = 全部子模型, improved = 改进版 (5个子模型)')
    parser.add_argument('--test-size', type=int, default=None,
                        help='限制测试样本数 (调试用)')
    parser.add_argument('--top-n', type=int, default=DEFAULT_TOP_N,
                        help=f'推荐列表长度 (默认: {DEFAULT_TOP_N})')
    parser.add_argument('--grid-step', type=float, default=DEFAULT_GRID_STEP,
                        help=f'双模型网格搜索步长 (默认: {DEFAULT_GRID_STEP})')
    parser.add_argument('--n-random', type=int, default=DEFAULT_N_RANDOM,
                        help=f'全模型随机搜索次数 (默认: {DEFAULT_N_RANDOM})')
    parser.add_argument('--output-dir', type=str, default=None,
                        help='输出目录 (默认: evaluation_results/hybrid_weights/)')

    args = parser.parse_args()

    run_weight_optimization(
        mode=args.mode,
        test_size=args.test_size,
        top_n=args.top_n,
        grid_step=args.grid_step,
        n_random=args.n_random,
        output_dir=args.output_dir,
    )


if __name__ == '__main__':
    main()