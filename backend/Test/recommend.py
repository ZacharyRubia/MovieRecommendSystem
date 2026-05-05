#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recommend.py - 推荐引擎

加载训练好的模型（SVD / User-CF / Item-CF），
为指定用户生成 Top-N 电影推荐。

用法:
  python recommend.py <user_id> [--algorithm svd|user_cf|item_cf|hybrid] [--top_n 10]
  python recommend.py --interactive   # 交互模式
"""

import os
import sys
import pickle
import json
import argparse
import math
import numpy as np
from collections import defaultdict

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
MODEL_DIR = os.path.join(BASE_DIR, 'models')


# ============================================================
# 模型加载
# ============================================================

def load_model(algorithm='svd'):
    """加载训练好的模型"""
    model_map = {
        'svd': 'svd_model.pkl',
        'user_cf': 'user_cf_model.pkl',
        'item_cf': 'item_cf_model.pkl',
    }

    filename = model_map.get(algorithm)
    if not filename:
        raise ValueError(f"未知算法: {algorithm}, 可选: {list(model_map.keys())}")

    filepath = os.path.join(MODEL_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"模型文件不存在: {filepath}\n请先运行 train_recommend.py 训练模型")

    print(f"[加载模型] {algorithm}: {filepath}")
    with open(filepath, 'rb') as f:
        model = pickle.load(f)

    # 恢复 User-CF/Item-CF 中的键
    if model.get('algorithm') in ('user_cf',):
        # 转换 key 为 int
        model['user_sim_matrix'] = {
            int(k) if isinstance(k, str) else k: v
            for k, v in model['user_sim_matrix'].items()
        }
        model['user_ratings'] = {
            int(k) if isinstance(k, str) else k: {
                int(mk) if isinstance(mk, str) else mk: mv
                for mk, mv in v.items()
            }
            for k, v in model['user_ratings'].items()
        }
        model['user_mean_rating'] = {
            int(k) if isinstance(k, str) else k: v
            for k, v in model['user_mean_rating'].items()
        }

    if model.get('algorithm') in ('item_cf',):
        # 恢复用户电影数据
        model['user_movies'] = {
            int(k) if isinstance(k, str) else k: [
                int(x) if isinstance(x, str) else x for x in v
            ]
            for k, v in model['user_movies'].items()
        }
        model['movie_sim_matrix'] = {
            int(k) if isinstance(k, str) else k: {
                int(mk) if isinstance(mk, str) else mk: mv
                for mk, mv in v.items()
            }
            for k, v in model['movie_sim_matrix'].items()
        }
        model['movie_ratings'] = {
            int(k) if isinstance(k, str) else k: {
                int(mk) if isinstance(mk, str) else mk: mv
                for mk, mv in v.items()
            }
            for k, v in model['movie_ratings'].items()
        }
        model['movie_mean_rating'] = {
            int(k) if isinstance(k, str) else k: v
            for k, v in model['movie_mean_rating'].items()
        }

    print(f"  算法: {model['algorithm']}, "
          f"训练集大小: {model.get('train_size', 'N/A')}")
    return model


# ============================================================
# 电影信息加载
# ============================================================

def load_movies():
    """加载电影信息（用于推荐结果展示）"""
    import pandas as pd
    filepath = os.path.join(DATA_DIR, 'test_movies.csv')
    if not os.path.exists(filepath):
        print("[警告] 电影信息文件不存在")
        return {}
    movies_df = pd.read_csv(filepath)
    movie_dict = {}
    for _, row in movies_df.iterrows():
        movie_dict[row['movie_id']] = {
            'title': row.get('title', '未知'),
            'description': row.get('description', ''),
            'release_year': row.get('release_year', 0),
            'avg_rating': row.get('avg_rating', 0),
        }
    return movie_dict


def load_user_ratings(user_id):
    """加载特定用户的评分历史"""
    import pandas as pd
    filepath = os.path.join(DATA_DIR, 'test_ratings.csv')
    if not os.path.exists(filepath):
        return []
    ratings_df = pd.read_csv(filepath)
    user_data = ratings_df[ratings_df['user_id'] == user_id]
    return user_data.to_dict('records')


# ============================================================
# 推荐算法实现
# ============================================================

def recommend_svd(model, user_id, top_n=10):
    """使用 SVD 模型推荐"""
    user2idx = model['user2idx']
    movie2idx = model['movie2idx']
    idx2movie = model['idx2movie']
    user_features = model['user_features']
    movie_features = model['movie_features']
    user_means = model['user_means']

    # 检查用户是否存在
    if user_id not in user2idx:
        print(f"[警告] 用户 {user_id} 不在训练集中")
        return []

    u_idx = user2idx[user_id]
    user_mean = user_means[u_idx]

    # 为所有电影计算预测评分
    predictions = []
    for mid, m_idx in movie2idx.items():
        pred = np.dot(user_features[u_idx], movie_features[m_idx]) + user_mean
        predictions.append((mid, float(pred)))

    # 按预测评分降序排列
    predictions.sort(key=lambda x: -x[1])
    return predictions[:top_n]


def recommend_user_cf(model, user_id, top_n=10):
    """使用 User-Based CF 推荐"""
    user_ratings = model['user_ratings']
    user_sim_matrix = model['user_sim_matrix']
    user_mean_rating = model['user_mean_rating']
    all_movies = model['all_movies']
    n_neighbors = model.get('n_neighbors', 30)

    # 检查用户是否存在
    if user_id not in user_ratings:
        print(f"[警告] 用户 {user_id} 不在训练集中")
        return []

    # 获取用户的评分电影
    rated_movies = set(user_ratings[user_id].keys())

    # 获取邻居
    sim_users = user_sim_matrix.get(user_id, {})
    if not sim_users:
        print(f"[警告] 用户 {user_id} 没有邻居")
        return []

    # 过滤出有评分的邻居
    neighbors = []
    for nuid, sim in sim_users.items():
        if nuid in user_ratings:
            neighbors.append((nuid, sim))

    neighbors.sort(key=lambda x: -x[1])
    neighbors = neighbors[:n_neighbors]

    if not neighbors:
        return []

    # 为每个未评分的电影计算预测
    uid_mean = user_mean_rating.get(user_id, 3.5)
    predictions = []

    for mid in all_movies:
        if mid in rated_movies:
            continue  # 跳过已评分电影

        num = 0.0
        den = 0.0
        for nuid, sim in neighbors:
            if mid in user_ratings.get(nuid, {}):
                rating = user_ratings[nuid][mid]
                n_mean = user_mean_rating.get(nuid, 3.5)
                num += sim * (rating - n_mean)
                den += abs(sim)

        if den > 0:
            pred = uid_mean + num / den
            predictions.append((mid, float(pred)))

    predictions.sort(key=lambda x: -x[1])
    return predictions[:top_n]


def recommend_item_cf(model, user_id, top_n=10):
    """使用 Item-Based CF 推荐"""
    user_movies = model['user_movies']
    movie_sim_matrix = model['movie_sim_matrix']
    movie_ratings = model['movie_ratings']
    movie_mean_rating = model['movie_mean_rating']
    n_neighbors = model.get('n_neighbors', 30)

    # 检查用户是否存在
    if user_id not in user_movies:
        print(f"[警告] 用户 {user_id} 不在训练集中")
        return []

    user_rated = set(user_movies[user_id])
    all_movies = set(movie_ratings.keys())
    candidate_movies = all_movies - user_rated

    predictions = []
    for mid in candidate_movies:
        sim_movies = movie_sim_matrix.get(mid, {})
        if not sim_movies:
            continue

        neighbors = []
        for rmid in user_rated:
            if rmid in sim_movies:
                sim = sim_movies[rmid]
                if sim > 0:
                    rating = movie_ratings[rmid].get(user_id)
                    if rating is not None:
                        neighbors.append((rmid, sim, rating))

        if not neighbors:
            continue

        neighbors.sort(key=lambda x: -x[1])
        neighbors = neighbors[:n_neighbors]

        num = 0.0
        den = 0.0
        for _, sim, rating in neighbors:
            num += sim * rating
            den += abs(sim)

        if den > 0:
            pred = num / den
            predictions.append((mid, float(pred)))

    predictions.sort(key=lambda x: -x[1])
    return predictions[:top_n]


def recommend_hybrid(model_svd, model_user_cf, model_item_cf,
                     user_id, top_n=10, weights=None):
    """
    混合推荐: 融合 SVD + User-CF + Item-CF 的结果

    weights: dict {'svd': 0.4, 'user_cf': 0.3, 'item_cf': 0.3} (默认)
    """
    if weights is None:
        weights = {'svd': 0.4, 'user_cf': 0.3, 'item_cf': 0.3}

    # 获取各算法推荐（各取 top_n * 3 个候选）
    n_candidates = top_n * 3
    try:
        svd_results = recommend_svd(model_svd, user_id, n_candidates)
    except Exception as e:
        print(f"  SVD 推荐失败: {e}")
        svd_results = []

    try:
        user_cf_results = recommend_user_cf(model_user_cf, user_id, n_candidates)
    except Exception as e:
        print(f"  User-CF 推荐失败: {e}")
        user_cf_results = []

    try:
        item_cf_results = recommend_item_cf(model_item_cf, user_id, n_candidates)
    except Exception as e:
        print(f"  Item-CF 推荐失败: {e}")
        item_cf_results = []

    # 加权融合
    score_map = defaultdict(float)
    weight_sum_map = defaultdict(float)

    for mid, score in svd_results:
        score_map[mid] += score * weights['svd']
        weight_sum_map[mid] += weights['svd']

    for mid, score in user_cf_results:
        score_map[mid] += score * weights['user_cf']
        weight_sum_map[mid] += weights['user_cf']

    for mid, score in item_cf_results:
        score_map[mid] += score * weights['item_cf']
        weight_sum_map[mid] += weights['item_cf']

    # 归一化
    final_scores = []
    for mid in score_map:
        if weight_sum_map[mid] > 0:
            final_scores.append((mid, score_map[mid] / weight_sum_map[mid]))

    final_scores.sort(key=lambda x: -x[1])
    return final_scores[:top_n]


# ============================================================
# 结果展示
# ============================================================

def display_recommendations(recommendations, movie_dict, top_n=10):
    """格式化显示推荐结果"""
    if not recommendations:
        print("  (无推荐结果)")
        return

    print(f"\n{'=' * 70}")
    print(f"  Top-{min(len(recommendations), top_n)} 推荐结果:")
    print(f"{'=' * 70}")
    print(f"{'#':<4} {'电影ID':<8} {'预测评分':<10} {'年份':<8} {'标题'}")
    print(f"{'-' * 70}")

    for i, (movie_id, score) in enumerate(recommendations[:top_n]):
        movie_info = movie_dict.get(movie_id, {})
        title = movie_info.get('title', f'电影-{movie_id}')
        year = movie_info.get('release_year', '')
        avg_rating = movie_info.get('avg_rating', '')
        print(f"{i + 1:<4} {movie_id:<8} {score:<10.4f} {str(year):<8} {title}")

    print(f"{'=' * 70}\n")


def display_user_history(user_id, movie_dict, limit=10):
    """显示用户评分历史"""
    ratings = load_user_ratings(user_id)
    if not ratings:
        print(f"  用户 {user_id} 无评分记录")
        return

    # 按评分降序排列
    ratings.sort(key=lambda x: -x['rating'])
    print(f"\n  --- 用户 {user_id} 的评分历史 (显示前 {min(limit, len(ratings))} 条) ---")
    print(f"  {'电影ID':<8} {'评分':<6} {'标题'}")
    print(f"  {'-' * 50}")
    for r in ratings[:limit]:
        movie_info = movie_dict.get(r['movie_id'], {})
        title = movie_info.get('title', f'电影-{r["movie_id"]}')
        print(f"  {r['movie_id']:<8} {r['rating']:<6.1f} {title}")


# ============================================================
# 交互模式
# ============================================================

def interactive_mode():
    """交互式推荐查询"""
    print("\n" + "=" * 60)
    print("        推荐系统 - 交互模式")
    print("=" * 60)

    # 加载模型
    print("\n[加载] 正在加载模型...")
    try:
        model_svd = load_model('svd')
        model_user_cf = load_model('user_cf')
        model_item_cf = load_model('item_cf')
    except (FileNotFoundError, Exception) as e:
        print(f"[错误] {e}")
        return

    movie_dict = load_movies()
    print("[就绪] 模型加载完成！\n")

    while True:
        print("\n" + "-" * 50)
        print("命令: recommend <user_id> [算法] | history <user_id> | list | quit")
        print("算法: svd | user_cf | item_cf | hybrid (默认)")
        print("-" * 50)

        try:
            cmd = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出交互模式")
            break

        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0].lower()

        if action == 'quit' or action == 'exit' or action == 'q':
            print("退出交互模式")
            break

        elif action == 'list':
            print(f"\n可用电影: {len(movie_dict)} 部")
            print(f"可用用户: 1-1000")

        elif action == 'recommend':
            if len(parts) < 2:
                print("用法: recommend <user_id> [svd|user_cf|item_cf|hybrid]")
                continue

            try:
                uid = int(parts[1])
            except ValueError:
                print("用户ID必须是整数")
                continue

            algorithm = parts[2] if len(parts) > 2 else 'hybrid'

            try:
                top_n = 10
                if algorithm == 'hybrid':
                    results = recommend_hybrid(model_svd, model_user_cf,
                                                model_item_cf, uid, top_n)
                elif algorithm == 'svd':
                    results = recommend_svd(model_svd, uid, top_n)
                elif algorithm == 'user_cf':
                    results = recommend_user_cf(model_user_cf, uid, top_n)
                elif algorithm == 'item_cf':
                    results = recommend_item_cf(model_item_cf, uid, top_n)
                else:
                    print(f"未知算法: {algorithm}")
                    continue

                display_user_history(uid, movie_dict)
                print(f"\n  算法: {algorithm}")
                display_recommendations(results, movie_dict, top_n)

            except Exception as e:
                print(f"[错误] {e}")

        elif action == 'history':
            if len(parts) < 2:
                print("用法: history <user_id>")
                continue

            try:
                uid = int(parts[1])
            except ValueError:
                print("用户ID必须是整数")
                continue

            display_user_history(uid, movie_dict, limit=15)

        else:
            print(f"未知命令: {action}")


# ============================================================
# 命令行模式
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='推荐引擎 - 为用户生成电影推荐')
    parser.add_argument('user_id', type=int, nargs='?', default=None,
                        help='用户ID')
    parser.add_argument('--algorithm', '-a', default='hybrid',
                        choices=['svd', 'user_cf', 'item_cf', 'hybrid'],
                        help='推荐算法 (默认: hybrid)')
    parser.add_argument('--top_n', '-n', type=int, default=10,
                        help='推荐数量 (默认: 10)')
    parser.add_argument('--interactive', '-i', action='store_true',
                        help='交互模式')
    parser.add_argument('--weights', type=str, default=None,
                        help='hybrid 模式权重, 格式: "0.4,0.3,0.3" (svd,user_cf,item_cf)')

    args = parser.parse_args()

    # 交互模式
    if args.interactive or args.user_id is None:
        interactive_mode()
        return

    # 命令行模式
    user_id = args.user_id
    algorithm = args.algorithm
    top_n = args.top_n

    # 加载模型
    print(f"\n[加载] 算法: {algorithm}, 用户: {user_id}, Top-N: {top_n}")
    try:
        model_svd = load_model('svd')
        model_user_cf = load_model('user_cf') if algorithm in ('user_cf', 'hybrid') else None
        model_item_cf = load_model('item_cf') if algorithm in ('item_cf', 'hybrid') else None
    except FileNotFoundError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    movie_dict = load_movies()

    # 显示用户历史
    display_user_history(user_id, movie_dict)

    # 生成推荐
    if algorithm == 'hybrid':
        weights = {'svd': 0.4, 'user_cf': 0.3, 'item_cf': 0.3}
        if args.weights:
            try:
                w = [float(x) for x in args.weights.split(',')]
                if len(w) == 3:
                    weights = {'svd': w[0], 'user_cf': w[1], 'item_cf': w[2]}
            except ValueError:
                pass
        results = recommend_hybrid(model_svd, model_user_cf, model_item_cf,
                                    user_id, top_n, weights)
    elif algorithm == 'svd':
        results = recommend_svd(model_svd, user_id, top_n)
    elif algorithm == 'user_cf':
        results = recommend_user_cf(model_user_cf, user_id, top_n)
    elif algorithm == 'item_cf':
        results = recommend_item_cf(model_item_cf, user_id, top_n)
    else:
        print(f"[错误] 未知算法: {algorithm}")
        sys.exit(1)

    print(f"\n  算法: {algorithm}")
    display_recommendations(results, movie_dict, top_n)


if __name__ == '__main__':
    main()