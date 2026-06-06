#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluate_online_hybrid.py - 在线融合推荐评估脚本

评估目标:
  通过请求线上 API，评估含 Qdrant 内容召回的 4 路 Hybrid 推荐效果，
  并与 SVD、改进 UCF、改进 ICF 单独推荐对比。

与离线评估的区别:
  - 离线 (evaluate_models.py):   用 RMSE + F1@10，基于评分预测值
  - 在线 (本脚本):               用 Precision@K + Recall@K + NDCG@K + Coverage，基于排序质量

评估算法:
  - hybrid:            SVD + 改进UCF + 改进ICF + Qdrant 4路融合 (论文最终公式)
  - svd:               SVD 单独推荐
  - user_cf_traditional:   传统 User-CF 单独推荐
  - user_cf_improved:      改进 User-CF 单独推荐
  - item_cf_traditional:   传统 Item-CF 单独推荐
  - item_cf_improved:      改进 Item-CF 单独推荐

评估指标:
  - Precision@K, Recall@K, F1@K, NDCG@K
  - Coverage (物品覆盖率)
  - ILD (列表内多样性, 需 MySQL 读取 genres 表)
  - ARPL (平均流行度倒数)
  - 有效推荐率 (非空列表比例)

用法:
  python scripts/evaluation/evaluate_online_hybrid.py
  python scripts/evaluation/evaluate_online_hybrid.py --top-n 10 --max-users 200 --algorithms hybrid,svd,user_cf_improved
"""

import os
import sys
import json
import time
import argparse
import warnings
import numpy as np
import pandas as pd
import requests
from collections import defaultdict

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, 'evaluation_results', 'online_hybrid')

DEFAULT_API_BASE = 'http://localhost:3000'
API_TIMEOUT = 30
REQUEST_DELAY = 0.1

DEFAULT_TOP_N = 10
DEFAULT_MAX_USERS = 200

RATING_THRESHOLD = 4.0

MYSQL_CONFIG = {
    'host': os.environ.get('DB_HOST', '192.168.43.38'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', 'newuser'),
    'password': os.environ.get('DB_PASSWORD', 'yourpassword'),
    'database': os.environ.get('DB_NAME', 'MovieRecommendSystem'),
    'charset': 'utf8mb4',
}


def load_test_data(data_dir):
    ratings_path = os.path.join(data_dir, 'test_ratings.csv')
    if not os.path.exists(ratings_path):
        print(f"[错误] 测试数据不存在: {ratings_path}")
        sys.exit(1)

    ratings_df = pd.read_csv(
        ratings_path,
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )

    movies_df = None
    movies_path = os.path.join(data_dir, 'test_movies.csv')
    if os.path.exists(movies_path):
        movies_df = pd.read_csv(movies_path)

    print(f"  评分数: {len(ratings_df)}, 用户数: {ratings_df['user_id'].nunique()}, 电影数: {ratings_df['movie_id'].nunique()}")
    return ratings_df, movies_df


def load_genres_from_mysql():
    try:
        import pymysql
        conn = pymysql.connect(**MYSQL_CONFIG, connect_timeout=10, read_timeout=30, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT mg.movie_id, g.code
            FROM movies_genres mg
            JOIN genres g ON mg.genre_id = g.id
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        genre_dict = {}
        for row in rows:
            mid = row['movie_id']
            if mid not in genre_dict:
                genre_dict[mid] = set()
            genre_dict[mid].add(row['code'])
        print(f"  已加载 genres: {len(genre_dict)} 部电影")
        return genre_dict
    except ImportError:
        print("  [警告] pymysql 未安装，跳过 ILD 计算")
        return None
    except Exception as e:
        print(f"  [警告] MySQL genres 加载失败: {e}")
        return None


def fetch_api_recommendations(api_base, user_id, algorithm, top_n):
    url = f"{api_base}/api/recommend/ai"
    params = {'userId': user_id, 'algorithm': algorithm, 'topN': top_n}
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success') and data.get('data', {}).get('recommendations'):
                return data['data']['recommendations']
        return []
    except requests.exceptions.Timeout:
        print(f"    [{algorithm}] 用户 {user_id} 请求超时")
        return []
    except Exception as e:
        print(f"    [{algorithm}] 用户 {user_id} 请求异常: {e}")
        return []


def compute_precision_recall_f1(recommended_ids, relevant_ids):
    if not recommended_ids:
        return 0.0, 0.0, 0.0
    hits = len(set(recommended_ids) & relevant_ids)
    precision = hits / len(recommended_ids)
    recall = hits / len(relevant_ids) if relevant_ids else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def compute_ndcg_at_k(recommended_ids, relevant_items_with_ratings, k=None):
    if k is None:
        k = len(recommended_ids)
    if not recommended_ids or not relevant_items_with_ratings:
        return 0.0

    relevance_map = {int(mid): max(0.0, (rating - 2.5) / 2.5)
                     for mid, rating in relevant_items_with_ratings.items()}

    dcg = 0.0
    for i, mid in enumerate(recommended_ids[:k]):
        rel = relevance_map.get(int(mid), 0.0)
        if i == 0:
            dcg += rel
        else:
            dcg += rel / np.log2(i + 2)

    ideal_rels = sorted(relevance_map.values(), reverse=True)[:k]
    idcg = 0.0
    for i, rel in enumerate(ideal_rels):
        if i == 0:
            idcg += rel
        else:
            idcg += rel / np.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


def compute_coverage(all_recommended, all_movie_ids):
    recommended_set = set()
    for movie_list in all_recommended.values():
        recommended_set.update(movie_list)
    return len(recommended_set) / len(all_movie_ids) if all_movie_ids else 0.0


def compute_ild(recommended_ids, movie_genres):
    if len(recommended_ids) < 2:
        return 0.0
    total_dist = 0.0
    count = 0
    for i in range(len(recommended_ids)):
        g1 = movie_genres.get(int(recommended_ids[i]), set())
        for j in range(i + 1, len(recommended_ids)):
            g2 = movie_genres.get(int(recommended_ids[j]), set())
            if g1 or g2:
                intersection = len(g1 & g2)
                union = len(g1 | g2)
                jaccard = intersection / union if union > 0 else 0.0
                total_dist += 1.0 - jaccard
                count += 1
    return total_dist / count if count > 0 else 0.0


def compute_arpl(recommended_ids, movie_popularity, total_users):
    if not recommended_ids:
        return 0.0
    arpl_sum = 0.0
    for mid in recommended_ids:
        count = movie_popularity.get(int(mid), 1)
        arpl_sum += np.log2(total_users / count) if count > 0 else 0.0
    return arpl_sum / len(recommended_ids)


def evaluate_algorithm(algo, user_ids, ratings_df, api_base, top_n,
                       test_positive, test_relevant_with_ratings,
                       movie_genres, movie_rating_counts, all_movie_ids,
                       n_total_users, delay=0.1):
    print(f"\n{'=' * 50}")
    print(f"[在线评估] 算法: {algo}")
    print(f"{'=' * 50}")

    metrics = defaultdict(list)
    all_recs = {}
    n_valid = 0

    for i, uid in enumerate(user_ids):
        uid_int = int(uid)
        recs = fetch_api_recommendations(api_base, uid_int, algo, top_n)

        if recs:
            rec_ids = [int(r['movieId']) for r in recs if 'movieId' in r]
        else:
            rec_ids = []

        all_recs[uid_int] = rec_ids
        metrics['valid_rate'].append(1.0 if rec_ids else 0.0)
        if rec_ids:
            n_valid += 1

        rel = test_positive.get(uid_int, set())
        if rel:
            p, r, f = compute_precision_recall_f1(rec_ids, rel)
            metrics['precision'].append(p)
            metrics['recall'].append(r)
            metrics['f1'].append(f)

        rel_with_ratings = test_relevant_with_ratings.get(uid_int, {})
        ndcg = compute_ndcg_at_k(rec_ids, rel_with_ratings, top_n)
        metrics['ndcg'].append(ndcg)

        if movie_genres:
            ild = compute_ild(rec_ids, movie_genres)
            metrics['ild'].append(ild)

        arpl = compute_arpl(rec_ids, movie_rating_counts, n_total_users)
        metrics['arpl'].append(arpl)

        if (i + 1) % max(1, len(user_ids) // 10) == 0:
            print(f"  进度: {i+1}/{len(user_ids)}")

        time.sleep(delay)

    coverage = compute_coverage(all_recs, all_movie_ids) if all_movie_ids else 0.0

    print(f"  有效推荐用户: {n_valid}/{len(user_ids)}")

    return {
        'algorithm': algo,
        'n_users_evaluated': len(user_ids),
        'n_users_with_recs': n_valid,
        **{k: float(np.mean(v)) if v else 0.0 for k, v in metrics.items()},
        'coverage': coverage,
        'recommendations': {str(k): v for k, v in all_recs.items()},
    }


def build_test_ground_truth(ratings_df, eval_user_set):
    rng = np.random.default_rng(42)

    groups = ratings_df.groupby('user_id')
    test_indices = []

    for _, group in groups:
        n = len(group)
        n_test = max(1, min(int(n * 0.2), n - 1))
        mask = np.zeros(n, dtype=bool)
        mask[rng.choice(n, size=n_test, replace=False)] = True
        test_indices.extend(group.index[mask])

    test_df = ratings_df.loc[test_indices]

    test_positive = defaultdict(set)
    test_relevant_with_ratings = defaultdict(dict)
    for _, row in test_df.iterrows():
        uid = int(row['user_id'])
        mid = int(row['movie_id'])
        rating = float(row['rating'])
        if uid in eval_user_set:
            if rating >= RATING_THRESHOLD:
                test_positive[uid].add(mid)
            test_relevant_with_ratings[uid][mid] = rating

    n_with_positive = sum(1 for uid in eval_user_set if uid in test_positive and test_positive[uid])
    n_test_items = sum(len(s) for s in test_positive.values())
    print(f"  Ground Truth: {len(test_df)} 条测试评分, {n_test_items} 个正反馈 ({n_with_positive} 个用户)")
    return test_positive, test_relevant_with_ratings


def print_results_table(all_results):
    print(f"\n\n{'=' * 100}")
    print(f"           在线融合推荐评估结果")
    print(f"{'=' * 100}")

    header = f"{'算法':<22} {'Prec@K':<9} {'Rec@K':<9} {'F1@K':<9} {'NDCG@K':<9} {'Coverage':<9} {'Valid%':<9} {'ILD':<9} {'ARPL':<9}"
    print(f"\n{header}")
    print(f"{'-' * 100}")

    for r in all_results:
        ild = r.get('ild', 0.0)
        valid = r.get('valid_rate', 0.0) * 100
        print(f"{r['algorithm']:<22} "
              f"{r['precision']:<9.4f} {r['recall']:<9.4f} {r['f1']:<9.4f} "
              f"{r['ndcg']:<9.4f} {r['coverage']:<9.4f} {valid:<9.1f} "
              f"{ild:<9.4f} {r['arpl']:<9.4f}")

    print(f"{'─' * 100}")


def save_results(all_results, metadata, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    output = {
        'metadata': metadata,
        'results': all_results,
    }

    json_path = os.path.join(output_dir, 'online_evaluation_results.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  结果已保存: {json_path}")

    csv_rows = []
    for r in all_results:
        csv_rows.append({
            'algorithm': r['algorithm'],
            'precision': r['precision'],
            'recall': r['recall'],
            'f1': r['f1'],
            'ndcg': r['ndcg'],
            'coverage': r['coverage'],
            'valid_rate': r.get('valid_rate', 0.0),
            'ild': r.get('ild', 0.0),
            'arpl': r.get('arpl', 0.0),
            'n_users_with_recs': r['n_users_with_recs'],
        })
    csv_df = pd.DataFrame(csv_rows)
    csv_path = os.path.join(output_dir, 'online_evaluation_summary.csv')
    csv_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"  汇总 CSV: {csv_path}")


def main():
    parser = argparse.ArgumentParser(
        description='在线融合推荐评估脚本 (含 Qdrant 4路 Hybrid)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/evaluation/evaluate_online_hybrid.py
  python scripts/evaluation/evaluate_online_hybrid.py --algorithms hybrid,svd,user_cf_improved
  python scripts/evaluation/evaluate_online_hybrid.py --max-users 500 --top-n 20
  python scripts/evaluation/evaluate_online_hybrid.py --api-base http://192.168.43.38:3000
        """,
    )
    parser.add_argument('--algorithms', type=str,
                        default='hybrid,svd',
                        help='评估的算法列表，逗号分隔。默认: hybrid,svd。注意: UCF/ICF 需模型用80%训练集导出才可评估，否则排除已评分电影后无法命中测试集')
    parser.add_argument('--top-n', type=int, default=DEFAULT_TOP_N,
                        help=f'推荐列表长度 (默认: {DEFAULT_TOP_N})')
    parser.add_argument('--max-users', type=int, default=DEFAULT_MAX_USERS,
                        help=f'最大评估用户数 (默认: {DEFAULT_MAX_USERS})')
    parser.add_argument('--api-base', type=str, default=DEFAULT_API_BASE,
                        help=f'API 服务地址 (默认: {DEFAULT_API_BASE})')
    parser.add_argument('--output-dir', type=str, default=None,
                        help=f'输出目录 (默认: {DEFAULT_OUTPUT_DIR})')
    parser.add_argument('--delay', type=float, default=REQUEST_DELAY,
                        help=f'请求间隔秒数 (默认: {REQUEST_DELAY})')
    parser.add_argument('--skip-ild', action='store_true',
                        help='跳过 ILD 计算 (不连接 MySQL)')

    args = parser.parse_args()

    algorithms = [a.strip() for a in args.algorithms.split(',')]
    output_dir = args.output_dir or DEFAULT_OUTPUT_DIR

    supported = ['hybrid', 'svd',
                 'user_cf_traditional', 'user_cf_improved',
                 'item_cf_traditional', 'item_cf_improved',
                 'user_cf', 'item_cf']
    for algo in algorithms:
        if algo not in supported:
            print(f"[警告] 未知算法 '{algo}'，可用: {supported}")
            algorithms.remove(algo)

    if not algorithms:
        print("[错误] 没有有效的评估算法")
        sys.exit(1)

    print("=" * 60)
    print("  在线融合推荐评估")
    print("=" * 60)
    print(f"  API:     {args.api_base}/api/recommend/ai")
    print(f"  算法:    {', '.join(algorithms)}")
    print(f"  Top-N:   {args.top_n}")
    print(f"  最大用户: {args.max_users}")
    print()

    overall_start = time.time()

    print("1. 加载测试数据 ...")
    ratings_df, movies_df = load_test_data(DATA_DIR)

    all_user_ids = sorted(ratings_df['user_id'].unique())
    all_movie_ids = set(ratings_df['movie_id'].unique())
    eval_user_ids = all_user_ids[:args.max_users]
    eval_user_set = set(eval_user_ids)

    print(f"  评估用户数: {len(eval_user_ids)}")

    print("\n2. 构建测试集 Ground Truth (80/20 split) ...")
    test_positive, test_relevant_with_ratings = build_test_ground_truth(ratings_df, eval_user_set)
    n_with_positive = sum(1 for uid in eval_user_ids if uid in test_positive and test_positive[uid])

    print("\n3. 加载电影流行度 ...")
    movie_rating_counts = {}
    for mid in all_movie_ids:
        count = len(ratings_df[ratings_df['movie_id'] == mid])
        movie_rating_counts[int(mid)] = max(count, 1)

    movie_genres = None
    if not args.skip_ild:
        print("\n4. 加载电影 genres (MySQL) ...")
        movie_genres = load_genres_from_mysql()
    else:
        print("\n4. 跳过 genres 加载 (--skip-ild)")

    print(f"\n5. 评估 {len(algorithms)} 个算法 ...")

    all_results = []
    for algo in algorithms:
        result = evaluate_algorithm(
            algo=algo,
            user_ids=eval_user_ids,
            ratings_df=ratings_df,
            api_base=args.api_base,
            top_n=args.top_n,
            test_positive=test_positive,
            test_relevant_with_ratings=test_relevant_with_ratings,
            movie_genres=movie_genres,
            movie_rating_counts=movie_rating_counts,
            all_movie_ids=all_movie_ids,
            n_total_users=len(all_user_ids),
            delay=args.delay,
        )
        all_results.append(result)

    elapsed = time.time() - overall_start

    metadata = {
        'evaluation_type': 'online_hybrid_with_qdrant',
        'api_base': args.api_base,
        'algorithms': algorithms,
        'top_n': args.top_n,
        'n_users_total': len(all_user_ids),
        'n_users_evaluated': len(eval_user_ids),
        'n_users_with_positive': n_with_positive,
        'n_movies': len(all_movie_ids),
        'n_ratings': len(ratings_df),
        'rating_threshold': RATING_THRESHOLD,
        'request_delay': args.delay,
        'total_time': round(elapsed, 2),
    }

    print_results_table(all_results)

    save_results(all_results, metadata, output_dir)

    print(f"\n总耗时: {elapsed:.2f} 秒")
    print(f"{'=' * 100}")


if __name__ == '__main__':
    main()
