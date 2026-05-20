#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_itemcf_traditional.py - 传统基于物品的协同过滤（传统 Item-CF）
对应 2.2.3 节

物品相似度：w_ij = |N(i) ∩ N(j)| / sqrt(|N(i)|·|N(j)|)
评分预测：P(u,j) = Σ w_ji · r_ui

单线程实现（简单清晰，适合中小规模数据集）
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
from train_logger import log_output, verbose_init, verbose_step, verbose_close

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


def compute_item_similarity(sparse_R, top_k=30, verbose=False):
    """
    传统物品相似度：w_ij = |N(i) ∩ N(j)| / sqrt(|N(i)|·|N(j)|)
    基于二元交互矩阵，单线程逐个物品计算
    
    等价于对物品的二元交互向量计算余弦相似度
    """
    n_movies = sparse_R.shape[1]
    
    verbose_step("物品相似度 - 二值化",
        f"将评分矩阵转换为二元交互矩阵（0/1），转置为 item×user 格式\n"
        f"shape: ({sparse_R.shape[1]}, {sparse_R.shape[0]})",
        verbose)
    
    # 转置为物品×用户矩阵
    binary_R = sparse_R.copy()
    binary_R.data = np.ones_like(binary_R.data, dtype=np.float32)
    item_user = binary_R.T.tocsr()
    
    # 计算每个物品的 L2 范数
    verbose_step("物品相似度 - 范数计算", "计算每个物品的交互频次 |N(i)| 并归一化", verbose)
    norms = np.sqrt(item_user.multiply(item_user).sum(axis=1)).A.ravel()
    nz = np.sum(norms == 0)
    if nz > 0:
        verbose_step("物品相似度 - 注意", f"{nz} 个物品无交互，范数设为 1.0", verbose)
    norms[norms == 0] = 1.0
    
    # 构建物品电影相似度（单线程）
    verbose_step("物品相似度 - 计算",
        f"相似度公式: w_ij = |N(i) ∩ N(j)| / sqrt(|N(i)|·|N(j)|)\n"
        f"等价于对二值化向量计算余弦相似度\n"
        f"单线程计算 {n_movies} 个物品的 Top-{top_k} 相似度",
        verbose)
    
    item_sim = {}  # {item_idx: {neighbor_idx: sim}}
    
    sim_start = time.time()
    for i in range(n_movies):
        ni = norms[i]
        if ni == 1.0:  # 无交互的电影
            continue
        
        item_i = item_user[i]
        if item_i.nnz == 0:
            continue
        
        # 计算 i 与所有物品的余弦相似度
        # cos(i,j) = (item_i · item_j) / (ni * nj)
        dots = item_user @ item_i.T
        dot_data = dots.toarray().ravel()
        
        similarities = dot_data / (ni * norms)
        similarities[i] = 0.0  # 自相似度置零
        similarities[similarities < 0.01] = 0.0
        
        # 取 Top-K
        if top_k > 0:
            actual_k = min(top_k, n_movies - 1)
            top_idx = np.argpartition(similarities, -actual_k)[-actual_k:]
            top_sim = similarities[top_idx]
            mask = top_sim > 0
            if np.any(mask):
                item_sim[i] = {int(j): float(s) for j, s in zip(top_idx[mask], top_sim[mask])}
        
        if (i + 1) % 500 == 0 or i == n_movies - 1:
            pct = (i + 1) / n_movies * 100
            elapsed = time.time() - sim_start
            print(f"    物品相似度进度: {i+1}/{n_movies} ({pct:.1f}%), 耗时 {elapsed:.2f}s")
    
    sim_elapsed = time.time() - sim_start
    verbose_step("物品相似度 - 统计",
        f"有相似物品的物品数: {len(item_sim)} / {n_movies} = {len(item_sim)/n_movies*100:.1f}%\n"
        f"总计算耗时: {sim_elapsed:.2f}s",
        verbose)
    
    if item_sim:
        neighbor_counts = [len(nb) for nb in item_sim.values()]
        verbose_step("物品相似度 - 邻居分布",
            f"邻居数: min={min(neighbor_counts)}, max={max(neighbor_counts)}, "
            f"mean={np.mean(neighbor_counts):.1f}, median={np.median(neighbor_counts):.0f}",
            verbose)
    
    verbose_step("物品相似度 - 完成", f"传统 Item-CF 相似度计算完毕，Top-{top_k} 邻居", verbose)
    return item_sim


def train_itemcf_traditional(train_df, n_neighbors=30, verbose=False):
    """
    传统 Item-CF 训练
    - 相似度：w_ij = |N(i) ∩ N(j)| / sqrt(|N(i)|·|N(j)|)
    - 预测：P(u,j) = Σ w_ji · r_ui
    """
    print("\n" + "=" * 60)
    print(f"[传统 Item-CF 训练] 邻居数: {n_neighbors} | 单线程")
    verbose_step("传统 Item-CF - 算法说明",
        f"物品相似度公式: w_ij = |N(i) ∩ N(j)| / sqrt(|N(i)|·|N(j)|)\n"
        f"预测公式: P(u,j) = Σ_{{i∈N(u)∩S(j,K)}} w_ji · r_ui\n"
        f"其中 N(i) 为喜欢物品 i 的用户集合, S(j,K) 为与 j 最相似的 K 个物品",
        verbose)
    
    start_time = time.time()
    
    (all_users, all_movies, user2idx, movie2idx, idx2user, idx2movie,
     n_users, n_movies, u_idx, m_idx, r_val) = build_mappings(train_df, verbose)
    
    # 稀疏评分矩阵
    verbose_step("稀疏矩阵 - 构建", f"构建评分矩阵，用户×物品", verbose)
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
        f"密度: {density:.4f}%",
        verbose)
    
    # 计算物品相似度
    item_sim = compute_item_similarity(sparse_R, top_k=n_neighbors, verbose=verbose)
    
    # 构建邻居表
    verbose_step("邻居表构建 - 开始", "将相似度字典转换为稠密邻居数组", verbose)
    max_neighbors = max(len(nb) for nb in item_sim.values()) if item_sim else 0
    sim_nb_idx = np.zeros((n_movies, max_neighbors), dtype=np.int32)
    sim_nb_val = np.zeros((n_movies, max_neighbors), dtype=np.float32)
    sim_nb_cnt = np.zeros(n_movies, dtype=np.int32)
    for mi, neighbors in item_sim.items():
        nb_list = list(neighbors.items())
        cnt = len(nb_list)
        sim_nb_cnt[mi] = cnt
        for j, (nmi, sim) in enumerate(nb_list):
            sim_nb_idx[mi, j] = nmi
            sim_nb_val[mi, j] = sim
    no_nb = np.sum(sim_nb_cnt == 0)
    verbose_step("邻居表构建 - 完成",
        f"邻居表维度: max_neighbors={max_neighbors}\n"
        f"无邻居物品: {no_nb} / {n_movies} = {no_nb/n_movies*100:.1f}%",
        verbose)
    
    # RMSE 计算（单线程）
    train_user_ids = train_df['user_id'].values.astype(np.int32)
    train_movie_ids = train_df['movie_id'].values.astype(np.int32)
    train_ratings = r_val
    
    print(f"\n  [RMSE 计算] 单线程...")
    verbose_step("RMSE 计算 - 开始",
        f"总样本数: {len(train_df)}\n"
        f"预测公式: P(u,j) = Σ_{{i∈N(u)∩S(j,K)}} w_ji · r_ui",
        verbose)
    
    pred_values = np.zeros(len(train_df), dtype=np.float32)
    rmse_start = time.time()
    
    for idx in range(len(train_df)):
        uid = train_user_ids[idx]
        mid = train_movie_ids[idx]
        ui = user2idx.get(uid)
        mi = movie2idx.get(mid)
        
        if ui is None or mi is None:
            pred_values[idx] = 3.0
            continue
        
        nb_cnt = sim_nb_cnt[mi]
        if nb_cnt == 0:
            pred_values[idx] = r_val.mean()
            continue
        
        nmi_arr = sim_nb_idx[mi, :nb_cnt]
        sim_arr = sim_nb_val[mi, :nb_cnt]
        
        # 获取用户对相似物品的评分
        user_ratings = sparse_R[ui, nmi_arr].toarray().ravel()
        mask = user_ratings != 0
        if not np.any(mask):
            pred_values[idx] = r_val.mean()
            continue
        
        numerator = float(np.sum(sim_arr[mask] * user_ratings[mask]))
        denominator = float(np.sum(np.abs(sim_arr[mask])))
        pred_values[idx] = numerator / denominator if denominator > 0 else r_val.mean()
    
    errors = pred_values - train_ratings
    mse = float(np.mean(errors ** 2))
    rmse = float(np.sqrt(mse))
    rmse_elapsed = time.time() - rmse_start
    
    verbose_step("RMSE 计算 - 结果",
        f"预测值范围: [{np.min(pred_values):.2f}, {np.max(pred_values):.2f}]\n"
        f"真实值范围: [{np.min(train_ratings):.2f}, {np.max(train_ratings):.2f}]\n"
        f"MSE={mse:.4f}, RMSE={rmse:.4f}\n"
        f"RMSE 计算耗时: {rmse_elapsed:.2f}s",
        verbose)
    
    elapsed = time.time() - start_time
    print(f"  训练 RMSE: {rmse:.4f}")
    print(f"  传统 Item-CF 训练耗时: {elapsed:.2f} 秒")
    
    # 构建物品相似度字典（输出用）
    item_sim_dict = {}
    for mi, neighbors in item_sim.items():
        mid = int(all_movies[mi])
        item_sim_dict[mid] = [(int(all_movies[nmi]), float(nsim))
                              for nmi, nsim in neighbors.items()]
    
    verbose_step("传统 Item-CF - 完成",
        f"邻居数 K={n_neighbors}, RMSE={rmse:.4f}, 耗时={elapsed:.2f}s",
        verbose)

    user_movies_dict = {}
    for uid, group in train_df.groupby('user_id'):
        user_movies_dict[int(uid)] = [int(m) for m in group['movie_id'].unique()]

    return {
        'algorithm': 'item_cf_traditional',
        'n_neighbors': n_neighbors,
        'item_similarities': item_sim_dict,
        'user_movies': user_movies_dict,
        'user2idx': user2idx,
        'movie2idx': movie2idx,
        'idx2user': idx2user,
        'idx2movie': idx2movie,
        'all_users': [int(u) for u in all_users],
        'all_movies': [int(m) for m in all_movies],
        'rmse': rmse,
        'train_size': len(train_df),
        'train_time': elapsed,
        'description': '传统Item-CF: w_ij=|N(i)∩N(j)|/sqrt(|N(i)|·|N(j)|), P(u,j)=Σw_ji·r_ui',
    }


def save_model(model, name='item_cf_traditional_model', verbose=False):
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
    parser = argparse.ArgumentParser(description='传统 Item-CF 模型训练（单线程）')
    parser.add_argument('--n-neighbors', type=int, default=30, help='邻居数 (default: 30)')
    parser.add_argument('--verbose', action='store_true', help='输出详细步骤日志到 logs/verbose/')
    args = parser.parse_args()
    
    verbose_init('train_itemcf_traditional', args.verbose)
    
    print(f"[算法] 传统 Item-CF (2.2.3): 余弦相似度 + 直接加权求和")
    print(f"[模式] 单线程")
    verbose_step("参数配置",
        f"n_neighbors={args.n_neighbors}",
        args.verbose)
    
    overall_start = time.time()
    ratings_df = load_data(verbose=args.verbose)
    
    verbose_step("开始训练", "执行传统 Item-CF 物品相似度计算与预测（单线程）", args.verbose)
    model = train_itemcf_traditional(
        ratings_df,
        n_neighbors=args.n_neighbors,
        verbose=args.verbose,
    )
    verbose_step("训练完成", f"邻居数={args.n_neighbors}", args.verbose)
    
    save_model(model, 'item_cf_traditional_model', verbose=args.verbose)
    verbose_step("模型保存完成", "模型已保存至 models/", args.verbose)
    
    total = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  传统 Item-CF 训练完成！总耗时: {total:.2f} 秒")
    print(f"{'=' * 60}\n")
    verbose_step("全部完成", f"总耗时: {total:.2f} 秒, RMSE: {model['rmse']:.4f}", args.verbose)
    verbose_close()


if __name__ == '__main__':
    with log_output('train_itemcf_traditional'):
        main()