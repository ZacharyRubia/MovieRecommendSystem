#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_usercf_improved.py - 改进的基于用户的协同过滤（改进 User-CF）
对应 2.2.2 节

改进点：
1. 加权预测公式：r̂_ui = μ_u + Σ sim(u,v)·(r_vi - μ_v) / Σ |sim(u,v)|
2. 评分稳定性因子：w'_uv = sim(u,v) / (1 + α·σ_v)

多线程实现（相似度分块并行 + RMSE 进程池并行）
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
from scipy.stats import pearsonr
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


def compute_pearson_similarity_approx(sparse_R_centered, user_means, top_k=30, chunk_size=2000, verbose=False):
    """
    近似 Pearson 相似度计算（基于去均值向量的余弦相似度）
    分块 + 多线程并行
    """
    from sklearn.metrics.pairwise import cosine_similarity
    from scipy.sparse import dia_matrix

    n_users = sparse_R_centered.shape[0]

    verbose_step("Pearson 相似度 - 归一化",
        f"对去均值矩阵进行 L2 归一化，shape=({n_users}, {sparse_R_centered.shape[1]})",
        verbose)

    norms = np.sqrt(sparse_R_centered.multiply(sparse_R_centered).sum(axis=1)).A.ravel()
    nz = np.sum(norms == 0)
    if nz > 0:
        verbose_step("Pearson 相似度 - 注意", f"{nz} 个用户去均值后为零向量（可能只有一个评分），范数设为 1.0", verbose)
    norms[norms == 0] = 1.0
    inv_norms = dia_matrix((1.0 / norms, [0]), shape=(n_users, n_users))
    normalized = inv_norms @ sparse_R_centered

    verbose_step("Pearson 相似度 - 归一化完成",
        f"归一化后: 各向量 L2 范数应为 1（近似 Pearson）\n"
        f"实际范数: min={np.min(norms):.4f}, max={np.max(norms):.4f}, mean={np.mean(norms):.4f}",
        verbose)

    chunk_ranges = [(i, min(i + chunk_size, n_users)) for i in range(0, n_users, chunk_size)]
    n_chunks = len(chunk_ranges)
    verbose_step("Pearson 相似度 - 分块计算",
        f"分块大小={chunk_size}, 总块数={n_chunks}, 并行进程数={_N_CPUS}\n"
        f"利用去均值余弦相似度近似 Pearson 相关系数\n"
        f"Pearson(u,v) ≈ cos(R_u - μ_u, R_v - μ_v)",
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
    verbose_step("Pearson 相似度 - 统计",
        f"有邻居的用户数: {len(user_sim)} / {n_users} = {len(user_sim)/n_users*100:.1f}%\n"
        f"计算耗时: {chunk_elapsed:.2f}s",
        verbose)

    if user_sim:
        neighbor_counts = [len(nb) for nb in user_sim.values()]
        verbose_step("Pearson 相似度 - 邻居分布",
            f"邻居数: min={min(neighbor_counts)}, max={max(neighbor_counts)}, "
            f"mean={np.mean(neighbor_counts):.1f}, median={np.median(neighbor_counts):.0f}",
            verbose)

    verbose_step("Pearson 相似度 - 完成", f"基于去均值余弦的近似 Pearson 相似度计算完毕", verbose)
    return user_sim


def compute_user_std(train_df, user2idx, n_users, verbose=False):
    """计算每个用户评分的标准差"""
    verbose_step("用户标准差 - 开始", "计算每个用户评分标准差 σ_v 用于稳定性因子", verbose)

    user_sq = np.zeros(n_users, dtype=np.float64)
    user_cnt = np.zeros(n_users, dtype=np.int64)

    for uid_val, rating in zip(train_df['user_id'], train_df['rating']):
        u = user2idx.get(uid_val)
        if u is not None:
            user_sq[u] += rating * rating
            user_cnt[u] += 1

    user_cnt[user_cnt < 2] = 2
    user_std = np.zeros(n_users, dtype=np.float32)
    for u in range(n_users):
        user_std[u] = np.sqrt(user_sq[u] / user_cnt[u])

    verbose_step("用户标准差 - 统计",
        f"用户评分标准差: min={np.min(user_std):.4f}, max={np.max(user_std):.4f}, "
        f"mean={np.mean(user_std):.4f}, median={np.median(user_std):.4f}\n"
        f"标准差=0 的用户: {np.sum(user_std == 0)} / {n_users}",
        verbose)
    return user_std


def train_usercf_improved(train_df, n_neighbors=30, alpha=0.5, chunk_size=2000, verbose=False):
    """
    改进 User-CF 训练
    - 加权预测：r̂_ui = μ_u + Σ sim(u,v)·(r_vi - μ_v) / Σ |sim(u,v)|
    - 稳定性因子：w'_uv = sim(u,v) / (1 + α·σ_v)
    """
    print("\n" + "=" * 60)
    print(f"[改进 User-CF 训练] 邻居数: {n_neighbors} | α: {alpha} | 进程数: {_N_CPUS}")
    verbose_step("改进 User-CF - 算法说明",
        f"改进点 1 - 加权预测公式:\n"
        f"  r̂_ui = μ_u + Σ_{{v∈N_i}} sim(u,v)·(r_vi - μ_v) / Σ_{{v∈N_i}} |sim(u,v)|\n"
        f"  通过均值中心化消除用户评分偏置\n"
        f"改进点 2 - 评分稳定性因子:\n"
        f"  w'_uv = sim(u,v) / (1 + α·σ_v)\n"
        f"  对评分波动大的邻居施加惩罚, α={alpha}",
        verbose)

    start_time = time.time()

    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df, verbose)

    # 用户均值
    verbose_step("用户均值 - 开始", "计算用户评分均值 μ_u 用于中心化", verbose)
    user_means = np.zeros(n_users, dtype=np.float32)
    np.add.at(user_means, u_idx, r_val)
    counts = np.bincount(u_idx, minlength=n_users).astype(np.float32)
    counts[counts == 0] = 1
    user_means /= counts
    verbose_step("用户均值 - 统计",
        f"μ_u: min={np.min(user_means):.2f}, max={np.max(user_means):.2f}, "
        f"global_mean={np.mean(user_means):.2f}",
        verbose)

    # 用户标准差（评分稳定性）
    user_std = compute_user_std(train_df, user2idx, n_users, verbose=verbose)

    # 去均值稀疏矩阵
    verbose_step("去均值矩阵 - 构建",
        f"构建去均值评分矩阵: r'_ui = r_ui - mu_u\n"
        f"该矩阵用于 Pearson 近似计算",
        verbose)
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
    verbose_step("去均值矩阵 - 统计",
        f"矩阵形状: ({n_users}, {n_movies})\n"
        f"非零元素: {non_zero:,}\n"
        f"密度: {density:.4f}%\n"
        f"去均值后值范围: [{np.min(centered_vals):.2f}, {np.max(centered_vals):.2f}]",
        verbose)

    # 计算 Pearson 近似相似度
    user_sim = compute_pearson_similarity_approx(
        sparse_R_centered, user_means, top_k=n_neighbors, chunk_size=chunk_size, verbose=verbose
    )

    # 应用评分稳定性因子：w'_uv = sim(u,v) / (1 + α·σ_v)
    verbose_step("稳定性因子 - 开始",
        f"对 Pearson 相似度施加稳定性因子: w'_uv = sim(u,v) / (1 + {alpha}·σ_v)\n"
        f"σ_v 越大（用户评分越不稳定），惩罚越强",
        verbose)

    orig_sim_count = sum(len(nb) for nb in user_sim.values())
    for ui, neighbors in user_sim.items():
        adjusted = {}
        for nui, sim_val in neighbors.items():
            sigma_v = user_std[nui]
            w_adjusted = sim_val / (1.0 + alpha * sigma_v)
            adjusted[nui] = w_adjusted
        user_sim[ui] = adjusted

    new_sim_count = sum(len(nb) for nb in user_sim.values())
    verbose_step("稳定性因子 - 完成",
        f"原始相似度条目数: {orig_sim_count}\n"
        f"调整后条目数: {new_sim_count}",
        verbose)

    # 构建邻居表
    verbose_step("邻居表构建 - 开始", "将调整后的相似度字典转换为稠密邻居数组", verbose)
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
        f"改进预测公式:\n"
        f"  r̂_ui = μ_u + Σ sim(u,v)·(r_vi - μ_v) / Σ |sim(u,v)|\n"
        f"  其中 sim(u,v) 已施加稳定性因子 w'_{{uv}} = sim(u,v)/(1+α·σ_v)",
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

                # 改进预测：均值中心化 + 加权求和
                neighbor_centered = neighbor_ratings[mask] - user_means[nui_arr[mask]]
                sim_used = sim_arr[mask]
                numerator = float(np.sum(sim_used * neighbor_centered))
                denominator = float(np.sum(np.abs(sim_used)))
                preds[k] = (numerator / denominator + user_means[ui]) if denominator > 0 else user_means[ui]

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
        f"MSE={mse:.4f}, RMSE={rmse:.4f}\n"
        f"平均预测误差: {np.mean(errors):.4f}（>0 表示低估）\n"
        f"RMSE 计算耗时: {rmse_elapsed:.2f}s",
        verbose)

    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  改进 User-CF 训练耗时: {elapsed:.2f} 秒")

    user_neighbors = {}
    for ui, neighbors in user_sim.items():
        uid = int(all_users[ui])
        user_neighbors[uid] = [(int(all_users[nui]), float(nsim))
                               for nui, nsim in neighbors.items()]

    user_movies = defaultdict(set)
    for uid_val, mid_val in zip(train_df['user_id'], train_df['movie_id']):
        user_movies[int(uid_val)].add(int(mid_val))

    verbose_step("改进 User-CF - 完成",
        f"邻居数 K={n_neighbors}, α={alpha}, RMSE={rmse:.4f}, 耗时={elapsed:.2f}s",
        verbose)

    return {
        'algorithm': 'user_cf_improved',
        'n_neighbors': n_neighbors,
        'alpha': alpha,
        'user_neighbors': user_neighbors,
        'user_means': {int(uid): float(user_means[i]) for i, uid in enumerate(all_users)},
        'user_std': {int(uid): float(user_std[i]) for i, uid in enumerate(all_users)},
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
        'description': '改进User-CF: Pearson相似度+均值中心化预测+评分稳定性因子w_uv=sim(u,v)/(1+alpha*sigma_v)',
    }


def save_model(model, name='user_cf_improved_model', verbose=False):
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
    parser = argparse.ArgumentParser(description='改进 User-CF 模型训练（多线程）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数 (default: 30)')
    parser.add_argument('--alpha', type=float, default=0.5, help='稳定性因子惩罚系数 α (default: 0.5)')
    parser.add_argument('--chunk-size', type=int, default=2000, help='分块大小 (default: 2000)')
    parser.add_argument('--n-jobs', type=int, default=None, help='并行进程数')
    parser.add_argument('--verbose', action='store_true', help='输出详细步骤日志到 logs/verbose/')
    args = parser.parse_args()

    if args.n_jobs is not None:
        global _N_CPUS
        _N_CPUS = args.n_jobs
        os.environ["LOKY_MAX_CPU_COUNT"] = str(_N_CPUS)

    verbose_init('train_usercf_improved', args.verbose)

    print(f"[系统] CPU 核心: {os.cpu_count()} | 使用进程数: {_N_CPUS}")
    print(f"[算法] 改进 User-CF (2.2.2): Pearson相似度 + 均值中心化预测 + 稳定性因子")
    verbose_step("参数配置",
        f"n_neighbors={args.n_neighbors}, alpha={args.alpha}, chunk_size={args.chunk_size}, "
        f"n_jobs={args.n_jobs if args.n_jobs is not None else _N_CPUS}",
        args.verbose)

    overall_start = time.time()
    ratings_df = load_data(verbose=args.verbose)

    verbose_step("开始训练", "执行改进 User-CF 训练（Pearson相似度 + 稳定性因子）", args.verbose)
    model = train_usercf_improved(
        ratings_df,
        n_neighbors=args.n_neighbors,
        alpha=args.alpha,
        chunk_size=args.chunk_size,
        verbose=args.verbose,
    )
    verbose_step("训练完成", f"邻居数={args.n_neighbors}, α={args.alpha}", args.verbose)

    save_model(model, 'user_cf_improved_model', verbose=args.verbose)
    verbose_step("模型保存完成", "模型已保存至 models/", args.verbose)

    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  改进 User-CF 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")
    verbose_step("全部完成", f"总耗时: {total:.2f} 秒, RMSE: {model['rmse']:.4f}", args.verbose)
    verbose_close()


if __name__ == '__main__':
    try:
        with log_output('train_usercf_improved'):
            main()
    except Exception as e:
        import traceback
        print(f"\n{'='*60}")
        print(f"[致命错误] train_usercf_improved.py 运行失败：")
        print(f"  错误类型: {type(e).__name__}")
        print(f"  错误信息: {e}")
        print(f"\n堆栈追踪:")
        traceback.print_exc()
        print(f"{'='*60}\n")
        sys.exit(1)
