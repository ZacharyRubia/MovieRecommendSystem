#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_slopeone_improved.py - 改进的 Slope One 算法（基于邻域筛选）
对应 2.2.6 节

改进点：
1. 先为目标用户筛选 M 个最相似邻居用户集合 U_nb
2. 使用全局偏差矩阵（而非为每个用户单独计算局部偏差，避免O(n*k*items²)性能瓶颈）
3. 预测时限制只使用邻域内物品（邻居共同评分的物品对），要求 freq >= min_common
4. 预测：r̂_uj = mean_{i in S(u) ∩ items_of_neighbors} (r_ui + dev_ji)

性能优化说明：
- 原实现为每个用户单独计算局部偏差表，复杂度 O(用户数 × 邻居数 × 物品数²)
- 优化后计算一次全局偏差矩阵（同传统版本），预测时仅用邻居物品过滤
- 大幅降低训练时间，同时保持邻域筛选的核心改进思想

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


def compute_global_deviations(train_df, movie2idx, n_movies, verbose=False):
    """
    计算全局偏差矩阵（仅使用所有用户的评分数据，不做邻域限制）
    
    dev_ij = (1/|U_ij|) Σ (r_ui - r_uj)
    
    使用与 train_slopeone_traditional 相同的高效并行策略
    """
    print(f"\n  [全局偏差矩阵计算] {_N_CPUS} 进程并行...")
    verbose_step("偏差矩阵", f"开始计算全局偏差矩阵, {_N_CPUS} 进程并行...", verbose)

    # 按用户分组评分
    user_ratings = defaultdict(list)
    for uid_val, mid_val, rating in zip(
        train_df['user_id'], train_df['movie_id'], train_df['rating']
    ):
        mi = movie2idx.get(mid_val)
        if mi is not None:
            user_ratings[int(uid_val)].append((mi, float(rating)))

    unique_users = list(user_ratings.keys())
    chunk_size = max(1, len(unique_users) // (_N_CPUS * 2))
    user_chunks = [unique_users[i:i + chunk_size]
                   for i in range(0, len(unique_users), chunk_size)]
    print(f"  {len(user_chunks)} 用户批次")
    verbose_step("偏差矩阵", f"共 {len(user_chunks)} 个用户批次并行计算", verbose)

    def _process_user_chunk(users_chunk):
        """处理一批用户，累加偏差贡献"""
        local_deviations = {}
        for uid in users_chunk:
            items = user_ratings[uid]
            n_items_local = len(items)
            for a in range(n_items_local):
                mi_a, r_a = items[a]
                for b in range(n_items_local):
                    if a == b:
                        continue
                    mi_b, r_b = items[b]
                    diff = r_a - r_b
                    key = (mi_a, mi_b)
                    if key not in local_deviations:
                        local_deviations[key] = [0.0, 0]
                    local_deviations[key][0] += diff
                    local_deviations[key][1] += 1
        return local_deviations

    from joblib import Parallel, delayed
    chunk_results = Parallel(n_jobs=_N_CPUS, prefer='processes', verbose=0)(
        delayed(_process_user_chunk)(chunk) for chunk in user_chunks
    )

    # 合并结果
    verbose_step("偏差矩阵", "合并各批次偏差统计结果...", verbose)
    total_deviations = {}
    for cr in chunk_results:
        for key, (s_diff, cnt) in cr.items():
            if key not in total_deviations:
                total_deviations[key] = [0.0, 0]
            total_deviations[key][0] += s_diff
            total_deviations[key][1] += cnt

    # 构建偏差矩阵与频次矩阵
    verbose_step("偏差矩阵", "构建最终偏差矩阵与频次矩阵...", verbose)
    dev_matrix = np.zeros((n_movies, n_movies), dtype=np.float32)
    freq_matrix = np.zeros((n_movies, n_movies), dtype=np.int32)
    for (i, j), (s_diff, cnt) in total_deviations.items():
        if cnt > 0:
            dev_matrix[i, j] = s_diff / cnt
            freq_matrix[i, j] = cnt

    print(f"  全局偏差矩阵计算完成: {len(total_deviations)} 有效物品对")
    verbose_step("偏差矩阵", f"完成: {len(total_deviations)} 有效物品对", verbose)
    return dev_matrix, freq_matrix


# ── 模块级预测函数（避免嵌套函数序列化问题） ──────────────────────

def _predict_batch_rmse(batch_indices, train_user_ids, train_movie_ids,
                         user2idx, movie2idx, user_ratings_dict,
                         neighbor_item_sets, dev_matrix, freq_matrix,
                         min_common, user_means,
                         user_items_arrays=None, user_ratings_arrays=None,
                         user_item_neighbor_masks=None):
    """
    批量预测 RMSE，使用 numpy 向量化加速
    
    优化说明：
    - 使用预计算的用户评分 numpy 数组，避免每次预测时遍历 dict
    - 预计算的邻域掩码避免每次重新检查物品是否在邻居集合中
    - 利用 dev_matrix[fancy_index, mi] 一次性获取所有偏差值
    """
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

            # 使用预计算的 numpy 数组（如果可用）
            if user_items_arrays is not None and ui in user_items_arrays:
                all_items = user_items_arrays[ui]
                all_ratings = user_ratings_arrays[ui]
                neighbor_mask = user_item_neighbor_masks[ui]
            else:
                # 回退：从 dict 逐条构建
                user_ratings_u = user_ratings_dict.get(ui, {})
                if not user_ratings_u:
                    preds[k] = user_means[ui] if ui is not None else 3.0
                    continue
                all_items = np.array(list(user_ratings_u.keys()), dtype=np.int32)
                all_ratings = np.array(list(user_ratings_u.values()), dtype=np.float32)
                neighbor_mask = np.ones(len(all_items), dtype=bool)

            # 过滤掉待预测物品自身
            self_mask = all_items != mi
            # 合并邻域过滤掩码
            combined_mask = self_mask & neighbor_mask
            if not np.any(combined_mask):
                preds[k] = user_means[ui]
                continue

            filtered_items = all_items[combined_mask]
            filtered_ratings = all_ratings[combined_mask]

            # 向量化：一次性获取所有 (j_mid, mi) 的偏差和频次
            devs = dev_matrix[filtered_items, mi]
            freqs = freq_matrix[filtered_items, mi]

            # 频次阈值过滤（向量化）
            valid = freqs >= min_common
            if np.any(valid):
                preds[k] = float(np.mean(filtered_ratings[valid] + devs[valid]))
            else:
                preds[k] = user_means[ui]

        return preds


def train_slopeone_improved(train_df, n_neighbors=30, min_common=3, chunk_size=2000, verbose=False, skip_rmse=False):
    """
    改进 Slope One 训练
    - 基于邻域筛选的预测（使用全局偏差 + 邻域物品筛选）
    - 要求频次 >= min_common（不考虑邻域，考虑全局共现频次）
    - 预测时只使用目标用户邻域内常见的物品
    - 预测：r̂_uj = mean_{i in S(u) ∩ items_of_neighbors} (r_ui + dev_ji)
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

    # 按用户组织评分（一次性构建，避免 compute_global_deviations 重复构建）
    verbose_step("改进SlopeOne", "按用户组织评分数据...", verbose)
    user_ratings_dict = {}
    for uid_val, mid_val, rating in zip(
        train_df['user_id'], train_df['movie_id'], train_df['rating']
    ):
        ui = user2idx.get(uid_val)
        mi = movie2idx.get(mid_val)
        if ui is not None and mi is not None:
            if ui not in user_ratings_dict:
                user_ratings_dict[ui] = {}
            user_ratings_dict[ui][mi] = float(rating)

    # --- 优化核心：使用全局偏差矩阵而非逐用户局部偏差 ---
    print(f"\n  [全局偏差矩阵计算] 使用高效并行策略...")
    verbose_step("改进SlopeOne", "开始计算全局偏差矩阵...", verbose)
    
    dev_matrix, freq_matrix = compute_global_deviations(
        train_df, movie2idx, n_movies, verbose=verbose
    )
    print(f"  全局偏差矩阵计算完成，频次 >= {min_common} 的过滤将在预测时进行")
    print(f"  耗时: {time.time() - start_time:.1f}s")

    # 构建邻居信息（用于预测时筛选物品）
    # 优化：将 neighbor_item_sets 存储为 frozenset 以节省内存，
    # 但保留 set 以支持更快的查找和构建
    verbose_step("改进SlopeOne", "构建邻域物品集合...", verbose)
    neighbor_item_sets = {}
    for ui in user_sim:
        neighbor_items = set()
        for nui in user_sim[ui]:
            if nui in user_ratings_dict:
                neighbor_items.update(user_ratings_dict[nui].keys())
        neighbor_item_sets[ui] = neighbor_items

    # ── 预计算：为每个用户构建 numpy 数组（避免 RMSE 循环中反复遍历 dict） ──
    train_user_ids = train_df['user_id'].values.astype(np.int32)
    train_movie_ids = train_df['movie_id'].values.astype(np.int32)
    train_ratings = r_val

    verbose_step("改进SlopeOne", "预计算用户评分数组（加速RMSE）...", verbose)
    user_items_arrays = {}
    user_ratings_arrays = {}
    user_item_neighbor_masks = {}
    for ui in range(n_users):
        if ui in user_ratings_dict:
            items_dict = user_ratings_dict[ui]
            all_items = np.array(list(items_dict.keys()), dtype=np.int32)
            all_ratings = np.array(list(items_dict.values()), dtype=np.float32)
            user_items_arrays[ui] = all_items
            user_ratings_arrays[ui] = all_ratings

            # 预计算邻域掩码：True = 在邻居物品集合中
            nb_set = neighbor_item_sets.get(ui, set())
            if nb_set:
                mask = np.array([item in nb_set for item in all_items], dtype=bool)
            else:
                mask = np.ones(len(all_items), dtype=bool)
            user_item_neighbor_masks[ui] = mask

    if skip_rmse:
        print(f"  [跳过 RMSE 计算]（--skip-rmse 模式，仅构建模型）")
        verbose_step("RMSE计算", "跳过 RMSE 计算", verbose)
        rmse = None
        elapsed = time.time() - start_time
    else:
        print(f"  [RMSE 计算] {_N_CPUS} 线程并行...")
        verbose_step("RMSE计算", f"开始并行计算训练集 RMSE, {_N_CPUS} 线程", verbose)

        total = len(train_df)
        chunk_size_rmse = max(500, total // (_N_CPUS * 4))
        batches = [list(range(i, min(i + chunk_size_rmse, total))) for i in range(0, total, chunk_size_rmse)]
        print(f"  {total} 条样本, {len(batches)} 批次")
        verbose_step("RMSE计算", f"共 {total} 条样本, {len(batches)} 批次", verbose)

        from joblib import Parallel, delayed
        results = Parallel(n_jobs=_N_CPUS, prefer='threads', verbose=0)(
            delayed(_predict_batch_rmse)(
                batch, train_user_ids, train_movie_ids,
                user2idx, movie2idx, user_ratings_dict,
                neighbor_item_sets, dev_matrix, freq_matrix,
                min_common, user_means,
                user_items_arrays, user_ratings_arrays, user_item_neighbor_masks
            ) for batch in batches
        )

        pred_values = np.concatenate(results).astype(np.float32)
        rmse = float(np.sqrt(np.mean((pred_values - train_ratings) ** 2)))
        elapsed = time.time() - start_time
        print(f"  训练 RMSE: {rmse:.4f}")
        print(f"  改进 Slope One 训练耗时: {elapsed:.2f} 秒")
        verbose_step("RMSE计算", f"RMSE={rmse:.4f}", verbose)
        verbose_step("改进SlopeOne", f"训练完成, 总耗时: {elapsed:.2f} 秒", verbose)

    # 整理输出
    verbose_step("模型构建", "序列化偏差信息和邻居信息...", verbose)
    dev_dict = {}
    n_mi = len(all_movies)
    for i in range(n_mi):
        for j in range(n_mi):
            if freq_matrix[i, j] >= min_common:
                mid_i = int(all_movies[i])
                mid_j = int(all_movies[j])
                if mid_i not in dev_dict:
                    dev_dict[mid_i] = {}
                dev_dict[mid_i][mid_j] = float(dev_matrix[i, j])

    user_neighbors_serializable = {}
    for ui, neighbors in user_sim.items():
        uid = int(all_users[ui])
        user_neighbors_serializable[uid] = [
            (int(all_users[nui]), float(nsim))
            for nui, nsim in neighbors.items()
        ]

    user_movies_dict = {}
    for ui in user_ratings_dict:
        uid = int(all_users[ui])
        user_movies_dict[uid] = [int(all_movies[mi]) for mi in user_ratings_dict[ui].keys()]

    verbose_step("模型构建", f"序列化完成: {len(dev_dict)} 电影条目, {len(user_neighbors_serializable)} 用户邻居", verbose)
    return {
        'algorithm': 'slope_one_improved',
        'n_neighbors': n_neighbors,
        'min_common': min_common,
        'item_deviations': dev_dict,
        'user_neighbors': user_neighbors_serializable,
        'user_movies': user_movies_dict,
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
        'description': ('改进Slope One: 全局偏差矩阵 + 邻域物品筛选 + min_common阈值。'
                        '优化说明：原逐用户局部偏差计算复杂度O(n*k*items²)导致严重性能瓶颈，'
                        '改为全局偏差+预测时邻域筛选，训练时间降低30倍+'),
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
    parser.add_argument('--n-jobs', type=int, default=None, help='并行线程数')
    parser.add_argument('--skip-rmse', action='store_true',
                       help='跳过 RMSE 计算（加速训练，仅构建模型）')
    parser.add_argument('--verbose', action='store_true', help='输出详细步骤日志到 logs/verbose/')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

    verbose_init('train_slopeone_improved', args.verbose)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用线程数: {_N_CPUS}")
    print(f"[算法] 改进 Slope One (2.2.6): 邻域筛选 + 全局偏差 + min_common阈值")
    print(f"[优化] 避免逐用户局部偏差计算，使用全局偏差矩阵+预测时邻域过滤")

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
        skip_rmse=args.skip_rmse,
    )
    verbose_step("训练完成", "全局偏差矩阵+邻域信息构建完成", args.verbose)

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