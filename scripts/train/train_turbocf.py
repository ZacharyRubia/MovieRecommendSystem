#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_turbocf.py - Turbo-CF（加速协同过滤）训练脚本

基于 K-Means 用户聚类的加速协同过滤算法（简称 Turbo-CF）：
  1. 离线聚类：将用户历史评分向量通过 K-Means 划分为 C 个簇
  2. 目标用户归属：计算用户向量与各簇中心的距离，分配至最近簇
  3. 局部邻居筛选：仅在簇内（或扩展至邻近 t 个簇）搜索相似用户
  4. 评分预测：采用加权平均公式进行评分预测

复杂度：O(U) → O(U/C)，当 C=100 时可获得两个数量级加速。
当用户规模超过 10^4 时自动启用 Turbo-CF。

算法选择：本系统采用基于 K-Means 用户聚类的 Turbo-CF 方法，
通过离线预处理将邻居搜索空间从全量压缩至局部子集。
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
from scipy.sparse import csr_matrix, issparse
from sklearn.cluster import KMeans
from sklearn.metrics.pairwise import cosine_similarity, cosine_distances
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

# ─── Turbo-CF 默认参数 ──────────────────────────────────────
# 当用户数超过此阈值时自动启用 Turbo-CF
TURBO_CF_USER_THRESHOLD = 10000
# 默认簇数（经验值：~sqrt(N_users)/10，结合实际数据集调整）
DEFAULT_N_CLUSTERS = 50
# 默认局部扩展范围：0=仅本簇, 1=本簇+最近1个簇, ...
DEFAULT_EXTEND_RANGE = 1
# 默认邻居数
DEFAULT_N_NEIGHBORS = 30


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


# ============================================================
# 核心 Turbo-CF 训练
# ============================================================

def train_turbo_cf(train_df, n_clusters=DEFAULT_N_CLUSTERS,
                   n_neighbors=DEFAULT_N_NEIGHBORS,
                   extend_range=DEFAULT_EXTEND_RANGE,
                   kmeans_max_iter=300, kmeans_n_init=10):
    """
    Turbo-CF 训练主函数

    Parameters
    ----------
    train_df : pd.DataFrame
        训练数据，包含 user_id, movie_id, rating 三列
    n_clusters : int
        K-Means 聚类数 C。较大的 C 可提高精度但降低加速比。
    n_neighbors : int
        每个用户的邻居数 K
    extend_range : int
        局部扩展范围 t。0=仅本簇, 1=本簇+最近1个簇, ...
    kmeans_max_iter : int
        K-Means 最大迭代次数
    kmeans_n_init : int
        K-Means 不同初始化的次数

    Returns
    -------
    dict
        包含模型参数的字典
    """
    print("\n" + "=" * 60)
    print(f"[Turbo-CF 训练] 簇数: {n_clusters} | 邻居数: {n_neighbors} "
          f"| 扩展范围: {extend_range} | 进程数: {_N_CPUS}")
    print(f"  核心思想: K-Means 用户聚类压缩邻居搜索空间 O(U) → O(U/C)")

    start_time = time.time()

    # ─── 1. 构建映射和数据结构 ────────────────────────────────
    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df)

    print(f"  用户数: {n_users}  |  电影数: {n_movies}  |  评分: {len(train_df)}")
    turbo_enabled = n_users >= TURBO_CF_USER_THRESHOLD
    status_msg = f"已启用 (用户数 >= {TURBO_CF_USER_THRESHOLD})" if turbo_enabled else f"未启用 (用户数 < {TURBO_CF_USER_THRESHOLD})"
    print(f"  Turbo-CF 模式: {status_msg}")

    # ─── 2. 计算用户均值（中心化用） ──────────────────────────
    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts
    print(f"  用户均值计算完成")

    # ─── 3. 构建用户-电影评分矩阵 ──────────────────────────
    # 用于聚类和相似度计算
    centered_vals = r_val - user_means[u_idx]
    sparse_R = csr_matrix(
        (centered_vals, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )
    non_zero = sparse_R.nnz
    density = non_zero / (n_users * n_movies) * 100
    print(f"  稀疏矩阵: ({n_users}, {n_movies}), 非零: {non_zero:,} ({density:.4f}%)")

    # ─── 步骤一：离线聚类 ─────────────────────────────────
    # 使用 K-Means 对用户进行聚类
    # 特征向量：用户在所有电影上的中心化评分（CSR 行向量）
    print(f"\n  [步骤一] K-Means 用户聚类 (C={n_clusters})...")

    cluster_start = time.time()
    kmeans = KMeans(
        n_clusters=n_clusters,
        init='k-means++',
        max_iter=kmeans_max_iter,
        n_init=kmeans_n_init,
        random_state=42,
        algorithm='elkan',
    )

    # 对于 K-Means 拟合稀疏数据，需要转换为稠密或使用 MiniBatch K-Means
    # 当电影数很大时，直接使用稀疏 CSR 矩阵进行聚类
    # sklearn 的 K-Means 支持 CSR 矩阵
    user_labels = kmeans.fit_predict(sparse_R)

    # 簇中心 (n_clusters, n_movies)
    cluster_centers = kmeans.cluster_centers_
    cluster_time = time.time() - cluster_start
    print(f"  K-Means 聚类完成 | 耗时: {cluster_time:.2f}秒")

    # 统计各簇用户数
    cluster_sizes = np.bincount(user_labels, minlength=n_clusters)
    for c in range(n_clusters):
        print(f"    簇 {c}: {cluster_sizes[c]} 个用户")
    print(f"    簇平均规模: {n_users / n_clusters:.0f} 个用户")

    # ─── 步骤二/三：簇内邻居搜索 ────────────────────────────
    print(f"\n  [步骤二/三] 簇内局部邻居搜索 (扩展范围 t={extend_range})...")

    # 计算簇间距离矩阵，用于扩展邻居搜索范围
    # 簇中心之间的余弦距离
    cluster_dist_matrix = cosine_distances(cluster_centers)
    # 对每个簇，按距离排序得到邻近簇列表（排除自身）
    cluster_neighbors_ordered = np.argsort(cluster_dist_matrix, axis=1)
    # 移除自身（距离为0）
    cluster_neighbors_ordered = cluster_neighbors_ordered[:, 1:]

    # 为每个用户确定其"扩展搜索空间"包含的簇
    # 即：用户所在簇 + 最近邻的 extend_range 个簇
    effective_cluster_set = {}
    for c in range(n_clusters):
        clusters_to_search = [c] + list(cluster_neighbors_ordered[c, :extend_range])
        effective_cluster_set[c] = clusters_to_search

    # 为每个簇内的用户构建局部相似度矩阵
    # 采用分簇处理策略以控制内存

    # 存储结果：每个用户的 top-K 邻居
    user_neighbors = {}  # {uid: [(neighbor_uid, similarity), ...]}

    # 预处理用户所在簇的映射
    user_label_map = {}  # {uid: cluster_id}
    for i, uid in enumerate(all_users):
        user_label_map[int(uid)] = int(user_labels[i])

    # 对每个簇，在其扩展搜索空间内计算用户相似度
    # 使用分簇并行处理
    def _process_cluster(cluster_id):
        """处理单个簇：在该簇的扩展搜索空间内计算所有用户的邻居"""
        with threadpool_limits(limits=1, user_api='blas'):
            local_result = {}

            # 获取此簇的用户索引
            cluster_user_indices = np.where(user_labels == cluster_id)[0]
            if len(cluster_user_indices) == 0:
                return local_result

            # 获取扩展搜索空间包含的用户索引
            search_clusters = effective_cluster_set[cluster_id]
            search_user_indices = np.where(
                np.isin(user_labels, search_clusters)
            )[0]

            # 如果搜索空间等于自身，使用所有用户（退化情况）
            if len(search_user_indices) == 0:
                search_user_indices = cluster_user_indices

            # 在搜索空间中提取用户-电影子矩阵
            # 使用行选择构建子矩阵
            sub_R = sparse_R[search_user_indices]

            # 计算搜索空间中用户与簇内用户的余弦相似度
            # 使用稀疏矩阵的余弦相似度计算
            # shape: (n_cluster_users, n_search_users)
            sub_sim = cosine_similarity(
                sparse_R[cluster_user_indices],
                sub_R
            )

            # 对每个簇内用户，选择 top-K 邻居
            for local_i, user_idx_in_cluster in enumerate(cluster_user_indices):
                uid = int(all_users[user_idx_in_cluster])
                sim_row = sub_sim[local_i]

                # 排除自身（相似度为1）
                # 找到自身在搜索空间中的位置
                self_pos = np.where(search_user_indices == user_idx_in_cluster)[0]
                if len(self_pos) > 0:
                    sim_row[self_pos[0]] = -1.0

                # 选择 top-K
                k_actual = min(n_neighbors, len(sim_row))
                if k_actual == 0:
                    local_result[uid] = []
                    continue

                # 使用 argpartition 加速 top-K 选择
                top_indices = np.argpartition(sim_row, -k_actual)[-k_actual:]
                top_sims = sim_row[top_indices]

                # 按相似度降序排序
                sort_order = np.argsort(-top_sims)
                top_indices = top_indices[sort_order]
                top_sims = top_sims[sort_order]

                # 过滤掉非正相似度
                neighbor_list = []
                for j in range(k_actual):
                    if top_sims[j] > 0:
                        neighbor_uid = int(all_users[search_user_indices[top_indices[j]]])
                        neighbor_list.append((neighbor_uid, float(top_sims[j])))

                # 归一化权重
                if neighbor_list:
                    total_sim = sum(abs(s) for _, s in neighbor_list)
                    if total_sim > 0:
                        neighbor_list = [(nuid, s / total_sim) for nuid, s in neighbor_list]

                local_result[uid] = neighbor_list

            return local_result

    # 并行处理各簇
    from joblib import Parallel, delayed
    cluster_results = Parallel(n_jobs=min(_N_CPUS, n_clusters), prefer='threads', verbose=0)(
        delayed(_process_cluster)(c) for c in range(n_clusters)
    )

    # 聚合结果
    for cr in cluster_results:
        user_neighbors.update(cr)

    # 统计邻居覆盖率
    users_with_neighbors = sum(1 for v in user_neighbors.values() if v)
    total_neighbors = sum(len(v) for v in user_neighbors.values())
    print(f"  邻居搜索完成: {users_with_neighbors}/{n_users} 用户有邻居, "
          f"平均 {total_neighbors / max(1, users_with_neighbors):.1f} 个邻居/用户")

    # ─── 步骤四：RMSE 评估 ──────────────────────────────────
    print(f"\n  [步骤四] 评分预测 & RMSE 计算（{_N_CPUS} 进程并行）...")

    # 构建用户到评分样本索引的映射
    user_to_indices = defaultdict(list)
    for i in range(len(train_df)):
        user_to_indices[int(train_df['user_id'].iloc[i])].append(i)

    user_ids_list = list(user_to_indices.keys())
    n_users_total = len(user_ids_list)

    batch_size = max(200, min(2000, n_users_total // max(1, _N_CPUS)))
    batches = [user_ids_list[i:i + batch_size] for i in range(0, n_users_total, batch_size)]
    print(f"  批次数: {len(batches)} (batch_size={batch_size})")

    def _predict_user_batch(batch_uids):
        """批量预测评分"""
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
                nb_list = user_neighbors.get(uid, [])
                if not nb_list:
                    # 无邻居时使用用户均值
                    preds_local.extend([user_means[u]] * len(sample_indices))
                    idxs_local.extend(sample_indices)
                    continue

                nb_uids, nb_weights = zip(*nb_list)
                nb_indices = np.array([user2idx.get(nuid, -1) for nuid in nb_uids], dtype=np.int32)
                nb_weights = np.array(nb_weights, dtype=np.float32)

                # 过滤无效邻居
                valid = nb_indices >= 0
                nb_indices = nb_indices[valid]
                nb_weights = nb_weights[valid]

                if len(nb_indices) == 0:
                    preds_local.extend([user_means[u]] * len(sample_indices))
                    idxs_local.extend(sample_indices)
                    continue

                # 获取邻居的评分行并提取目标电影的评分
                neighbor_rows = sparse_R[nb_indices]
                neighbor_csc = neighbor_rows.tocsc()
                sub_R = neighbor_csc[:, movie_cols]

                # 预测：加权平均
                # pred_centered = sum(sim * (r_vi - mean_v)) / sum(|sim|)
                # 使用向量化计算
                pred_centered = np.asarray(sub_R.T.dot(nb_weights))
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
    print(f"  Turbo-CF 训练耗时: {elapsed:.2f} 秒")

    # ─── 构建用户评分电影集 ────────────────────────────────
    user_movies = defaultdict(set)
    for uid_val, mid_val in zip(train_df['user_id'], train_df['movie_id']):
        user_movies[int(uid_val)].add(int(mid_val))

    # ─── 整理模型输出 ──────────────────────────────────────
    # 将用户-簇映射保存为可序列化格式
    user_cluster_map = {int(all_users[i]): int(user_labels[i]) for i in range(n_users)}

    # 将簇中心保存为可序列化格式（转换为列表）
    cluster_centers_list = cluster_centers.tolist()

    print(f"\n  ⚡ Turbo-CF 加速比估计:")
    print(f"    全量搜索复杂度: O({n_users})")
    print(f"    簇内搜索复杂度: O({n_users // n_clusters}) (C={n_clusters})")
    print(f"    理论加速比: {n_users / max(1, n_users // n_clusters):.1f}x")

    return {
        'algorithm': 'turbo_cf',
        'n_clusters': n_clusters,
        'n_neighbors': n_neighbors,
        'extend_range': extend_range,
        'turbo_enabled': turbo_enabled,

        # 聚类相关
        'cluster_centers': cluster_centers_list,       # list of lists, shape (C, n_movies)
        'user_cluster_map': user_cluster_map,          # {uid: cluster_id}
        'cluster_sizes': cluster_sizes.tolist(),       # 各簇用户数
        'cluster_neighbors_ordered': cluster_neighbors_ordered.tolist(),  # (C, C-1) 按距离排序的邻近簇

        # 邻居相关
        'user_neighbors': user_neighbors,              # {uid: [(nuid, sim), ...]}

        # 均值
        'user_means': {int(uid): float(user_means[i]) for i, uid in enumerate(all_users)},

        # 映射
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],

        # 用户已评分电影
        'user_movies': {str(k): list(v) for k, v in user_movies.items()},

        # 训练元信息
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
        'n_users': n_users,
        'n_movies': n_movies,
    }


def save_model(model, name='turbo_cf_model'):
    path = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {path}")
    with open(path, 'wb') as f:
        pickle.dump(model, f)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  模型大小: {size_mb:.2f} MB")

    meta = {k: v for k, v in model.items()
            if isinstance(v, (str, int, float, bool, list))}
    meta['turbo_enabled'] = model.get('turbo_enabled', False)
    meta_path = os.path.join(MODEL_DIR, f'{name}_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  元数据: {meta_path}")
    return path


def main():
    parser = argparse.ArgumentParser(description='Turbo-CF 模型训练（K-Means 用户聚类加速版）')
    parser.add_argument('--n-clusters', type=int, default=DEFAULT_N_CLUSTERS,
                        help=f'K-Means 聚类数 C (default: {DEFAULT_N_CLUSTERS})')
    parser.add_argument('--n-neighbors', type=int, default=DEFAULT_N_NEIGHBORS,
                        help=f'邻居数 K (default: {DEFAULT_N_NEIGHBORS})')
    parser.add_argument('--extend-range', type=int, default=DEFAULT_EXTEND_RANGE,
                        help=f'扩展簇范围 t (default: {DEFAULT_EXTEND_RANGE})')
    parser.add_argument('--n-jobs', type=int, default=None,
                        help='并行进程数')
    parser.add_argument('--kmeans-max-iter', type=int, default=300,
                        help='K-Means 最大迭代次数 (default: 300)')
    parser.add_argument('--kmeans-n-init', type=int, default=10,
                        help='K-Means 初始化次数 (default: 10)')
    parser.add_argument('--model-name', type=str, default='turbo_cf_model',
                        help='模型名称 (default: turbo_cf_model)')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用进程数: {_N_CPUS}")
    print(f"        Turbo-CF 配置: C={args.n_clusters}, K={args.n_neighbors}, t={args.extend_range}")
    print(f"        阈值: >= {TURBO_CF_USER_THRESHOLD} 用户自动启用")

    overall_start = time.time()
    ratings_df = load_data()

    model = train_turbo_cf(
        ratings_df,
        n_clusters=args.n_clusters,
        n_neighbors=args.n_neighbors,
        extend_range=args.extend_range,
        kmeans_max_iter=args.kmeans_max_iter,
        kmeans_n_init=args.kmeans_n_init,
    )
    save_model(model, args.model_name)

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  Turbo-CF 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()