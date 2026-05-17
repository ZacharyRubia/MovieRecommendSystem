#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_usercf_traditional.py - 传统基于用户的协同过滤（传统 User-CF）
对应 2.2.1 节

用户相似度：w_uv = |N(u) ∩ N(v)| / sqrt(|N(u)|·|N(v)|)
评分预测：P(u,i) = Σ w_uv · r_vi (邻居加权求和)

多线程实现（RMSE 评估使用进程池并行）
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
    verbose_step("数据加载 - 开始", f"读取文件: {os.path.join(DATA_DIR, 'test_ratings.csv')}", verbose)
    ratings_df = pd.read_csv(
        os.path.join(DATA_DIR, 'test_ratings.csv'),
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )
    n_total = len(ratings_df)
    n_users = ratings_df['user_id'].nunique()
    n_movies = ratings_df['movie_id'].nunique()
    print(f"  评分数据: {n_total} 条, 用户 {n_users} 个, 电影 {n_movies} 部")
    verbose_step("数据加载 - 详情",
        f"总评分记录数: {n_total}\n"
        f"用户数: {n_users}\n"
        f"电影数: {n_movies}\n"
        f"数据路径: {os.path.join(DATA_DIR, 'test_ratings.csv')}\n"
        f"数据密度: {n_total / (n_users * n_movies) * 100:.4f}%",
        verbose)
    verbose_step("数据加载 - 完成", f"成功加载 {n_total} 条评分记录", verbose)
    return ratings_df


def build_mappings(train_df, verbose=False):
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
    verbose_step("映射构建 - 完成",
        f"用户数={n_users}, 电影数={n_movies}\n"
        f"评分向量长度={len(r_val)}",
        verbose)
    return (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
            n_users, n_movies, u_idx, m_idx, r_val)


def compute_user_similarity_traditional(sparse_R, top_k=30, chunk_size=2000, verbose=False):
    """
    传统用户相似度：w_uv = |N(u) ∩ N(v)| / sqrt(|N(u)|·|N(v)|)
    基于二元交互矩阵，分块 + 多线程并行计算
    """
    from sklearn.metrics.pairwise import cosine_similarity

    n_users = sparse_R.shape[0]

    verbose_step("用户相似度 - 二值化", f"将评分矩阵转换为二元交互矩阵（0/1），shape=({n_users}, {sparse_R.shape[1]})", verbose)

    binary_R = sparse_R.copy()
    binary_R.data = np.ones_like(binary_R.data, dtype=np.float32)

    # 计算 L2 范数并归一化
    verbose_step("用户相似度 - 归一化", "计算每个用户的 L2 范数并进行归一化", verbose)
    norms = np.sqrt(binary_R.multiply(binary_R).sum(axis=1)).A.ravel()
    nz = np.sum(norms == 0)
    if nz > 0:
        verbose_step("用户相似度 - 归一化 (续)", f"{nz} 个用户无评分（范数为 0），已设为 1.0 避免除零", verbose)
    norms[norms == 0] = 1.0
    inv_norms = dia_matrix((1.0 / norms, [0]), shape=(n_users, n_users))
    normalized = inv_norms @ binary_R

    verbose_step("用户相似度 - 归一化完成",
        f"用户范数: min={np.min(norms):.4f}, max={np.max(norms):.4f}, mean={np.mean(norms):.4f}\n"
        f"二值化+归一化矩阵稀疏度: {normalized.nnz} / {normalized.shape[0] * normalized.shape[1]}",
        verbose)

    chunk_ranges = [(i, min(i + chunk_size, n_users)) for i in range(0, n_users, chunk_size)]
    n_chunks = len(chunk_ranges)
    verbose_step("用户相似度 - 分块计算",
        f"分块大小={chunk_size}, 总块数={n_chunks}, 并行进程数={_N_CPUS}\n"
        f"相似度公式: cosine_similarity(归一化向量) → 映射到 [0, 1]",
        verbose)

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
    chunk_start = time.time()
    chunk_results = Parallel(n_jobs=_N_CPUS, prefer='threads', verbose=0)(
        delayed(_process_chunk)(c_start, c_end) for c_start, c_end in chunk_ranges
    )

    user_sim = {}
    for cr in chunk_results:
        user_sim.update(cr)

    chunk_elapsed = time.time() - chunk_start
    verbose_step("用户相似度 - 相似度统计",
        f"有邻居的用户数: {len(user_sim)} / {n_users} = {len(user_sim)/n_users*100:.1f}%\n"
        f"总相似度计算耗时: {chunk_elapsed:.2f}s",
        verbose)

    # 统计邻居分布
    if user_sim:
        neighbor_counts = [len(nb) for nb in user_sim.values()]
        verbose_step("用户相似度 - 邻居分布",
            f"邻居数: min={min(neighbor_counts)}, max={max(neighbor_counts)}, "
            f"mean={np.mean(neighbor_counts):.1f}, median={np.median(neighbor_counts):.0f}",
            verbose)

    verbose_step("用户相似度 - 完成", f"传统 User-CF 相似度计算完毕，Top-{top_k} 邻居", verbose)
    return user_sim


def train_usercf_traditional(train_df, n_neighbors=30, chunk_size=2000, verbose=False):
    """
    传统 User-CF 训练
    - 相似度：余弦相似度（基于二元交互矩阵）
    - 预测：P(u,i) = Σ w_uv · r_vi
    """
    print("\n" + "=" * 60)
    print(f"[传统 User-CF 训练] 邻居数: {n_neighbors} | 进程数: {_N_CPUS}")
    verbose_step("传统 User-CF 训练 - 开始",
        f"邻居数 K={n_neighbors}, 分块大小={chunk_size}, 并行进程数={_N_CPUS}\n"
        f"相似度公式: w_uv = |N(u)∩N(v)| / sqrt(|N(u)|·|N(v)|)\n"
        f"预测公式: P(u,i) = Σ w_uv · r_vi",
        verbose)

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df, verbose)

    # 用户均值
    verbose_step("用户均值 - 开始", "计算每个用户的评分均值 μ_u", verbose)
    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts
    verbose_step("用户均值 - 统计",
        f"用户评分均值: min={np.min(user_means):.2f}, max={np.max(user_means):.2f}, "
        f"global_mean={np.mean(user_means):.2f}",
        verbose)

    # 稀疏矩阵
    sparse_R = csr_matrix(
        (r_val, (u_idx, m_idx)),
        shape=(n_users, n_movies),
        dtype=np.float32
    )
    non_zero = sparse_R.nnz
    density = non_zero / (n_users * n_movies) * 100
    print(f"  稀疏矩阵: ({n_users}, {n_movies}), 非零: {non_zero:,} ({density:.4f}%)")
    verbose_step("稀疏矩阵 - 统计",
        f"矩阵形状: ({n_users}, {n_movies})\n"
        f"非零元素: {non_zero:,}\n"
        f"密度: {density:.4f}%\n"
        f"每用户平均评分: {non_zero/n_users:.1f}\n"
        f"每电影平均评分: {non_zero/n_movies:.1f}",
        verbose)

    # 计算用户相似度
    verbose_step("相似度计算 - 公式说明",
        "传统 User-CF 相似度:\n"
        "  1) 将评分矩阵二值化: r_ui > 0 → 1, 否则 0\n"
        "  2) N(u) = 用户 u 交互过的物品集合\n"
        "  3) w_uv = |N(u) ∩ N(v)| / sqrt(|N(u)|·|N(v)|)\n"
        "     等价于对二值化向量计算余弦相似度",
        verbose)

    user_sim = compute_user_similarity_traditional(sparse_R, top_k=n_neighbors, chunk_size=chunk_size, verbose=verbose)

    # 构建邻居表
    verbose_step("邻居表构建 - 开始", "将相似度字典转换为稠密邻居数组", verbose)
    max_neighbors = max(len(nb) for nb in user_sim.values()) if user_sim else 0
    sim_nb_idx = np.zeros((n_users, max_neighbors), dtype=np.int32)
    sim_nb_val = np.zeros((n_users, max_neighbors), dtype=np.float32)
    sim_nb_cnt = np.zeros(n_users, dtype=np.int32)
    for ui, neighbors in user_sim.items():
        nb_list = list(neighbors.items())
        cnt = len(nb_list)
        sim_nb_cnt[ui] = cnt
        for j, (nui, sim) in enumerate(nb_list):
            sim_nb_idx[ui, j] = nui
            sim_nb_val[ui, j] = sim
    no_nb = np.sum(sim_nb_cnt == 0)
    verbose_step("邻居表构建 - 完成",
        f"邻居表维度: max_neighbors={max_neighbors}\n"
        f"无邻居用户: {no_nb} / {n_users} = {no_nb/n_users*100:.1f}%",
        verbose)

    train_user_ids = train_df['user_id'].values.astype(np.int32)
    train_movie_ids = train_df['movie_id'].values.astype(np.int32)
    train_ratings = r_val

    print(f"\n  [RMSE 计算] {_N_CPUS} 进程并行...")
    verbose_step("RMSE 计算 - 开始",
        f"总样本数: {len(train_df)}\n"
        f"并行进程数: {_N_CPUS}\n"
        f"预测公式: P(u,i) = Σ w_uv · r_vi\n"
        f"          = Σ_{{v∈S(u,K)∩N(i)}} sim_uv · r_vi",
        verbose)

    def _predict_batch(batch_indices):
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

                nb_cnt = sim_nb_cnt[ui]
                if nb_cnt == 0:
                    preds[k] = user_means[ui]
                    continue

                nui_arr = sim_nb_idx[ui, :nb_cnt]
                sim_arr = sim_nb_val[ui, :nb_cnt]

                neighbor_ratings = np.array([sparse_R[nui, mi] for nui in nui_arr], dtype=np.float32)
                mask = neighbor_ratings != 0
                if not np.any(mask):
                    preds[k] = user_means[ui]
                    continue

                numerator = float(np.sum(sim_arr[mask] * neighbor_ratings[mask]))
                denominator = float(np.sum(np.abs(sim_arr[mask])))
                preds[k] = numerator / denominator if denominator > 0 else user_means[ui]

            return preds

    total = len(train_df)
    chunk_size_rmse = max(1000, total // (_N_CPUS * 2))
    batches = [list(range(i, min(i + chunk_size_rmse, total))) for i in range(0, total, chunk_size_rmse)]
    print(f"  {total} 条样本, {len(batches)} 批次")

    from joblib import Parallel, delayed
    rmse_start = time.time()
    results = Parallel(n_jobs=_N_CPUS, prefer='processes', verbose=0)(
        delayed(_predict_batch)(batch) for batch in batches
    )

    pred_values = np.concatenate(results).astype(np.float32)
    errors = pred_values - train_ratings
    mse = float(np.mean(errors ** 2))
    rmse = float(np.sqrt(mse))
    rmse_elapsed = time.time() - rmse_start

    verbose_step("RMSE 计算 - 结果",
        f"预测值范围: [{np.min(pred_values):.2f}, {np.max(pred_values):.2f}]\n"
        f"真实值范围: [{np.min(train_ratings):.2f}, {np.max(train_ratings):.2f}]\n"
        f"预测误差: MSE={mse:.4f}, RMSE={rmse:.4f}\n"
        f"预测错误方向: mean_error={np.mean(errors):.4f}（>0 表示低估）\n"
        f"RMSE 计算耗时: {rmse_elapsed:.2f}s",
        verbose)

    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  传统 User-CF 训练耗时: {elapsed:.2f} 秒")

    user_neighbors = {}
    for ui, neighbors in user_sim.items():
        uid = int(all_users[ui])
        user_neighbors[uid] = [(int(all_users[nui]), float(nsim))
                               for nui, nsim in neighbors.items()]

    user_movies = defaultdict(set)
    for uid_val, mid_val in zip(train_df['user_id'], train_df['movie_id']):
        user_movies[int(uid_val)].add(int(mid_val))

    verbose_step("传统 User-CF 训练 - 完成",
        f"邻居数 K={n_neighbors}, RMSE={rmse:.4f}, 耗时={elapsed:.2f}s",
        verbose)

    return {
        'algorithm': 'user_cf_traditional',
        'n_neighbors': n_neighbors,
        'user_neighbors': user_neighbors,
        'user_means': {int(uid): float(user_means[i]) for i, uid in enumerate(all_users)},
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'user_movies': {str(k): list(v) for k, v in user_movies.items()},
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
        'description': '传统User-CF: w_uv=|N(u)∩N(v)|/sqrt(|N(u)|·|N(v)|)，P(u,i)=Σw_uv·r_vi',
    }


def save_model(model, name='user_cf_traditional_model', verbose=False):
    path = os.path.join(MODEL_DIR, f'{name}.pkl')
    print(f"\n[保存模型] {path}")
    verbose_step("模型保存 - 开始", f"模型路径: {path}\n模型键: {list(model.keys())}", verbose)
    with open(path, 'wb') as f:
        pickle.dump(model, f)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  模型大小: {size_mb:.2f} MB")

    meta = {k: v for k, v in model.items()
            if isinstance(v, (str, int, float, bool, list))}
    meta_path = os.path.join(MODEL_DIR, f'{name}_meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  元数据: {meta_path}")
    verbose_step("模型保存 - 完成",
        f"模型文件: {path} ({size_mb:.2f} MB)\n"
        f"元数据: {meta_path}",
        verbose)
    return path


def main():
    parser = argparse.ArgumentParser(description='传统 User-CF 模型训练（多线程）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数 (default: 30)')
    parser.add_argument('--chunk-size', type=int, default=2000, help='分块大小 (default: 2000)')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行进程数')
    parser.add_argument('--verbose', action='store_true', help='输出详细步骤日志到 logs/verbose/')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

    verbose_init('train_usercf_traditional', args.verbose)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用进程数: {_N_CPUS}")
    print(f"[算法] 传统 User-CF (2.2.1): 余弦相似度 + 直接加权求和")
    verbose_step("参数配置",
        f"n_neighbors={args.n_neighbors}, chunk_size={args.chunk_size}, "
        f"n_jobs={args.n_jobs if args.n_jobs is not None else _N_CPUS}",
        args.verbose)

    overall_start = time.time()
    ratings_df = load_data(verbose=args.verbose)

    verbose_step("开始训练", "执行传统 User-CF 相似度计算与预测", args.verbose)
    model = train_usercf_traditional(
        ratings_df,
        n_neighbors=args.n_neighbors,
        chunk_size=args.chunk_size,
        verbose=args.verbose,
    )
    verbose_step("训练完成", f"邻居数={args.n_neighbors}, 分块大小={args.chunk_size}", args.verbose)

    save_model(model, 'user_cf_traditional_model', verbose=args.verbose)
    verbose_step("模型保存完成", "模型已保存至 models/", args.verbose)

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  传统 User-CF 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")
    verbose_step("全部完成", f"总耗时: {total:.2f} 秒, RMSE: {model['rmse']:.4f}", args.verbose)
    verbose_close()


if __name__ == '__main__':
    try:
        with log_output('train_usercf_traditional'):
            main()
    except Exception as e:
        import traceback
        print(f"\n{'='*60}")
        print(f"[致命错误] train_usercf_traditional.py 运行失败：")
        print(f"  错误类型: {type(e).__name__}")
        print(f"  错误信息: {e}")
        print(f"\n堆栈追踪:")
        traceback.print_exc()
        print(f"{'='*60}\n")
        sys.exit(1)
