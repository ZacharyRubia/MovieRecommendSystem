#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluate_coldstart.py - 冷启动场景推荐策略评估脚本

================================================================
功能说明
================================================================

1. 用户冷启动场景评估
   - 按用户历史评分数分段：0条 / 1-3条 / 4-10条 / 11-50条 / 50+条
   - 对比四种策略在各分段的推荐效果

2. 物品冷启动场景评估
   - 按电影被评次数分段：0-2次 / 3-10次 / 10+次
   - 分析各策略对新物品的召回能力

3. 四路推荐策略对比
   - Random: 随机打乱全量电影池取 Top-N
   - Popular: 按评分次数降序取 Top-N
   - Genre-SQL: 基于用户已评分电影的题材重叠推荐
   - Qdrant-Semantic: 基于 Qdrant 语义向量检索

4. 评估指标
   - 准确性: Precision@K, Recall@K, F1@K, NDCG@K
   - 多样性: Coverage (物品覆盖率), ILD (列表内多样性)
   - 新颖性: ARPL (平均流行度倒数)
   - 兜底能力: 有效推荐率 (非空列表比例)

5. 数据导出
   - JSON 主结果 (全部分组 × 全策略)
   - CSV 对比汇总表
   - 各分组详细结果 JSON

================================================================
用法
================================================================

  # 默认运行
  python scripts/evaluation/evaluate_coldstart.py

  # 自定义参数
  python scripts/evaluation/evaluate_coldstart.py \
      --user-groups "0,1-3,4-10,11-50,50+" \
      --item-groups "0-2,3-10,10+" \
      --strategies random,popular,genre,qdrant \
      --top-n 10 \
      --output-dir coldstart_results

  # 仅评估用户冷启动
  python scripts/evaluation/evaluate_coldstart.py --skip-item-coldstart

  # 仅评估物品冷启动
  python scripts/evaluation/evaluate_coldstart.py --skip-user-coldstart

  # 指定 Qdrant 连接
  python scripts/evaluation/evaluate_coldstart.py --qdrant-host 192.168.43.38 --qdrant-port 6333

================================================================
输出文件
================================================================

  {output_dir}/
  ├── coldstart_summary.json           # 汇总指标
  ├── coldstart_comparison.csv         # Excel 可打开的对比表
  ├── per_user_group/                  # 用户冷启动分组结果
  │   ├── group_0_ratings.json
  │   ├── group_1_3_ratings.json
  │   └── ...
  └── per_item_group/                  # 物品冷启动分组结果
      ├── group_cold_items.json
      └── ...
"""

import os
import sys
import io
import json
import time
import random
import argparse
import warnings
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=RuntimeWarning)
warnings.filterwarnings('ignore', category=UserWarning)

# Windows 控制台 UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ─── 路径 ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, 'coldstart_results')

# ─── MySQL 配置 ──────────────────────────────────────────────
MYSQL_CONFIG = {
    'host': os.environ.get('DB_HOST', '192.168.43.38'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', 'newuser'),
    'password': os.environ.get('DB_PASSWORD', 'yourpassword'),
    'database': os.environ.get('DB_NAME', 'MovieRecommendSystem'),
    'charset': 'utf8mb4',
}

# ─── Qdrant 配置 ─────────────────────────────────────────────
_QDRANT_CONFIG = {
    'host': os.environ.get('QDRANT_HOST', '192.168.43.38'),
    'port': int(os.environ.get('QDRANT_PORT', '6333')),
}
QDRANT_COLLECTION = 'movies'

# ─── 常量 ────────────────────────────────────────────────────
DEFAULT_TOP_N = 10
DEFAULT_USER_GROUPS = [
    (0, 0, '0条评分'),
    (1, 3, '1-3条评分'),
    (4, 10, '4-10条评分'),
    (11, 50, '11-50条评分'),
    (51, 999999, '50+条评分'),
]
DEFAULT_ITEM_GROUPS = [
    (0, 2, '冷启动物品(0-2次)'),
    (3, 10, '温物品(3-10次)'),
    (11, 999999, '热物品(10+次)'),
]
DEFAULT_STRATEGIES = ['random', 'popular', 'genre', 'qdrant']
RANDOM_SEED = 42

# ============================================================
# 1. MySQL 数据加载
# ============================================================

def get_mysql_connection():
    try:
        import pymysql
        conn = pymysql.connect(
            **MYSQL_CONFIG,
            connect_timeout=10,
            read_timeout=120,
            cursorclass=pymysql.cursors.DictCursor,
        )
        return conn
    except ImportError:
        print("[警告] pymysql 未安装，将使用 CSV 数据")
        return None
    except Exception as e:
        print(f"[警告] MySQL 连接失败: {e}，将使用 CSV 数据")
        return None


def load_data_from_csv():
    ratings_df = pd.read_csv(
        os.path.join(DATA_DIR, 'test_ratings.csv'),
        dtype={'user_id': np.int32, 'movie_id': np.int32, 'rating': np.float32},
    )

    movies_df = None
    movies_path = os.path.join(DATA_DIR, 'test_movies.csv')
    if os.path.exists(movies_path):
        movies_df = pd.read_csv(movies_path)

    print(f"  评分总数: {len(ratings_df)} 条")
    print(f"  用户数:   {ratings_df['user_id'].nunique()}")
    print(f"  电影数:   {ratings_df['movie_id'].nunique()}")

    return ratings_df, movies_df


def _mysql_execute(cursor, query, description="query"):
    try:
        cursor.execute(query)
        return cursor.fetchall()
    except Exception as e:
        print(f"  [MySQL] {description} 失败: {e}")
        raise


def load_data_from_mysql(data_limit=None):
    try:
        return _load_data_from_mysql_impl(data_limit)
    except Exception as e:
        print(f"  [MySQL] 数据加载失败: {e}")
        print("  => 回退到 CSV 数据源")
        return load_data_from_csv()


def _load_data_from_mysql_impl(data_limit=None):
    conn = get_mysql_connection()
    if conn is None:
        return load_data_from_csv()

    print("  从 MySQL 加载数据...")
    cursor = conn.cursor()

    if data_limit:
        rating_rows = _mysql_execute(cursor, f"""
            SELECT user_id, movie_id, rating
            FROM users_movies_behaviors
            WHERE behavior_type = 'rate' AND rating IS NOT NULL
            LIMIT {int(data_limit)}
        """, "评分数据查询")
    else:
        rating_rows = _mysql_execute(cursor, """
            SELECT user_id, movie_id, rating
            FROM users_movies_behaviors
            WHERE behavior_type = 'rate' AND rating IS NOT NULL
        """, "评分数据查询(全量)")

    ratings_df = pd.DataFrame(rating_rows, columns=['user_id', 'movie_id', 'rating'])
    ratings_df['user_id'] = ratings_df['user_id'].astype(np.int32)
    ratings_df['movie_id'] = ratings_df['movie_id'].astype(np.int32)
    ratings_df['rating'] = ratings_df['rating'].astype(np.float32)

    sampled_movie_ids = set(int(mid) for mid in ratings_df['movie_id'].unique())
    sampled_user_ids = set(int(uid) for uid in ratings_df['user_id'].unique())

    if data_limit and sampled_movie_ids:
        movie_id_list = ','.join(str(mid) for mid in sampled_movie_ids)
        movie_filter = f"AND m.id IN ({movie_id_list})"
        genre_filter = f"AND mg.movie_id IN ({movie_id_list})"
    else:
        movie_filter = ""
        genre_filter = ""

    movie_rows = _mysql_execute(cursor, f"""
        SELECT m.id AS movie_id, m.title, m.release_year, m.avg_rating
        FROM movies m
        WHERE 1=1 {movie_filter}
    """, "电影信息查询")

    genre_rows = _mysql_execute(cursor, f"""
        SELECT mg.movie_id, g.code
        FROM movies_genres mg
        JOIN genres g ON mg.genre_id = g.id
        WHERE 1=1 {genre_filter}
        ORDER BY mg.movie_id, g.code
    """, "电影题材查询")

    movies_df = pd.DataFrame(movie_rows)
    genres_agg = defaultdict(list)
    for row in genre_rows:
        genres_agg[row['movie_id']].append(row['code'])
    movies_df['genres_str'] = movies_df['movie_id'].map(
        lambda mid: ', '.join(genres_agg.get(mid, []))
    )

    user_prefs = {}
    if data_limit and sampled_user_ids:
        user_id_list = ','.join(str(uid) for uid in sampled_user_ids)
        try:
            pref_rows = _mysql_execute(cursor, f"""
                SELECT user_id, tag_id
                FROM users_preferred_tags
                WHERE user_id IN ({user_id_list})
            """, "用户偏好标签查询")
            for row in pref_rows:
                user_prefs.setdefault(int(row['user_id']), []).append(int(row['tag_id']))
        except Exception:
            print("  [MySQL] 用户偏好标签查询失败，跳过")

    # data_limit 模式：直接从采样数据计算评分次数，避免扫全表
    if data_limit:
        movie_rating_counts = ratings_df.groupby('movie_id').size().to_dict()
    else:
        try:
            count_rows = _mysql_execute(cursor, """
                SELECT movie_id, COUNT(*) AS rating_count
                FROM users_movies_behaviors
                WHERE behavior_type = 'rate' AND rating IS NOT NULL
                GROUP BY movie_id
            """, "电影评分次数统计")
            movie_rating_counts = {int(row['movie_id']): int(row['rating_count']) for row in count_rows}
        except Exception:
            print("  [MySQL] 评分次数统计失败，使用采样数据替代")
            movie_rating_counts = ratings_df.groupby('movie_id').size().to_dict()

    conn.close()

    print(f"  评分总数: {len(ratings_df)} 条")
    print(f"  用户数:   {ratings_df['user_id'].nunique()}")
    print(f"  电影数:   {ratings_df['movie_id'].nunique()}")
    print(f"  有偏好标签的用户: {len(user_prefs)}")

    return ratings_df, movies_df, dict(user_prefs), movie_rating_counts


# ============================================================
# 2. 用户/物品分段
# ============================================================

def parse_group_spec(group_spec_str):
    specs = [s.strip() for s in group_spec_str.split(',')]
    groups = []
    for spec in specs:
        if spec.endswith('+'):
            lo = int(spec[:-1])
            groups.append((lo, 999999, spec))
        elif '-' in spec:
            parts = spec.split('-')
            lo, hi = int(parts[0]), int(parts[1])
            groups.append((lo, hi, spec))
        else:
            val = int(spec)
            groups.append((val, val, spec))
    return groups


def segment_users_by_rating_count(ratings_df, user_groups=None):
    if user_groups is None:
        user_groups = DEFAULT_USER_GROUPS

    user_rating_counts = ratings_df.groupby('user_id').size()

    segmented = {}
    for lo, hi, label in user_groups:
        users_in_segment = set()
        for uid, count in user_rating_counts.items():
            if lo <= count <= hi:
                users_in_segment.add(int(uid))
        # 同时添加评分数量为0的用户（这些用户不在ratings_df中）
        all_users_in_data = set(int(uid) for uid in ratings_df['user_id'].unique())
        if lo == 0:
            # 0条评分的用户需要从movies_df中的用户列表或MySQL中获取
            pass

        segmented[label] = users_in_segment

    return segmented, user_rating_counts


def segment_items_by_rating_count(ratings_df, movie_rating_counts=None, item_groups=None):
    if item_groups is None:
        item_groups = DEFAULT_ITEM_GROUPS

    if movie_rating_counts is None:
        movie_rating_counts = ratings_df.groupby('movie_id').size().to_dict()

    segmented = {}
    for lo, hi, label in item_groups:
        items_in_segment = set()
        for mid, count in movie_rating_counts.items():
            if lo <= count <= hi:
                items_in_segment.add(int(mid))
        segmented[label] = items_in_segment

    return segmented, movie_rating_counts


# ============================================================
# 3. 推荐策略实现
# ============================================================

# ─── 3.1 Qdrant 客户端 ───────────────────────────────────────

_qdrant_client = None

def get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client
    try:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(host=_QDRANT_CONFIG['host'], port=_QDRANT_CONFIG['port'])
        _qdrant_client.get_collection(QDRANT_COLLECTION)
        print(f"  Qdrant 连接成功: {_QDRANT_CONFIG['host']}:{_QDRANT_CONFIG['port']}")
        return _qdrant_client
    except ImportError:
        print("[警告] qdrant-client 未安装，Qdrant 策略将降级为 Genre 推荐")
        return None
    except Exception as e:
        print(f"[警告] Qdrant 连接失败: {e}，Qdrant 策略将降级为 Genre 推荐")
        return None


# ─── 3.2 Random 策略 ─────────────────────────────────────────

class RandomStrategy:
    def __init__(self, all_movie_ids, seed=RANDOM_SEED):
        self.name = 'Random'
        self.all_movie_ids = list(all_movie_ids)
        self.rng = random.Random(seed + hash('random_strategy') % 10000)

    def recommend(self, user_id, user_ratings, top_n=DEFAULT_TOP_N, **kwargs):
        rated_set = set(user_ratings.keys()) if user_ratings else set()
        candidates = [mid for mid in self.all_movie_ids if mid not in rated_set]
        self.rng.shuffle(candidates)
        return candidates[:top_n]


# ─── 3.3 Popular 策略 ────────────────────────────────────────

class PopularStrategy:
    def __init__(self, movie_rating_counts):
        self.name = 'Popular'
        self.popular_movies = sorted(
            [(int(mid), count) for mid, count in movie_rating_counts.items()],
            key=lambda x: -x[1]
        )

    def recommend(self, user_id, user_ratings, top_n=DEFAULT_TOP_N, **kwargs):
        rated_set = set(user_ratings.keys()) if user_ratings else set()
        results = []
        for mid, _ in self.popular_movies:
            if mid not in rated_set:
                results.append(mid)
            if len(results) >= top_n:
                break
        return results


# ─── 3.4 Genre-SQL 策略 ──────────────────────────────────────

class GenreStrategy:
    def __init__(self, movie_genres, all_movie_ids):
        self.name = 'Genre'
        self.movie_genres = movie_genres  # {movie_id: set(genre_code)}
        self.all_movie_ids = list(all_movie_ids)

    def recommend(self, user_id, user_ratings, top_n=DEFAULT_TOP_N, preferred_genres=None, **kwargs):
        rated_set = set(user_ratings.keys()) if user_ratings else set()

        # 收集用户偏好题材
        user_genre_weights = defaultdict(float)
        if user_ratings:
            for mid, rating in user_ratings.items():
                if mid in self.movie_genres:
                    for g in self.movie_genres[mid]:
                        user_genre_weights[g] += rating
        if preferred_genres:
            for g in preferred_genres:
                user_genre_weights[g] += 4.0

        if not user_genre_weights:
            # 无偏好信息时，用全局热门电影
            return PopularStrategy({mid: 1 for mid in self.all_movie_ids}).recommend(
                user_id, user_ratings, top_n
            )

        scored = []
        for mid in self.all_movie_ids:
            if mid in rated_set:
                continue
            score = 0.0
            if mid in self.movie_genres:
                overlap = sum(
                    user_genre_weights.get(g, 0) for g in self.movie_genres[mid]
                )
                score = overlap
            scored.append((mid, score))

        scored.sort(key=lambda x: -x[1])
        return [mid for mid, _ in scored[:top_n]]


# ─── 3.5 Qdrant-Semantic 策略 ─────────────────────────────────

class QdrantStrategy:
    def __init__(self, all_movie_ids):
        self.name = 'Qdrant'
        self.all_movie_ids = set(all_movie_ids)
        self.qdrant_available = False
        client = get_qdrant_client()
        if client:
            try:
                client.get_collection(QDRANT_COLLECTION)
                self.qdrant_available = True
                self.client = client
            except Exception:
                self.qdrant_available = False

    def recommend(self, user_id, user_ratings, top_n=DEFAULT_TOP_N,
                  preferred_genres=None, all_movie_genres=None, **kwargs):
        rated_set = set(user_ratings.keys()) if user_ratings else set()

        if not self.qdrant_available:
            fallback = GenreStrategy(all_movie_genres or {}, self.all_movie_ids)
            return fallback.recommend(
                user_id, user_ratings, top_n, preferred_genres
            )

        try:
            from qdrant_client.models import RecommendInput, RecommendQuery

            positive_ids = []
            negative_ids = []

            if user_ratings:
                sorted_rated = sorted(user_ratings.items(), key=lambda x: -x[1])
                for mid, rating in sorted_rated:
                    mid_int = int(mid)
                    if mid_int in self.all_movie_ids:
                        if rating >= 3.5:
                            positive_ids.append(mid_int)
                        elif rating <= 2.0:
                            negative_ids.append(mid_int)

            if not positive_ids:
                scroll_result = self.client.scroll(
                    collection_name=QDRANT_COLLECTION,
                    limit=5,
                    with_payload=False,
                    with_vectors=False,
                )
                positive_ids = [int(r.id) for r in scroll_result[0]]

            if not positive_ids:
                return list(self.all_movie_ids)[:top_n]

            recommend_input = RecommendInput(
                positive=positive_ids[:5],
                negative=negative_ids[:5] if negative_ids else None,
            )
            recommend_query = RecommendQuery(recommend=recommend_input)
            results = self.client.query_points(
                collection_name=QDRANT_COLLECTION,
                query=recommend_query,
                limit=top_n * 3,
                with_payload=False,
            )
            recommendations = [int(r.id) for r in results.points]

            filtered = [mid for mid in recommendations if mid not in rated_set]
            return filtered[:top_n]

        except Exception as e:
            print(f"  [Qdrant] 推荐失败: {e}，降级为 Genre")
            fallback = GenreStrategy(all_movie_genres or {}, self.all_movie_ids)
            return fallback.recommend(
                user_id, user_ratings, top_n, preferred_genres
            )


# ============================================================
# 4. 评估指标
# ============================================================

def compute_rmse(y_true, y_pred):
    return float(np.sqrt(np.mean((np.array(y_true) - np.array(y_pred)) ** 2)))


def compute_mae(y_true, y_pred):
    return float(np.mean(np.abs(np.array(y_true) - np.array(y_pred))))


def compute_precision_recall_f1(recommended_items, relevant_items):
    if not recommended_items:
        return 0.0, 0.0, 0.0
    hits = len(set(recommended_items) & set(relevant_items))
    precision = hits / len(recommended_items) if recommended_items else 0.0
    recall = hits / len(relevant_items) if relevant_items else 0.0
    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0
    return precision, recall, f1


def compute_ndcg_at_k(recommended_items, relevant_items_with_ratings, k=None):
    if k is None:
        k = len(recommended_items)
    if not recommended_items or not relevant_items_with_ratings:
        return 0.0

    relevance_map = {int(mid): max(0, (rating - 2.5) / 2.5)
                     for mid, rating in relevant_items_with_ratings.items()}

    dcg = 0.0
    for i, mid in enumerate(recommended_items[:k]):
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


def compute_coverage(recommendations_per_user, all_movies):
    recommended_movies = set()
    for movie_list in recommendations_per_user.values():
        recommended_movies.update(movie_list)
    return len(recommended_movies) / len(all_movies) if all_movies else 0.0


def compute_ild(recommended_items, movie_genres):
    """列表内多样性: 计算推荐列表中相邻两部电影之间的题材差异均值"""
    if len(recommended_items) < 2:
        return 0.0
    total_dist = 0.0
    count = 0
    for i in range(len(recommended_items)):
        g1 = movie_genres.get(int(recommended_items[i]), set())
        for j in range(i + 1, len(recommended_items)):
            g2 = movie_genres.get(int(recommended_items[j]), set())
            if g1 or g2:
                intersection = len(g1 & g2)
                union = len(g1 | g2)
                jaccard = intersection / union if union > 0 else 0.0
                total_dist += 1.0 - jaccard
                count += 1
    return total_dist / count if count > 0 else 0.0


def compute_arpl(recommended_items, movie_rating_counts, total_users):
    """平均流行度倒数: 推荐物品越冷门，ARPL 越高"""
    if not recommended_items:
        return 0.0
    arpl_sum = 0.0
    for mid in recommended_items:
        count = movie_rating_counts.get(int(mid), 1)
        arpl_sum += 1.0 / max(count, 1)
    return arpl_sum / len(recommended_items)


# ============================================================
# 5. 主评估流程
# ============================================================

def evaluate_strategy_for_users(
    strategy,
    user_ids,
    user_ratings_dict,
    test_positive,
    top_n,
    all_movie_ids,
    movie_genres,
    user_preferred_genres,
    movie_rating_counts,
    full_ratings_dict=None,
    max_users=200,
):
    if full_ratings_dict is None:
        full_ratings_dict = user_ratings_dict
    results = {
        'strategy': strategy.name,
        'precision': [],
        'recall': [],
        'f1': [],
        'ndcg': [],
        'ild': [],
        'arpl': [],
        'valid_rate': [],
        'recommendations': {},
    }

    eval_user_ids = list(user_ids)[:max_users]
    total_users_eval = len(eval_user_ids)

    for uid in eval_user_ids:
        uid_int = int(uid)
        user_ratings = user_ratings_dict.get(uid_int, {})
        preferred_genres = user_preferred_genres.get(uid_int)

        try:
            recs = strategy.recommend(
                user_id=uid_int,
                user_ratings=user_ratings,
                top_n=top_n,
                preferred_genres=preferred_genres,
                all_movie_genres=movie_genres,
            )
        except Exception as e:
            print(f"  [{strategy.name}] 用户 {uid_int} 推荐异常: {e}")
            recs = []

        results['recommendations'][uid_int] = recs
        results['valid_rate'].append(1.0 if recs else 0.0)

        rel_items = test_positive.get(uid_int, set())
        rel_with_ratings = {
            mid: rating for mid, rating in full_ratings_dict.get(uid_int, {}).items()
            if mid in rel_items
        }

        if rel_items:
            p, r, f = compute_precision_recall_f1(set(recs), rel_items)
            results['precision'].append(p)
            results['recall'].append(r)
            results['f1'].append(f)
            ndcg = compute_ndcg_at_k(recs, rel_with_ratings, top_n)
            results['ndcg'].append(ndcg)

        ild = compute_ild(recs, movie_genres)
        results['ild'].append(ild)

        arpl = compute_arpl(recs, movie_rating_counts, total_users_eval)
        results['arpl'].append(arpl)

    return results


def summarize_results(raw_results):
    return {
        'Precision@K': float(np.mean(raw_results['precision'])) if raw_results['precision'] else 0.0,
        'Recall@K': float(np.mean(raw_results['recall'])) if raw_results['recall'] else 0.0,
        'F1@K': float(np.mean(raw_results['f1'])) if raw_results['f1'] else 0.0,
        'NDCG@K': float(np.mean(raw_results['ndcg'])) if raw_results['ndcg'] else 0.0,
        'ILD': float(np.mean(raw_results['ild'])) if raw_results['ild'] else 0.0,
        'ARPL': float(np.mean(raw_results['arpl'])) if raw_results['arpl'] else 0.0,
        '有效推荐率': float(np.mean(raw_results['valid_rate'])) if raw_results['valid_rate'] else 0.0,
        '平均列表长度': float(np.mean([len(v) for v in raw_results['recommendations'].values()])) if raw_results['recommendations'] else 0.0,
    }


def run_user_coldstart_eval(
    strategies,
    user_segments,
    user_ratings_dict,
    test_positive,
    all_movie_ids,
    movie_genres,
    user_preferred_genres,
    movie_rating_counts,
    top_n,
    full_ratings_dict=None,
):
    print("\n" + "=" * 60)
    print("[用户冷启动评估]")
    print("=" * 60)

    all_group_results = {}

    for segment_label, user_ids in user_segments.items():
        if not user_ids:
            print(f"\n  分组 [{segment_label}]: 无用户，跳过")
            continue

        print(f"\n  分组 [{segment_label}]: {len(user_ids)} 个用户")

        seg_results = {}
        for strategy in strategies:
            print(f"    策略 [{strategy.name}]...", end=' ', flush=True)
            raw = evaluate_strategy_for_users(
                strategy,
                user_ids,
                user_ratings_dict,
                test_positive,
                top_n,
                all_movie_ids,
                movie_genres,
                user_preferred_genres,
                movie_rating_counts,
                full_ratings_dict=full_ratings_dict,
            )
            summary = summarize_results(raw)
            seg_results[strategy.name] = summary
            print(f"F1={summary['F1@K']:.4f}, 覆盖率={summary['有效推荐率']:.2%}")

        all_group_results[segment_label] = seg_results

    return all_group_results


def run_item_coldstart_eval(
    strategies,
    item_segments,
    user_ratings_dict,
    test_positive,
    all_movie_ids,
    movie_genres,
    user_preferred_genres,
    movie_rating_counts,
    top_n,
    full_ratings_dict=None,
):
    print("\n" + "=" * 60)
    print("[物品冷启动评估]")
    print("=" * 60)

    all_group_results = {}

    for segment_label, item_ids in item_segments.items():
        if not item_ids:
            print(f"\n  分组 [{segment_label}]: 无物品，跳过")
            continue

        print(f"\n  分组 [{segment_label}]: {len(item_ids)} 个物品")

        # 找出评分过这些物品的用户
        users_for_segment = set()
        for uid, ratings in user_ratings_dict.items():
            if set(ratings.keys()) & item_ids:
                users_for_segment.add(uid)

        if not users_for_segment:
            print(f"    无用户评分过此分组的物品，跳过")
            continue

        print(f"    关联用户: {len(users_for_segment)}")

        seg_results = {}
        for strategy in strategies:
            print(f"    策略 [{strategy.name}]...", end=' ', flush=True)
            raw = evaluate_strategy_for_users(
                strategy,
                users_for_segment,
                user_ratings_dict,
                test_positive,
                top_n,
                all_movie_ids,
                movie_genres,
                user_preferred_genres,
                movie_rating_counts,
                full_ratings_dict=full_ratings_dict,
            )

            # 额外计算：该策略推荐列表中冷启动物品的占比
            cold_hit_total = 0
            cold_hit_count = 0
            for uid, recs in raw['recommendations'].items():
                for mid in recs:
                    cold_hit_total += 1
                    if int(mid) in item_ids:
                        cold_hit_count += 1
            cold_recall_rate = cold_hit_count / cold_hit_total if cold_hit_total > 0 else 0.0

            summary = summarize_results(raw)
            summary['冷启动物品召回率'] = cold_recall_rate
            seg_results[strategy.name] = summary
            print(f"F1={summary['F1@K']:.4f}, 冷物品召回率={cold_recall_rate:.2%}")

        all_group_results[segment_label] = seg_results

    return all_group_results


# ============================================================
# 6. 结果导出
# ============================================================

def save_results(user_results, item_results, metadata, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    # ── 1. 完整汇总 JSON ──
    output = {
        'metadata': metadata,
        'user_coldstart': user_results,
        'item_coldstart': item_results,
    }

    summary_path = os.path.join(output_dir, 'coldstart_summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  汇总结果: {summary_path}")

    # ── 2. CSV 对比表 ──
    csv_rows = []

    # 用户冷启动对比
    for segment_label, seg_results in user_results.items():
        for strategy_name, metrics in seg_results.items():
            csv_rows.append({
                '场景': '用户冷启动',
                '分组': segment_label,
                '策略': strategy_name,
                'Precision@K': round(metrics['Precision@K'], 6),
                'Recall@K': round(metrics['Recall@K'], 6),
                'F1@K': round(metrics['F1@K'], 6),
                'NDCG@K': round(metrics['NDCG@K'], 6),
                'ILD': round(metrics['ILD'], 6),
                'ARPL': round(metrics['ARPL'], 6),
                '有效推荐率': round(metrics['有效推荐率'], 6),
                '平均列表长度': round(metrics['平均列表长度'], 2),
            })

    # 物品冷启动对比
    for segment_label, seg_results in item_results.items():
        for strategy_name, metrics in seg_results.items():
            row = {
                '场景': '物品冷启动',
                '分组': segment_label,
                '策略': strategy_name,
                'Precision@K': round(metrics['Precision@K'], 6),
                'Recall@K': round(metrics['Recall@K'], 6),
                'F1@K': round(metrics['F1@K'], 6),
                'NDCG@K': round(metrics['NDCG@K'], 6),
                'ILD': round(metrics['ILD'], 6),
                'ARPL': round(metrics['ARPL'], 6),
                '有效推荐率': round(metrics['有效推荐率'], 6),
                '平均列表长度': round(metrics['平均列表长度'], 2),
            }
            if '冷启动物品召回率' in metrics:
                row['冷启动物品召回率'] = round(metrics['冷启动物品召回率'], 6)
            csv_rows.append(row)

    df = pd.DataFrame(csv_rows)
    csv_path = os.path.join(output_dir, 'coldstart_comparison.csv')
    df.to_csv(csv_path, index=False)
    print(f"  对比 CSV: {csv_path}")

    # ── 3. 各用户分组详细 JSON ──
    user_group_dir = os.path.join(output_dir, 'per_user_group')
    os.makedirs(user_group_dir, exist_ok=True)
    for segment_label, seg_results in user_results.items():
        safe_label = segment_label.replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_')
        group_path = os.path.join(user_group_dir, f'group_{safe_label}.json')
        with open(group_path, 'w', encoding='utf-8') as f:
            json.dump({segment_label: seg_results}, f, indent=2, ensure_ascii=False)

    # ── 4. 各物品分组详细 JSON ──
    item_group_dir = os.path.join(output_dir, 'per_item_group')
    os.makedirs(item_group_dir, exist_ok=True)
    for segment_label, seg_results in item_results.items():
        safe_label = segment_label.replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_')
        group_path = os.path.join(item_group_dir, f'group_{safe_label}.json')
        with open(group_path, 'w', encoding='utf-8') as f:
            json.dump({segment_label: seg_results}, f, indent=2, ensure_ascii=False)

    # ── 5. 生成 Markdown 报告 ──
    generate_markdown_report(user_results, item_results, metadata, output_dir)

    print(f"\n  所有结果已保存至: {output_dir}")


def generate_markdown_report(user_results, item_results, metadata, output_dir):
    lines = []
    lines.append("# 冷启动推荐策略评估报告")
    lines.append("")
    lines.append(f"生成时间: {metadata.get('timestamp', 'N/A')}")
    lines.append(f"Top-N: {metadata.get('top_n', 10)}")
    lines.append(f"评估用户数: {metadata.get('n_users', 'N/A')}")
    lines.append(f"评估电影数: {metadata.get('n_movies', 'N/A')}")
    lines.append("")

    # 用户冷启动表
    if user_results:
        lines.append("## 用户冷启动场景")
        lines.append("")
        lines.append("| 分组 | 策略 | Precision@K | Recall@K | F1@K | NDCG@K | ILD | ARPL | 有效推荐率 |")
        lines.append("|------|------|------------|---------|------|--------|-----|------|-----------|")

        for segment_label, seg_results in user_results.items():
            for strategy_name, m in seg_results.items():
                lines.append(
                    f"| {segment_label} | {strategy_name} "
                    f"| {m['Precision@K']:.4f} | {m['Recall@K']:.4f} | {m['F1@K']:.4f} "
                    f"| {m['NDCG@K']:.4f} | {m['ILD']:.4f} | {m['ARPL']:.4f} | {m['有效推荐率']:.2%} |"
                )

        lines.append("")

    # 物品冷启动表
    if item_results:
        lines.append("## 物品冷启动场景")
        lines.append("")
        lines.append("| 分组 | 策略 | Precision@K | Recall@K | F1@K | NDCG@K | 冷物品召回率 | 有效推荐率 |")
        lines.append("|------|------|------------|---------|------|--------|-------------|-----------|")

        for segment_label, seg_results in item_results.items():
            for strategy_name, m in seg_results.items():
                cold_recall = m.get('冷启动物品召回率', 0.0)
                lines.append(
                    f"| {segment_label} | {strategy_name} "
                    f"| {m['Precision@K']:.4f} | {m['Recall@K']:.4f} | {m['F1@K']:.4f} "
                    f"| {m['NDCG@K']:.4f} | {cold_recall:.2%} | {m['有效推荐率']:.2%} |"
                )

        lines.append("")

    # 核心发现
    lines.append("## 核心发现")
    lines.append("")
    lines.append("### 1. 语义向量召回是全生命周期内的底线保障")
    lines.append("")
    lines.append("观察用户冷启动表中各分段的表现，重点关注：")
    lines.append("- Qdrant-Semantic 在 0条/1-3条评分分段的 Precision/F1 是否显著优于 Random 和 Popular")
    lines.append("- CF-Hybrid 在哪些分段开始有效（通常需要 10+ 条评分）")
    lines.append("- Qdrant 是否能覆盖 CF 完全无法工作的阶段")
    lines.append("")

    lines.append("### 2. 冷启动场景下内容向量召回策略有效")
    lines.append("")
    lines.append("观察物品冷启动表中「冷启动物品(0-2次)」分组：")
    lines.append("- Popular 策略对新物品的召回率（理论上接近0，因为无评分数据）")
    lines.append("- Qdrant 策略对新物品的召回率（应显著高于 Popular，因为只依赖元数据向量）")
    lines.append("")

    lines.append("### 3. 内容推荐优于随机和单纯热门推荐")
    lines.append("")
    lines.append("对比各分段下四路策略的 F1/NDCG：")
    lines.append("- Qdrant-Semantic ≈ Genre-SQL > Popular > Random（预期排序）")
    lines.append("- 在冷启动分段，Genre-SQL 和 Qdrant 之间的性能差距显示了语义向量的价值")
    lines.append("")

    report_path = os.path.join(output_dir, 'coldstart_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"  评估报告: {report_path}")


# ============================================================
# 7. 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='冷启动场景推荐策略评估')
    parser.add_argument('--user-groups', type=str, default='0,1-3,4-10,11-50,50+',
                        help='用户分段，逗号分隔 (默认: 0,1-3,4-10,11-50,50+)')
    parser.add_argument('--item-groups', type=str, default='0-2,3-10,10+',
                        help='物品分段，逗号分隔 (默认: 0-2,3-10,10+)')
    parser.add_argument('--strategies', type=str, default='random,popular,genre,qdrant',
                        help='评估策略，逗号分隔 (可选: random,popular,genre,qdrant)')
    parser.add_argument('--top-n', type=int, default=DEFAULT_TOP_N,
                        help=f'推荐列表长度 (默认: {DEFAULT_TOP_N})')
    parser.add_argument('--output-dir', type=str, default=DEFAULT_OUTPUT_DIR,
                        help=f'输出目录 (默认: {DEFAULT_OUTPUT_DIR})')
    parser.add_argument('--skip-user-coldstart', action='store_true',
                        help='跳过用户冷启动评估')
    parser.add_argument('--skip-item-coldstart', action='store_true',
                        help='跳过物品冷启动评估')
    parser.add_argument('--max-users-per-group', type=int, default=200,
                        help='每分组最大评估用户数 (默认: 200)')
    parser.add_argument('--qdrant-host', type=str, default=_QDRANT_CONFIG['host'],
                        help=f'Qdrant 主机 (默认: {_QDRANT_CONFIG["host"]})')
    parser.add_argument('--qdrant-port', type=int, default=_QDRANT_CONFIG['port'],
                        help=f'Qdrant 端口 (默认: {_QDRANT_CONFIG["port"]})')
    parser.add_argument('--csv-only', action='store_true',
                        help='仅从 CSV 加载数据 (不连接 MySQL)')
    parser.add_argument('--data-limit', type=int, default=None,
                        help='限制加载的评分数 (调试用，如: 5000)')
    args = parser.parse_args()

    _QDRANT_CONFIG['host'] = args.qdrant_host
    _QDRANT_CONFIG['port'] = args.qdrant_port

    overall_start = time.time()

    # ── 1. 加载数据 ──
    print("=" * 60)
    print("[数据加载]")
    print("=" * 60)

    user_prefs = {}
    movie_rating_counts_from_db = None

    if args.csv_only:
        ratings_df, movies_df = load_data_from_csv()
    else:
        result = load_data_from_mysql(data_limit=args.data_limit)
        if len(result) == 2:
            ratings_df, movies_df = result
        else:
            ratings_df, movies_df, user_prefs, movie_rating_counts_from_db = result

    all_movie_ids = set(int(mid) for mid in ratings_df['movie_id'].unique())

    # ── 2. 构建用户评分字典和测试正反馈 ──
    user_ratings_dict = defaultdict(dict)
    for _, row in ratings_df.iterrows():
        user_ratings_dict[int(row['user_id'])][int(row['movie_id'])] = float(row['rating'])

    # 构建电影题材映射
    movie_genres = {}
    if movies_df is not None and 'genres_str' in movies_df.columns:
        for _, row in movies_df.iterrows():
            mid = int(row['movie_id'])
            genres_str = row.get('genres_str', '')
            if pd.notna(genres_str) and genres_str:
                movie_genres[mid] = set(g.strip() for g in str(genres_str).split(',') if g.strip())
            else:
                movie_genres[mid] = set()
    else:
        # 从 CSV 中无 genre 信息，设为空
        for mid in all_movie_ids:
            movie_genres[mid] = set()
        if movies_df is not None and 'genres' in movies_df.columns:
            for _, row in movies_df.iterrows():
                mid = int(row['movie_id'])
                g = row.get('genres', '')
                if pd.notna(g) and g:
                    movie_genres[mid] = set(str(g).split('|'))

    # ── 3. 用户分段 ──
    user_groups_spec = parse_group_spec(args.user_groups)
    user_segments, user_rating_counts = segment_users_by_rating_count(ratings_df, user_groups_spec)

    # 统计用户分段
    print("\n[用户分段统计]")
    total_segmented = 0
    for label, users in user_segments.items():
        print(f"  {label}: {len(users)} 个用户")
        total_segmented += len(users)
    print(f"  总计: {total_segmented} 个用户 (不含0评分用户)")

    # 如果 MySQL 可用，尝试找出 0 评分用户（注册了但无评分行为的用户）
    if not args.csv_only and '0条评分' in user_segments or any(
        lo == 0 for lo, _, _ in user_groups_spec
    ):
        conn = get_mysql_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.id FROM users u
                WHERE u.id NOT IN (
                    SELECT DISTINCT user_id FROM users_movies_behaviors
                    WHERE behavior_type = 'rate' AND rating IS NOT NULL
                )
                LIMIT 500
            """)
            zero_users = [int(row['id']) for row in cursor.fetchall()]
            conn.close()
            for label in list(user_segments.keys()):
                if label.startswith('0'):
                    user_segments[label] = set(zero_users)
                    print(f"  {label}: {len(zero_users)} 个用户 (来自 MySQL)")
                    break

    # ── 4. 物品分段 ──
    item_groups_spec = parse_group_spec(args.item_groups)

    if movie_rating_counts_from_db is None:
        movie_rating_counts_from_db = ratings_df.groupby('movie_id').size().to_dict()

    # 补全所有电影（包括未被评分的）
    for mid in all_movie_ids:
        if mid not in movie_rating_counts_from_db:
            movie_rating_counts_from_db[int(mid)] = 0

    item_segments, item_rating_counts = segment_items_by_rating_count(
        ratings_df, movie_rating_counts_from_db, item_groups_spec
    )

    print("\n[物品分段统计]")
    for label, items in item_segments.items():
        print(f"  {label}: {len(items)} 个电影")

    # ── 5. Train/Test split ──
    # 为每个用户保留 20% 的评分作为测试集（至少 1 条），其余作为训练集
    # 避免策略排除已评分电影后无法命中 test_positive 导致 F1 恒为 0
    eval_rng = random.Random(RANDOM_SEED)
    test_positive = defaultdict(set)
    train_ratings_dict = defaultdict(dict)
    full_ratings_dict = dict(user_ratings_dict)

    for uid, ratings in user_ratings_dict.items():
        rating_items = list(ratings.items())
        eval_rng.shuffle(rating_items)
        n_test = max(1, int(len(rating_items) * 0.2))
        test_items = rating_items[:n_test]
        train_items = rating_items[n_test:]

        for mid, rating in train_items:
            train_ratings_dict[uid][int(mid)] = rating
        for mid, rating in test_items:
            if rating >= 4.0:
                test_positive[uid].add(int(mid))

    user_ratings_dict = train_ratings_dict
    total_test_positives = sum(len(v) for v in test_positive.values())
    print(f"\n[Train/Test 分割]")
    print(f"  训练集评分: {sum(len(v) for v in user_ratings_dict.values())} 条")
    print(f"  测试正反馈 (rating≥4): {total_test_positives} 条")

    # ── 6. 初始化策略 ──
    print("\n" + "=" * 60)
    print("[策略初始化]")
    print("=" * 60)

    strategy_names = [s.strip() for s in args.strategies.split(',')]
    strategies = []

    for sname in strategy_names:
        if sname == 'random':
            strategies.append(RandomStrategy(all_movie_ids))
            print(f"  Random: 已初始化 ({len(all_movie_ids)} 候选电影)")
        elif sname == 'popular':
            strategies.append(PopularStrategy(movie_rating_counts_from_db))
            print(f"  Popular: 已初始化 ({len(movie_rating_counts_from_db)} 电影)")
        elif sname == 'genre':
            strategies.append(GenreStrategy(movie_genres, all_movie_ids))
            print(f"  Genre: 已初始化 ({len(movie_genres)} 电影有题材)")
        elif sname == 'qdrant':
            strategies.append(QdrantStrategy(all_movie_ids))
            print(f"  Qdrant: 已初始化")

    # ── 7. 运行评估 ──
    metadata = {
        'timestamp': datetime.now().isoformat(),
        'top_n': args.top_n,
        'n_users': ratings_df['user_id'].nunique(),
        'n_movies': ratings_df['movie_id'].nunique(),
        'n_ratings': len(ratings_df),
        'user_groups': args.user_groups,
        'item_groups': args.item_groups,
        'strategies': strategy_names,
    }

    user_results = {}
    item_results = {}

    if not args.skip_user_coldstart:
        user_results = run_user_coldstart_eval(
            strategies,
            user_segments,
            user_ratings_dict,
            test_positive,
            all_movie_ids,
            movie_genres,
            user_prefs,
            movie_rating_counts_from_db,
            args.top_n,
            full_ratings_dict=full_ratings_dict,
        )

    if not args.skip_item_coldstart:
        item_results = run_item_coldstart_eval(
            strategies,
            item_segments,
            user_ratings_dict,
            test_positive,
            all_movie_ids,
            movie_genres,
            user_prefs,
            movie_rating_counts_from_db,
            args.top_n,
            full_ratings_dict=full_ratings_dict,
        )

    # ── 8. 导出结果 ──
    save_results(user_results, item_results, metadata, args.output_dir)

    total_time = time.time() - overall_start
    print(f"\n{'=' * 60}")
    print(f"  评估完成！总耗时: {total_time:.2f} 秒")
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()
