#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_slopeone_traditional.py - 传统 Slope One 算法
对应 2.2.5 节

核心：计算物品间平均评分偏差
dev_ij = (1/|U_ij|) Σ (r_ui - r_uj)
预测：r̂_uj = (1/|S(u)|) Σ (r_ui + dev_ji)

多线程实现（偏差矩阵使用进程池并行计算）
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
from scipy.sparse import csr_matrix
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


def compute_item_deviations(train_df, movie2idx, n_movies, verbose=False):
    """
    计算传统 Slope One 偏差矩阵
    dev_ij = (1/|U_ij|) Σ (r_ui - r_uj)

    多线程并行计算（按物品对分块）
    """
    print(f"\n  [偏差矩阵计算] {_N_CPUS} 进程并行...")
    verbose_step("偏差矩阵", f"开始计算偏差矩阵, {_N_CPUS} 进程并行...", verbose)

    n_movies_int = n_movies

    # 按用户分组评分
    verbose_step("偏差矩阵", "按用户分组评分数据...", verbose)
    user_ratings = defaultdict(list)
    for uid_val, mid_val, rating in zip(
        train_df['user_id'], train_df['movie_id'], train_df['rating']
    ):
        mi = movie2idx.get(mid_val)
        if mi is not None:
            user_ratings[int(uid_val)].append((mi, float(rating)))

    users_with_ratings = list(user_ratings.keys())
    print(f"  用户数: {len(users_with_ratings)}")
    verbose_step("偏差矩阵", f"评分用户数: {len(users_with_ratings)}", verbose)

    # 预计算共现计数和偏差和（使用稀疏矩阵乘法高效计算）
    # 方法：逐用户构建该用户的评分向量片段，累加贡献
    # 使用分块并行策略

    # 将所有用户评分展开为并行友好的格式
    user_item_pairs = []
    for uid, items in user_ratings.items():
        for mi, rating in items:
            user_item_pairs.append((uid, mi, rating))

    # 按用户分批处理
    unique_users = list(user_ratings.keys())
    chunk_size = max(1, len(unique_users) // (_N_CPUS * 2))
    user_chunks = [unique_users[i:i + chunk_size]
                   for i in range(0, len(unique_users), chunk_size)]
    print(f"  {len(user_chunks)} 用户批次")
    verbose_step("偏差矩阵", f"共 {len(user_chunks)} 个用户批次并行计算", verbose)

    def _process_user_chunk(users_chunk):
        """处理一批用户，累加偏差贡献"""
        # 使用字典存储 (i,j) -> (sum_diff, count)
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
                    diff = r_a - r_b  # dev_ab ≈ r_a - r_b
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

    # 构建偏差矩阵
    verbose_step("偏差矩阵", "构建最终偏差矩阵与频次矩阵...", verbose)
    dev_matrix = np.zeros((n_movies_int, n_movies_int), dtype=np.float32)
    freq_matrix = np.zeros((n_movies_int, n_movies_int), dtype=np.int32)
    for (i, j), (s_diff, cnt) in total_deviations.items():
        if cnt > 0:
            dev_matrix[i, j] = s_diff / cnt
            freq_matrix[i, j] = cnt

    print(f"  偏差矩阵计算完成: {len(total_deviations)} 有效物品对")
    verbose_step("偏差矩阵", f"完成: {len(total_deviations)} 有效物品对", verbose)
    return dev_matrix, freq_matrix


def train_slopeone_traditional(train_df, verbose=False):
    """
    传统 Slope One 训练
    - 偏差：dev_ij = (1/|U_ij|) Σ (r_ui - r_uj)
    - 预测：r̂_uj = (1/|S(u)|) Σ (r_ui + dev_ji)
    多线程实现
    """
    print("\n" + "=" * 60)
    print(f"[传统 Slope One 训练] 进程数: {_N_CPUS}")

    start_time = time.time()

    verbose_step("SlopeOne训练", "构建映射和稀疏矩阵...", verbose)
    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df, verbose=verbose)

    sparse_R = csr_matrix(
        (r_val, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )
    non_zero = sparse_R.nnz
    density = non_zero / (n_users * n_movies) * 100
    print(f"  稀疏矩阵: ({n_users}, {n_movies}), 非零: {non_zero:,} ({density:.4f}%)")
    verbose_step("SlopeOne训练", f"稀疏矩阵: ({n_users}, {n_movies}), 非零: {non_zero:,} ({density:.4f}%)", verbose)

    # 计算偏差矩阵
    verbose_step("SlopeOne训练", "开始计算物品偏差矩阵...", verbose)
    dev_matrix, freq_matrix = compute_item_deviations(train_df, movie2idx, n_movies, verbose=verbose)

    def _predict(ui, mi, user_ratings):
        """对单个用户-物品对进行 Slope One 预测"""
        # 获取用户评分的物品及其评分
        user_items = np.where(user_ratings > 0)[0]
        if len(user_items) == 0:
            return 3.0

        s_pred = 0.0
        s_count = 0
        for other_mi in user_items:
            if other_mi == mi:
                continue
            # 使用 dev_{mi, other_mi} = -dev_{other_mi, mi}
            dev = dev_matrix[other_mi, mi]
            if freq_matrix[other_mi, mi] > 0:
                s_pred += user_ratings[other_mi] + dev
                s_count += 1

        if s_count > 0:
            return s_pred / s_count
        else:
            # 回退到用户均值
            return float(np.mean(user_ratings[user_items]))

    train_user_ids = train_df['user_id'].values.astype(np.int32)
    train_movie_ids = train_df['movie_id'].values.astype(np.int32)
    train_ratings = r_val

    print(f"\n  [RMSE 计算] {_N_CPUS} 进程并行...")
    verbose_step("RMSE计算", f"开始并行计算训练集 RMSE, {_N_CPUS} 进程", verbose)

    def _predict_batch(batch_indices):
        """批量预测"""
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

                user_row = sparse_R[ui].toarray().ravel()
                preds[k] = _predict(ui, mi, user_row)

            return preds

    total = len(train_df)
    chunk_size_rmse = max(500, total // (_N_CPUS * 4))
    batches = [list(range(i, min(i + chunk_size_rmse, total))) for i in range(0, total, chunk_size_rmse)]
    print(f"  {total} 条样本, {len(batches)} 批次")
    verbose_step("RMSE计算", f"共 {total} 条样本, {len(batches)} 批次", verbose)

    from joblib import Parallel, delayed
    results = Parallel(n_jobs=_N_CPUS, prefer='processes', verbose=0)(
        delayed(_predict_batch)(batch) for batch in batches
    )

    pred_values = np.concatenate(results).astype(np.float32)
    rmse = float(np.sqrt(np.mean((pred_values - train_ratings) ** 2)))
    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  传统 Slope One 训练耗时: {elapsed:.2f} 秒")
    verbose_step("RMSE计算", f"RMSE={rmse:.4f}", verbose)
    verbose_step("SlopeOne训练", f"训练完成, 总耗时: {elapsed:.2f} 秒", verbose)

    dev_dict = {}
    n_mi = len(all_movies)
    verbose_step("模型构建", "序列化偏差字典...", verbose)
    for i in range(n_mi):
        for j in range(n_mi):
            if freq_matrix[i, j] > 0:
                mid_i = int(all_movies[i])
                mid_j = int(all_movies[j])
                if mid_i not in dev_dict:
                    dev_dict[mid_i] = {}
                dev_dict[mid_i][mid_j] = float(dev_matrix[i, j])

    verbose_step("模型构建", f"偏差字典序列化完成, 含 {len(dev_dict)} 个电影条目", verbose)
    return {
        'algorithm': 'slope_one_traditional',
        'item_deviations': dev_dict,
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
        'description': '传统Slope One: dev_ij=mean(r_ui-r_uj), r̂_uj=mean(r_ui+dev_ji)',
    }


def save_model(model, name='slope_one_traditional_model', verbose=False):
    path = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {path}")
    verbose_step("模型保存", f"开始保存模型至 {path}...", verbose)
    with open(path, 'wb') as f:
        pickle.dump(model, f)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  模型大小: {size_mb:.2f} MB")
    verbose_step("模型保存", f"模型文件大小: {size_mb:.2f} MB", verbose)

    verbose_step("模型保存", "保存元信息 JSON...", verbose)
    meta = {k: v for k, v in model.items()
            if isinstance(v, (str, int, float, bool, list))}
    meta_path = os.path.join(MODEL_DIR, f'{name}_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  元数据: {meta_path}")
    verbose_step("模型保存", f"元数据已保存至 {meta_path}", verbose)
    return path


def main():
    parser = argparse.ArgumentParser(description='传统 Slope One 模型训练（多线程）')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行进程数')
    parser.add_argument('--verbose', action='store_true', help='输出详细步骤日志到 logs/verbose/')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

    verbose_init('train_slopeone_traditional', args.verbose)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用进程数: {_N_CPUS}")
    print(f"[算法] 传统 Slope One (2.2.5): 全局偏差 + 加权平均预测")

    overall_start = time.time()
    verbose_step("数据加载", "从数据库加载评分数据...", args.verbose)
    ratings_df = load_data(verbose=args.verbose)
    verbose_step("数据加载完成", f"加载 {len(ratings_df)} 条评分记录", args.verbose)

    verbose_step("开始训练", "执行传统 Slope One 偏差计算与预测", args.verbose)
    model = train_slopeone_traditional(ratings_df, verbose=args.verbose)
    verbose_step("训练完成", "全局偏差矩阵构建完成", args.verbose)

    verbose_step("保存模型", "持久化模型文件...", args.verbose)
    save_model(model, 'slope_one_traditional_model', verbose=args.verbose)
    verbose_step("模型保存完成", "模型已保存至 models/", args.verbose)

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  传统 Slope One 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")
    verbose_step("全部完成", f"总耗时: {total:.2f} 秒", args.verbose)
    verbose_close()


if __name__ == '__main__':
    with log_output('train_slopeone_traditional'):
        main()