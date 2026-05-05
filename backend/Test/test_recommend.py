#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_recommend.py - 推荐系统测试评估脚本

功能:
  1. 加载训练好的模型
  2. 计算全面评估指标（RMSE, MAE, Precision@K, Recall@K, Coverage, Diversity）
  3. 对比三种算法性能
  4. 可视化评估结果
  5. 展示推荐样例

运行:
  python test_recommend.py                     # 全面评估
  python test_recommend.py --quick             # 快速评估（小样本）
  python test_recommend.py --user 42           # 评估特定用户
  python test_recommend.py --algorithm svd     # 仅评估 SVD
  python test_recommend.py --demo              # 仅展示推荐样例
"""

import os
import sys
import pickle
import json
import math
import time
import random
import argparse
import numpy as np
from collections import defaultdict

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')

# ---------- 导入推荐引擎 ----------
sys.path.insert(0, BASE_DIR)
from recommend import (
    load_model, load_movies, load_user_ratings,
    recommend_svd, recommend_user_cf, recommend_item_cf, recommend_hybrid,
)


# ============================================================
# 1. 加载测试数据
# ============================================================

def load_test_data():
    """加载完整的数据集用于评估"""
    import pandas as pd

    ratings_df = pd.read_csv(os.path.join(DATA_DIR, 'test_ratings.csv'))
    movies_df = pd.read_csv(os.path.join(DATA_DIR, 'test_movies.csv'))

    print(f"[测试数据] 评分: {len(ratings_df)} 条, "
          f"用户: {ratings_df['user_id'].nunique()}, "
          f"电影: {ratings_df['movie_id'].nunique()}")
    print(f"[测试数据] 电影: {len(movies_df)} 部")

    return ratings_df, movies_df


def get_ground_truth(ratings_df, threshold=4.0):
    """
    获取每个用户的"真实喜好"电影（评分 >= threshold 的电影视为喜欢）
    用于计算 Precision/Recall
    """
    ground_truth = defaultdict(set)
    for _, row in ratings_df.iterrows():
        if row['rating'] >= threshold:
            ground_truth[row['user_id']].add(row['movie_id'])
    return dict(ground_truth)


# ============================================================
# 2. 评分预测评估（RMSE, MAE）
# ============================================================

def evaluate_rating_prediction(model, ratings_df, sample_size=None):
    """
    评估评分预测准确性

    指标:
      - RMSE: Root Mean Square Error
      - MAE: Mean Absolute Error
    """
    algorithm = model['algorithm']

    if sample_size and len(ratings_df) > sample_size:
        eval_df = ratings_df.sample(n=sample_size, random_state=42)
    else:
        eval_df = ratings_df

    print(f"\n  评估评分预测: {len(eval_df)} 条样本...")

    errors_sq = []
    errors_abs = []

    for _, row in eval_df.iterrows():
        uid = row['user_id']
        mid = row['movie_id']
        true_rating = row['rating']

        # 获取预测
        if algorithm == 'svd':
            pred = _predict_svd(model, uid, mid)
        elif algorithm == 'user_cf':
            pred = _predict_user_cf_model(model, uid, mid)
        elif algorithm == 'item_cf':
            pred = _predict_item_cf_model(model, uid, mid)
        else:
            continue

        if pred is not None:
            errors_sq.append((pred - true_rating) ** 2)
            errors_abs.append(abs(pred - true_rating))

    if not errors_sq:
        return {'rmse': float('inf'), 'mae': float('inf'), 'count': 0}

    rmse = math.sqrt(np.mean(errors_sq))
    mae = np.mean(errors_abs)

    return {
        'rmse': float(rmse),
        'mae': float(mae),
        'count': len(errors_sq),
    }


def _predict_svd(model, uid, mid):
    """SVD 单条预测"""
    if uid not in model['user2idx'] or mid not in model['movie2idx']:
        return None
    u = model['user2idx'][uid]
    m = model['movie2idx'][mid]
    pred = np.dot(model['user_features'][u], model['movie_features'][m])
    pred += model['user_means'][u]
    return float(pred)


def _predict_user_cf_model(model, uid, mid):
    """User-CF 单条预测（从 recommend 模块中复用）"""
    from recommend import _predict_user_cf
    return _predict_user_cf(
        uid, mid,
        model['user_ratings'],
        model['user_sim_matrix'],
        model['user_mean_rating'],
        model.get('n_neighbors', 30)
    )


def _predict_item_cf_model(model, uid, mid):
    """Item-CF 单条预测（从 recommend 模块中复用）"""
    from recommend import _predict_item_cf
    # user_movies 在 model 中可能是 dict 形式
    # 需要特殊处理
    user_movies = {}
    for k, v in model['user_movies'].items():
        if isinstance(k, str):
            user_movies[int(k)] = set(v) if isinstance(v, list) else v
        else:
            user_movies[k] = set(v) if isinstance(v, list) else v

    return _predict_item_cf(
        uid, mid,
        model['movie_ratings'],
        model['movie_sim_matrix'],
        model['movie_mean_rating'],
        user_movies,
        model.get('n_neighbors', 30)
    )


# ============================================================
# 3. 排序推荐评估（Precision@K, Recall@K, NDCG）
# ============================================================

def evaluate_ranking(model, ratings_df, ground_truth, k=10, sample_users=None):
    """
    评估推荐排序质量

    指标:
      - Precision@K: 推荐列表中用户喜欢的比例
      - Recall@K: 用户喜欢的电影中被推荐的比例
      - NDCG@K: 归一化折损累计增益
      - HitRate@K: 至少推荐到一个喜欢电影的用户比例
    """
    algorithm = model['algorithm']
    all_users = list(ground_truth.keys())

    if sample_users:
        users = list(set(sample_users) & set(all_users))
    else:
        # 随机抽取 200 个有评价的用户
        n_sample = min(200, len(all_users))
        random.seed(42)
        users = random.sample(all_users, n_sample)

    print(f"\n  评估排序质量: {len(users)} 个用户, K={k}...")

    precisions = []
    recalls = []
    ndcgs = []
    hit_users = 0

    recommend_func_map = {
        'svd': recommend_svd,
        'user_cf': recommend_user_cf,
        'item_cf': recommend_item_cf,
    }

    recommend_func = recommend_func_map.get(algorithm)
    if not recommend_func:
        return {}

    for uid in users:
        true_liked = ground_truth.get(uid, set())
        n_true = len(true_liked)

        if n_true == 0:
            continue

        # 获取推荐结果
        try:
            if algorithm == 'svd':
                recs = recommend_func(model, uid, top_n=k)
            else:
                recs = recommend_func(model, uid, top_n=k)
        except Exception:
            continue

        if not recs:
            continue

        rec_movies = set(mid for mid, _ in recs)

        # 命中数
        hits = len(rec_movies & true_liked)

        # Precision@K
        p = hits / min(k, len(recs))
        precisions.append(p)

        # Recall@K
        r = hits / n_true
        recalls.append(r)

        # HitRate
        if hits > 0:
            hit_users += 1

        # NDCG@K
        dcg = 0.0
        idcg = 0.0
        ideal_hits = min(k, n_true)

        for i, (mid, _) in enumerate(recs[:k]):
            if mid in true_liked:
                dcg += 1.0 / math.log2(i + 2)

        for i in range(ideal_hits):
            idcg += 1.0 / math.log2(i + 2)

        ndcg = dcg / idcg if idcg > 0 else 0.0
        ndcgs.append(ndcg)

    return {
        'precision@{}'.format(k): float(np.mean(precisions)) if precisions else 0,
        'recall@{}'.format(k): float(np.mean(recalls)) if recalls else 0,
        'ndcg@{}'.format(k): float(np.mean(ndcgs)) if ndcgs else 0,
        'hit_rate@{}'.format(k): hit_users / len(users) if users else 0,
        'eval_users': len(users),
    }


# ============================================================
# 4. 多样性 & 覆盖率评估
# ============================================================

def evaluate_diversity(model, ratings_df, n_users=50, k=10):
    """
    评估推荐的多样性和覆盖率

    指标:
      - Coverage: 推荐覆盖的电影比例
      - Intra-list Diversity: 推荐列表内部多样性的平均值
    """
    algorithm = model['algorithm']
    all_users = ratings_df['user_id'].unique()
    all_movies = set(ratings_df['movie_id'].unique())

    # 随机选取用户
    random.seed(42)
    sample_users = random.sample(list(all_users), min(n_users, len(all_users)))

    recommend_func_map = {
        'svd': recommend_svd,
        'user_cf': recommend_user_cf,
        'item_cf': recommend_item_cf,
    }
    recommend_func = recommend_func_map.get(algorithm)

    if not recommend_func:
        return {}

    print(f"\n  评估多样性: {len(sample_users)} 个用户...")

    recommended_movies = set()
    diversity_scores = []

    # 加载电影信息用于计算多样性
    movie_dict = load_movies()

    for uid in sample_users:
        try:
            recs = recommend_func(model, uid, top_n=k)
        except Exception:
            continue

        if not recs:
            continue

        rec_movies = [mid for mid, _ in recs]
        recommended_movies.update(rec_movies)

        # 计算列表内部多样性（基于年份的差异）
        years = []
        for mid in rec_movies:
            info = movie_dict.get(mid, {})
            y = info.get('release_year', 0)
            if y and y > 0:
                years.append(y)

        if len(years) >= 2:
            # 用年份标准差作为多样性指标
            year_diversity = float(np.std(years))
            diversity_scores.append(year_diversity)

    coverage = len(recommended_movies) / len(all_movies) if all_movies else 0

    return {
        'coverage': float(coverage),
        'diversity_mean': float(np.mean(diversity_scores)) if diversity_scores else 0,
        'recommended_movies': len(recommended_movies),
        'total_movies': len(all_movies),
    }


# ============================================================
# 5. 混合模型评估 (使用 recommend_hybrid)
# ============================================================

def evaluate_hybrid(model_svd, model_user_cf, model_item_cf,
                    ratings_df, ground_truth, k=10, sample_users=None):
    """评估混合推荐模型"""
    all_users = list(ground_truth.keys())

    if sample_users:
        users = list(set(sample_users) & set(all_users))
    else:
        n_sample = min(200, len(all_users))
        random.seed(42)
        users = random.sample(all_users, n_sample)

    print(f"\n  评估混合模型 (Hybrid): {len(users)} 个用户, K={k}...")

    precisions = []
    recalls = []
    ndcgs = []
    hit_users = 0

    for uid in users:
        true_liked = ground_truth.get(uid, set())
        n_true = len(true_liked)
        if n_true == 0:
            continue

        try:
            recs = recommend_hybrid(model_svd, model_user_cf, model_item_cf,
                                    uid, top_n=k)
        except Exception:
            continue

        if not recs:
            continue

        rec_movies = set(mid for mid, _ in recs)
        hits = len(rec_movies & true_liked)

        p = hits / min(k, len(recs))
        precisions.append(p)

        r = hits / n_true
        recalls.append(r)

        if hits > 0:
            hit_users += 1

        # NDCG
        dcg = 0.0
        idcg = 0.0
        ideal_hits = min(k, n_true)
        for i, (mid, _) in enumerate(recs[:k]):
            if mid in true_liked:
                dcg += 1.0 / math.log2(i + 2)
        for i in range(ideal_hits):
            idcg += 1.0 / math.log2(i + 2)
        ndcg = dcg / idcg if idcg > 0 else 0.0
        ndcgs.append(ndcg)

    return {
        'precision@{}'.format(k): float(np.mean(precisions)) if precisions else 0,
        'recall@{}'.format(k): float(np.mean(recalls)) if recalls else 0,
        'ndcg@{}'.format(k): float(np.mean(ndcgs)) if ndcgs else 0,
        'hit_rate@{}'.format(k): hit_users / len(users) if users else 0,
        'eval_users': len(users),
    }


# ============================================================
# 6. 用户特定推荐展示
# ============================================================

def demo_recommend_for_user(user_id=1, algorithms=None, top_n=10):
    """为单个用户展示各算法的推荐结果"""
    if algorithms is None:
        algorithms = ['svd', 'user_cf', 'item_cf', 'hybrid']

    print("\n" + "=" * 70)
    print(f"  推荐样例演示 - 用户 #{user_id}")
    print("=" * 70)

    # 加载模型
    models = {}
    for alg in algorithms:
        try:
            models[alg] = load_model(alg)
        except Exception as e:
            print(f"[警告] 加载 {alg} 模型失败: {e}")

    if not models:
        print("[错误] 没有可用模型")
        return

    movie_dict = load_movies()
    user_ratings = load_user_ratings(user_id)

    # 显示用户评分历史
    print(f"\n  --- 用户 #{user_id} 的评分历史 (共 {len(user_ratings)} 条) ---")
    user_ratings.sort(key=lambda x: -x['rating'])
    print(f"  {'电影ID':<8} {'评分':<6} {'标题'}")
    print(f"  {'-' * 55}")
    for r in user_ratings[:10]:
        info = movie_dict.get(r['movie_id'], {})
        title = info.get('title', f'电影-{r["movie_id"]}')
        print(f"  {r['movie_id']:<8} {r['rating']:<6.1f} {title}")

    # 各算法推荐结果
    for alg in algorithms:
        if alg not in models:
            continue

        print(f"\n  >>> 算法: {alg.upper()}")

        try:
            if alg == 'hybrid':
                results = recommend_hybrid(
                    models.get('svd'), models.get('user_cf'),
                    models.get('item_cf'), user_id, top_n
                )
            else:
                results = recommend_func_by_name(alg, models[alg], user_id, top_n)
        except Exception as e:
            print(f"  [错误] {e}")
            continue

        if not results:
            print("  (无推荐结果)")
            continue

        print(f"  {'#':<4} {'电影ID':<8} {'预测评分':<10} {'标题'}")
        print(f"  {'-' * 55}")
        for i, (mid, score) in enumerate(results):
            info = movie_dict.get(mid, {})
            title = info.get('title', f'电影-{mid}')
            print(f"  {i + 1:<4} {mid:<8} {score:<10.4f} {title}")


def recommend_func_by_name(alg, model, uid, top_n):
    """根据算法名称调用对应的推荐函数"""
    if alg == 'svd':
        return recommend_svd(model, uid, top_n)
    elif alg == 'user_cf':
        return recommend_user_cf(model, uid, top_n)
    elif alg == 'item_cf':
        return recommend_item_cf(model, uid, top_n)
    return []


# ============================================================
# 7. 完整评估主函数
# ============================================================

def run_full_evaluation(sample_size=None, quick=False):
    """运行完整的算法评估"""
    print("\n" + "=" * 70)
    print("       推荐系统全面评估")
    print("=" * 70)

    # 加载数据
    ratings_df, movies_df = load_test_data()
    ground_truth = get_ground_truth(ratings_df, threshold=4.0)
    print(f"[真实喜好] 用户数: {len(ground_truth)}")

    # 加载模型
    algorithms = ['svd', 'user_cf', 'item_cf']
    models = {}
    for alg in algorithms:
        try:
            models[alg] = load_model(alg)
            print(f"  {alg}: 已加载")
        except Exception as e:
            print(f"  {alg}: 加载失败 - {e}")

    if not models:
        print("[错误] 没有可用模型，请先运行 train_recommend.py")
        return {}

    # 设定评估参数
    if quick:
        pred_sample = 2000
        rank_users = 50
        div_users = 20
    else:
        pred_sample = sample_size
        rank_users = 200
        div_users = 50

    k_values = [5, 10, 20]
    all_results = {}

    for alg_name, model in models.items():
        print(f"\n{'=' * 50}")
        print(f"  评估算法: {alg_name.upper()}")
        print(f"{'=' * 50}")

        result = {'algorithm': alg_name}

        # 评分预测评估
        print(f"\n[1/3] 评分预测准确性:")
        pred_result = evaluate_rating_prediction(model, ratings_df, pred_sample)
        result['rating_prediction'] = pred_result
        if pred_result.get('rmse'):
            print(f"  RMSE: {pred_result['rmse']:.4f}")
            print(f"  MAE:  {pred_result['mae']:.4f}")

        # 排序质量评估
        print(f"\n[2/3] 排序推荐质量:")
        for k in k_values:
            rank_result = evaluate_ranking(model, ratings_df, ground_truth,
                                           k=k, sample_users=None)
            result[f'ranking_k{k}'] = rank_result
            if rank_result.get('precision@{}'.format(k)):
                print(f"  K={k}: Precision={rank_result['precision@{}'.format(k)]:.4f}, "
                      f"Recall={rank_result['recall@{}'.format(k)]:.4f}, "
                      f"NDCG={rank_result['ndcg@{}'.format(k)]:.4f}")

        # 多样性评估
        print(f"\n[3/3] 多样性 & 覆盖率:")
        div_result = evaluate_diversity(model, ratings_df, n_users=div_users, k=10)
        result['diversity'] = div_result
        print(f"  覆盖率: {div_result.get('coverage', 0):.2%}")
        print(f"  多样性: {div_result.get('diversity_mean', 0):.2f}")
        print(f"  推荐电影: {div_result.get('recommended_movies', 0)} / {div_result.get('total_movies', 0)}")

        all_results[alg_name] = result

    # 混合模型评估
    print(f"\n{'=' * 50}")
    print(f"  评估算法: HYBRID")
    print(f"{'=' * 50}")

    hybrid_results = {'algorithm': 'hybrid'}
    for k in k_values:
        rank_result = evaluate_hybrid(
            models.get('svd'), models.get('user_cf'), models.get('item_cf'),
            ratings_df, ground_truth, k=k
        )
        hybrid_results[f'ranking_k{k}'] = rank_result
        if rank_result.get('precision@{}'.format(k)):
            print(f"  K={k}: Precision={rank_result['precision@{}'.format(k)]:.4f}, "
                  f"Recall={rank_result['recall@{}'.format(k)]:.4f}, "
                  f"NDCG={rank_result['ndcg@{}'.format(k)]:.4f}")

    all_results['hybrid'] = hybrid_results

    return all_results


def print_summary(all_results):
    """打印评估结果汇总对比"""
    if not all_results:
        return

    print("\n" + "=" * 70)
    print("                    评估结果汇总")
    print("=" * 70)

    # RMSE/MAE 对比
    print(f"\n--- 评分预测误差对比 ---")
    print(f"{'算法':<12} {'RMSE':<10} {'MAE':<10}")
    print("-" * 35)
    for alg, result in all_results.items():
        pred = result.get('rating_prediction', {})
        rmse = pred.get('rmse', float('inf'))
        mae = pred.get('mae', float('inf'))
        if rmse != float('inf'):
            print(f"{alg:<12} {rmse:<10.4f} {mae:<10.4f}")

    # Precision/Recall/NDCG 对比 (K=10)
    print(f"\n--- Top-10 排序质量对比 ---")
    print(f"{'算法':<12} {'Precision@10':<15} {'Recall@10':<15} {'NDCG@10':<12} {'HitRate@10':<12}")
    print("-" * 65)
    for alg, result in all_results.items():
        rank_k = result.get('ranking_k10', {})
        p = rank_k.get('precision@10', 0)
        r = rank_k.get('recall@10', 0)
        n = rank_k.get('ndcg@10', 0)
        h = rank_k.get('hit_rate@10', 0)
        print(f"{alg:<12} {p:<15.4f} {r:<15.4f} {n:<12.4f} {h:<12.2%}")

    # 覆盖率/多样性对比
    print(f"\n--- 覆盖率和多样性对比 ---")
    print(f"{'算法':<12} {'覆盖率':<12} {'多样性':<12}")
    print("-" * 40)
    for alg, result in all_results.items():
        div = result.get('diversity', {})
        cov = div.get('coverage', 0)
        d = div.get('diversity_mean', 0)
        if cov > 0:
            print(f"{alg:<12} {cov:<12.2%} {d:<12.2f}")


# ============================================================
# 8. 保存评估结果
# ============================================================

def save_evaluation_results(all_results):
    """保存评估结果到 JSON 文件"""
    output_path = os.path.join(MODEL_DIR, 'evaluation_results.json')

    # 转换为可序列化格式
    serializable = {}
    for alg, result in all_results.items():
        serializable[alg] = {}
        for key, value in result.items():
            if isinstance(value, dict):
                serializable[alg][key] = {
                    str(k): float(v) if isinstance(v, (np.floating, float)) else v
                    for k, v in value.items()
                }
            else:
                serializable[alg][key] = value

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)
    print(f"\n[保存] 评估结果已保存到: {output_path}")


# ============================================================
# 9. 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='推荐系统测试评估')
    parser.add_argument('--quick', action='store_true', help='快速评估（小样本）')
    parser.add_argument('--sample', type=int, default=None, help='评分预测采样数量')
    parser.add_argument('--user', type=int, default=None, help='评估特定用户并展示推荐')
    parser.add_argument('--algorithm', '-a', default=None,
                        choices=['svd', 'user_cf', 'item_cf', 'hybrid'],
                        help='仅评估指定算法')
    parser.add_argument('--demo', action='store_true', help='仅展示推荐样例')
    parser.add_argument('--demo-users', type=str, default='1,42,100',
                        help='推荐样例的用户列表 (逗号分隔)')

    args = parser.parse_args()

    # Demo 模式
    if args.demo:
        user_ids = [int(x.strip()) for x in args.demo_users.split(',')]
        for uid in user_ids[:5]:  # 最多 5 个用户
            demo_recommend_for_user(uid, top_n=10)
        return

    # 特定用户评估
    if args.user is not None:
        user_id = args.user
        ratings_df, _ = load_test_data()
        ground_truth = get_ground_truth(ratings_df)

        algorithms = ['svd', 'user_cf', 'item_cf']
        if args.algorithm:
            algorithms = [args.algorithm]

        for alg in algorithms:
            try:
                model = load_model(alg)
            except Exception as e:
                print(f"[错误] 加载 {alg} 失败: {e}")
                continue

            print(f"\n--- 用户 #{user_id} 的 {alg.upper()} 评估 ---")
            pred_result = evaluate_rating_prediction(
                model, ratings_df[ratings_df['user_id'] == user_id]
            )
            if pred_result.get('count', 0) > 0:
                print(f"  RMSE: {pred_result['rmse']:.4f}")
                print(f"  MAE: {pred_result['mae']:.4f}")
                print(f"  样本数: {pred_result['count']}")

            rank_result = evaluate_ranking(
                model, ratings_df, ground_truth,
                k=10, sample_users=[user_id]
            )
            print(f"  Precision@10: {rank_result.get('precision@10', 0):.4f}")
            print(f"  Recall@10: {rank_result.get('recall@10', 0):.4f}")

        # 展示推荐
        demo_recommend_for_user(user_id, algorithms=algorithms, top_n=10)
        return

    # 全面评估
    all_results = run_full_evaluation(sample_size=args.sample, quick=args.quick)

    if all_results:
        print_summary(all_results)
        save_evaluation_results(all_results)

    print("\n" + "=" * 70)
    print("  评估完成！")
    print("=" * 70)


if __name__ == '__main__':
    main()