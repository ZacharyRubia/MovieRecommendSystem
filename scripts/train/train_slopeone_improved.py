#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_slopeone_improved.py - 改进的 Slope One 算法（基于邻域筛选）
对应 2.2.6 节

改进点：
1. 先为目标用户筛选 M 个最相似邻居用户集合 U_nb
2. 仅在邻域内计算局部偏差：dev_{ij}^{local} = mean over U_nb ∩ U_ij of (r_ui - r_uj)
3. 要求 |U_nb ∩ U_ij| >= 3 保证偏差可靠
4. 预测：r̂_uj = mean_{i in S(u)} (r_ui + dev_{ji}^{local})

多线程实现
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
from scipy.sparse import csr_matrix, dia_matrix
from threadpoolctl import threadpool_limits
from train_logger import log_output, verbose_init, verbose_step, verbose_close

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


def load_data(verbose=False):
    print("=" * 60)
    verbose_step("数据加载", "开始读取评分数据文件...", verbose)
    print("[加载数据] 读取评分数据...")
    ratings_df = pd.read_csv(
        os.path.join(DATA_DIR, 'test_ratings.csv'),
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )
    n_records = len(ratings_df)
    n_users = ratings_df['user_id'].nunique()
    n_movies = ratings_df['movie_id'].nunique()
    print(f"  评分数据: {n_records} 条, "
          f"用户 {n_users} 个, "
          f"电影 {n_movies} 部")
    verbose_step("数据加载", f"完成: {n_records} 条, {n_users} 用户, {n_movies} 电影", verbose)
    return ratings_df


def build_mappings(train_df, verbose=False):
    verbose_step("映射构建", "开始构建用户-电影映射表...", verbose)
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
    verbose_step("映射构建", f"完成: {n_users} 用户, {n_movies} 电影, {len(r_val)} 条评分", verbose)
    return (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
            n_users, n_movies, u_idx, m_idx, r_val)


def compute_user_similarity(sparse_R_centered, top_k=50, chunk_size=2000, verbose=False):
    """
    使用去均值向量的余弦相似度（近似 Pearson）计算用户相似度
    用于 Slope One 邻居筛选
    """
    verbose_step("用户相似度", "开始计算用户相似度矩阵...", verbose)
    from sklearn.metrics.pairwise import cosine_similarity

    n_users = sparse_R_centered.shape[0]

    norms = np.sqrt(sparse_R_centered.multiply(sparse_R_centered).sum(axis=1)).A.ravel()
    norms[norms == 0] = 1.0
    inv_norms = dia_matrix((1.0 / norms, [0]), shape=(n_users, n_users))
    normalized = inv_norms @ sparse_R_centered
    verbose_step("用户相似度", "向量归一化完成", verbose)

    chunk_ranges = [(i, min(i + chunk_size, n_users)) for i in range(0, n_users, chunk_size)]
    n_chunks = len(chunk_ranges)
    verbose_step("用户相似度", f"分 {n_chunks} 块并行计算余弦相似度", verbose)

    def _process_chunk(c_start, c_end):
        with threadpool_limits(limits=1, user_api='blas'):
            chunk = normalized[c_start:c_end]
            sim_chunk = cosine_similarity(chunk, normalized)
        np.maximum(sim_chunk, 0, out=sim_chunk)

        chunk_rows = c_end - c_start
        for local_i in range(chunk_rows):
            global_i = c_start + local_i
            sim_chunk[local_i, global_i] = 0.0

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
                    result[global_i] = {int(j): float(row[j]) for j in pos}
        return result

    from joblib import Parallel, delayed
    chunk_results = Parallel(n_jobs=_N_CPUS, prefer='threads', verbose=0)(
        delayed(_process_chunk)(c_start, c_end) for c_start, c_end in chunk_ranges
    )

    user_sim = {}
    for cr in chunk_results:
        user_sim.update(cr)

    verbose_step("用户相似度", f"完成: {len(user_sim)} 用户有邻居", verbose)
    return user_sim


def train_slopeone_improved(train_df, n_neighbors=30, min_common=3, chunk_size=2000, verbose=False):
    """
    改进 Slope One 训练
    - 基于邻域筛选的局部偏差
    - 要求 |U_nb ∩ U_ij| >= 3
    - 预测：r̂_uj = mean_{i in S(u)} (r_ui + dev_{ji}^{local})
    多线程实现
    """
    print("\n" + "=" * 60)
    print(f"[改进 Slope One 训练] 邻居数: {n_neighbors} | "
          f"最小共同用户: {min_common} | 进程数: {_N_CPUS}")

    start_time = time.time()

    verbose_step("改进SlopeOne", "构建映射...", verbose)
    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df, verbose=verbose)

    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts
    print(f"  用户均值计算完成")
    verbose_step("改进SlopeOne", f"用户均值计算完成, {n_users} 用户", verbose)

    # 去均值评分矩阵
    centered_vals = r_val - user_means[u_idx]
    sparse_R_centered = csr_matrix(
        (centered_vals, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )
    sparse_R = csr_matrix(
        (r_val, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )
    non_zero = sparse_R.nnz
    density = non_zero / (n_users * n_movies) * 100
    print(f"  稀疏矩阵: ({n_users}, {n_movies}), 非零: {non_zero:,} ({density:.4f}%)")
    verbose_step("改进SlopeOne", f"稀疏矩阵: ({n_users}, {n_movies}), 非零: {non_zero:,} ({density:.4f}%)", verbose)

    # 计算用户相似度用于邻居筛选
    print(f"\n  [用户相似度计算] 用于邻居筛选...")
    verbose_step("改进SlopeOne", "计算用户相似度用于邻居筛选...", verbose)
    user_sim = compute_user_similarity(sparse_R_centered, top_k=n_neighbors, chunk_size=chunk_size, verbose=verbose)
    print(f"  用户相似度计算完成: {len(user_sim)} 用户有邻居")

    # 按用户组织评分（用于后续局部偏差计算）
    verbose_step("改进SlopeOne", "按用户组织评分数据...", verbose)
    user_ratings_dict = defaultdict(dict)
    for uid_val, mid_val, rating in zip(
        train_df['user_id'], train_df['movie_id'], train_df['rating']
    ):
        ui = user2idx.get(uid_val)
        mi = movie2idx.get(mid_val)
        if ui is not None and mi is not None:
            user_ratings_dict[ui][mi] = float(rating)

    # 构建邻居信息用于局部偏差计算
    neighbor_lists = {}
    for ui, neighbors in user_sim.items():
        neighbor_lists[ui] = list(neighbors.keys())

    print(f"\n  [局部偏差计算] 分用户组并行...")
    verbose_step("改进SlopeOne", f"开始并行计算局部偏差矩阵...", verbose)

    def _compute_local_deviations(ui, mi, target_user_neighbors):
        """
        计算用户 u 在邻域 U_nb 内对物品 i 与所有其他物品的局部偏差
        dev_{ij}^{local} = mean_{u' in U_nb ∩ U_ij} (r_{u'i} - r_{u'j})
        要求 |U_nb ∩ U_ij| >= min_common
        """
        local_devs = {}
        user_u_ratings = user_ratings_dict.get(ui, {})
        if not user_u_ratings:
            return local_devs

        # 检查用户是否评分过目标物品 mi
        if mi not in user_u_ratings:
            return local_devs

        # 对所有邻居用户迭代，统计与 mi 的偏差
        for nui in target_user_neighbors:
            if nui not in user_ratings_dict:
                continue
            neighbor_ratings = user_ratings_dict[nui]
            if mi not in neighbor_ratings:
                continue
            r_n_mi = neighbor_ratings[mi]
            for other_mi, r_n_other in neighbor_ratings.items():
                if other_mi == mi:
                    continue
                if other_mi not in user_u_ratings:
                    continue
                diff = r_n_mi - r_n_other  # dev_{mi, other_mi}
                key = (mi, other_mi)
                if key not in local_devs:
                    local_devs[key] = [0.0, 0, []]
                local_devs[key][0] += diff
                local_devs[key][1] += 1

        # 存储邻居集（用于后续预测）
        return local_devs

    # 由于局部偏差是逐用户-物品对计算的，计算量太大
    # 采用简化策略：计算所有用户在邻域内的全局局部偏差表
    # 方法：对于每个用户 u，基于其邻居 U_nb，为每对物品 (i,j) 计算局部偏差
    # 然后 u 的预测使用这些局部偏差

    # 按用户分块并行计算局部偏差表
    all_user_indices = list(user_sim.keys())
    chunk_size_local = max(1, len(all_user_indices) // (_N_CPUS * 2))
    user_chunks = [all_user_indices[i:i + chunk_size_local]
                   for i in range(0, len(all_user_indices), chunk_size_local)]
    print(f"  {len(user_chunks)} 用户批次计算局部偏差")
    verbose_step("改进SlopeOne", f"{len(user_chunks)} 用户批次并行计算", verbose)

    def _process_user_deviations(users_chunk):
        """为一批用户计算其邻域内的局部偏差表"""
        results = {}
        for ui in users_chunk:
            neighbors = neighbor_lists.get(ui, [])
            if not neighbors:
                continue

            # 收集邻域内所有用户的评分
            neighbor_all_ratings = {}
            for nui in neighbors:
                if nui in user_ratings_dict:
                    neighbor_all_ratings[nui] = user_ratings_dict[nui]

            if len(neighbor_all_ratings) < 2:
                continue

            # 统计邻域内每对物品 (i, j) 的偏差
            local_devs_sum = defaultdict(float)
            local_devs_cnt = defaultdict(int)

            for nui, nratings in neighbor_all_ratings.items():
                items_list = list(nratings.keys())
                for a in range(len(items_list)):
                    mi_a = items_list[a]
                    r_a = nratings[mi_a]
                    for b in range(len(items_list)):
                        if a == b:
                            continue
                        mi_b = items_list[b]
                        r_b = nratings[mi_b]
                        key = (mi_a, mi_b)  # dev_{a,b}
                        local_devs_sum[key] += r_a - r_b
                        local_devs_cnt[key] += 1

            # 过滤：仅保留 |U_nb ∩ U_ij| >= min_common
            local_devs = {}
            for (i, j), s_diff in local_devs_sum.items():
                cnt = local_devs_cnt[(i, j)]
                if cnt >= min_common:
                    key_str = f"{i},{j}"
                    local_devs[key_str] = s_diff / cnt

            if local_devs:
                results[ui] = local_devs

        return results

    start_local = time.time()
    from joblib import Parallel, delayed
    chunk_dev_results = Parallel(n_jobs=_N_CPUS, prefer='processes', verbose=0)(
        delayed(_process_user_deviations)(chunk) for chunk in user_chunks
    )

    local_deviations = {}
    for cr in chunk_dev_results:
        local_deviations.update(cr)

    print(f"  局部偏差计算完成: {len(local_deviations)} 用户有局部偏差")
    print(f"  耗时: {time.time() - start_local:.1f}s")
    verbose_step("改进SlopeOne", f"局部偏差计算完成: {len(local_deviations)} 用户有局部偏差", verbose)

    # RMSE 评估
    train_user_ids = train_df['user_id'].values.astype(np.int32)
    train_movie_ids = train_df['movie_id'].values.astype(np.int32)
    train_ratings = r_val

    print(f"\n  [RMSE 计算] {_N_CPUS} 进程并行...")
    verbose_step("RMSE计算", f"开始并行计算训练集 RMSE, {_N_CPUS} 进程", verbose)

    def _predict_batch(batch_indices):
        """批量预测：r̂_uj = mean_{i in S(u)} (r_ui + dev_{ji}^{local})"""
        with threadpool_limits(limits=1, user_api='blas'):
            n_batch = len(batch_indices)
            preds = np.zeros(n_batch, dtype=np.float32)
            for k, i in enumerate(batch_indices):
                uid = train_user_ids[i]
                mid = train_movie_ids[i]
                ui = user2idx.get(uid)
                mi = movie2idx.get(mid)

                if ui is None or mi is None:
                    preds[k] = 3.0
                    continue

                user_ratings_u = user_ratings_dict.get(ui, {})
                if not user_ratings_u:
                    preds[k] = user_means[ui] if ui is not None else 3.0
                    continue

                local_devs_u = local_deviations.get(ui, {})
                s_pred = 0.0
                s_count = 0
                for rated_mi, rating in user_ratings_u.items():
                    if rated_mi == mi:
                        continue
                    # 查找 dev_{mi, rated_mi}^{local}
                    key = f"{rated_mi},{mi}"
                    dev = local_devs_u.get(key)
                    if dev is not None:
                        s_pred += rating + dev
                        s_count += 1

                if s_count > 0:
                    preds[k] = s_pred / s_count
                else:
                    preds[k] = user_means[ui]

            return preds

    total = len(train_df)
    chunk_size_rmse = max(500, total // (_N_CPUS * 4))
    batches = [list(range(i, min(i + chunk_size_rmse, total))) for i in range(0, total, chunk_size_rmse)]
    print(f"  {total} 条样本, {len(batches)} 批次")
    verbose_step("RMSE计算", f"共 {total} 条样本, {len(batches)} 批次", verbose)

    results = Parallel(n_jobs=_N_CPUS, prefer='processes', verbose=0)(
        delayed(_predict_batch)(batch) for batch in batches
    )

    pred_values = np.concatenate(results).astype(np.float32)
    rmse = float(np.sqrt(np.mean((pred_values - train_ratings) ** 2)))
    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  改进 Slope One 训练耗时: {elapsed:.2f} 秒")
    verbose_step("RMSE计算", f"RMSE={rmse:.4f}", verbose)
    verbose_step("改进SlopeOne", f"训练完成, 总耗时: {elapsed:.2f} 秒", verbose)

    # 整理输出：local_deviations 转换为可序列化格式
    verbose_step("模型构建", "序列化局部偏差和邻居信息...", verbose)
    local_devs_serializable = {}
    for ui, devs in local_deviations.items():
        uid = int(all_users[ui])
        local_devs_serializable[uid] = {
            k: float(v) for k, v in devs.items()
        }

    user_neighbors_serializable = {}
    for ui, neighbors in user_sim.items():
        uid = int(all_users[ui])
        user_neighbors_serializable[uid] = [
            (int(all_users[nui]), float(nsim))
            for nui, nsim in neighbors.items()
        ]

    verbose_step("模型构建", f"序列化完成: {len(local_devs_serializable)} 用户局部偏差, {len(user_neighbors_serializable)} 用户邻居", verbose)
    return {
        'algorithm': 'slope_one_improved',
        'n_neighbors': n_neighbors,
        'min_common': min_common,
        'user_local_deviations': local_devs_serializable,
        'user_neighbors': user_neighbors_serializable,
        'user_means': {int(uid): float(user_means[i]) for i, uid in enumerate(all_users)},
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
        'description': '改进Slope One: 基于邻域筛选的局部偏差 + min_common阈值',
    }


def save_model(model, name='slope_one_improved_model', verbose=False):
    path = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {path}")
    verbose_step("模型保存", f"开始保存模型至 {path}...", verbose)
    with open(path, 'wb') as f:
        pickle.dump(model, f)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  模型大小: {size_mb:.2f} MB")
    verbose_step("模型保存", f"模型文件大小: {size_mb:.2f} MB", verbose)

    meta = {k: v for k, v in model.items()
            if isinstance(v, (str, int, float, bool, list))}
    meta_path = os.path.join(MODEL_DIR, f'{name}_meta.json')
    verbose_step("模型保存", "保存元信息 JSON...", verbose)
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  元数据: {meta_path}")
    verbose_step("模型保存", f"元数据已保存至 {meta_path}", verbose)
    return path


def main():
    parser = argparse.ArgumentParser(description='改进 Slope One 模型训练（多线程）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数 M (default: 30)')
    parser.add_argument('--min-common', type=int, default=3,
                       help='最小共同评分用户数阈值 (default: 3)')
    parser.add_argument('--chunk-size', type=int, default=2000, help='分块大小 (default: 2000)')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行进程数')
    parser.add_argument('--verbose', action='store_true', help='输出详细步骤日志到 logs/verbose/')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

    verbose_init('train_slopeone_improved', args.verbose)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用进程数: {_N_CPUS}")
    print(f"[算法] 改进 Slope One (2.2.6): 邻域筛选 + 局部偏差 + 最小共同用户阈值")

    overall_start = time.time()
    verbose_step("数据加载", "从数据库加载评分数据...", args.verbose)
    ratings_df = load_data(verbose=args.verbose)
    verbose_step("数据加载完成", f"加载 {len(ratings_df)} 条评分记录", args.verbose)

    verbose_step("开始训练", f"邻居数={args.n_neighbors}, min_common={args.min_common}", args.verbose)
    model = train_slopeone_improved(
        ratings_df,
        n_neighbors=args.n_neighbors,
        min_common=args.min_common,
        chunk_size=args.chunk_size,
        verbose=args.verbose,
    )
    verbose_step("训练完成", "邻域局部偏差矩阵构建完成", args.verbose)

    verbose_step("保存模型", "持久化模型文件...", args.verbose)
    save_model(model, 'slope_one_improved_model', verbose=args.verbose)
    verbose_step("模型保存完成", "模型已保存至 models/", args.verbose)

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  改进 Slope One 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")
    verbose_step("全部完成", f"总耗时: {total:.2f} 秒", args.verbose)
    verbose_close()


if __name__ == '__main__':
    with log_output('train_slopeone_improved'):
        main()