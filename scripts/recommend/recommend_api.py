#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recommend_api.py - 推荐引擎 REST API 服务

加载训练好的模型（SVD / User-CF / Item-CF），
通过 Flask HTTP API 为 Node.js 后端提供推荐服务。

支持缓存优先策略：
  1. 查询 MySQL users_recommendations 表（快速返回）
  2. 无缓存或过期 → 实时计算
  3. 实时计算结果异步写入缓存

启动:
  python recommend_api.py [--port 5100]
"""

import os
import sys
import pickle
import json
import math
import argparse
import numpy as np
from collections import defaultdict
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

# ---------- MySQL 缓存表支持 ----------
try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False
    print("[警告] pymysql 未安装，缓存表查询功能不可用")
    print("  安装: pip install pymysql")

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, 'models')

# MySQL 连接参数
CACHE_DB_HOST = os.environ.get('CACHE_DB_HOST', '192.168.1.38')
CACHE_DB_USER = os.environ.get('CACHE_DB_USER', 'newuser')
CACHE_DB_PASS = os.environ.get('CACHE_DB_PASS', 'yourpassword')
CACHE_DB_NAME = os.environ.get('CACHE_DB_NAME', 'MovieRecommendSystem')
CACHE_DB_PORT = int(os.environ.get('CACHE_DB_PORT', '3306'))
CACHE_TTL_SECONDS = 60 * 60  # 缓存有效期 1 小时

app = Flask(__name__)
CORS(app)

# 全局模型缓存
_models = {}
_movie_dict = {}


# ============================================================
# MySQL 连接与缓存操作
# ============================================================

def get_db():
    """获取 MySQL 数据库连接"""
    if not HAS_PYMYSQL:
        return None
    try:
        conn = pymysql.connect(
            host=CACHE_DB_HOST,
            user=CACHE_DB_USER,
            password=CACHE_DB_PASS,
            database=CACHE_DB_NAME,
            port=CACHE_DB_PORT,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=5
        )
        return conn
    except Exception as e:
        print(f"[缓存] MySQL 连接失败: {e}")
        return None


def get_cached_recommendation(user_id, algorithm='hybrid'):
    """
    从 users_recommendations 表查询缓存的推荐结果
    返回: (recommendations_list, algorithm) 或 None
    """
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT recommend_movies, algorithm, updated_at "
                "FROM users_recommendations "
                "WHERE user_id = %s AND algorithm = %s "
                "ORDER BY updated_at DESC LIMIT 1",
                (user_id, algorithm)
            )
            row = cursor.fetchone()
            if not row:
                return None

            # 检查缓存是否过期
            updated = row['updated_at']
            if isinstance(updated, datetime):
                age_seconds = (datetime.now() - updated).total_seconds()
            else:
                age_seconds = CACHE_TTL_SECONDS + 1  # 视为过期

            if age_seconds > CACHE_TTL_SECONDS:
                print(f"[缓存] 用户 {user_id} 缓存已过期 ({age_seconds:.0f}s > {CACHE_TTL_SECONDS}s)")
                return None

            # 解析 JSON
            try:
                items = json.loads(row['recommend_movies'])
            except (json.JSONDecodeError, TypeError):
                return None

            print(f"[缓存] 命中用户 {user_id}, 算法: {row['algorithm']}, 条目数: {len(items)}")
            return items, row['algorithm']

    except Exception as e:
        print(f"[缓存] 查询失败: {e}")
        return None
    finally:
        conn.close()


def save_result_to_cache(user_id, recommendations, algorithm='hybrid'):
    """
    将实时计算结果写回 users_recommendations 缓存表
    """
    conn = get_db()
    if not conn:
        return
    try:
        # 转换为 JSON
        items = [
            {'movie_id': int(mid), 'score': round(float(score), 4)}
            for mid, score in recommendations
        ]
        recommend_json = json.dumps(items, ensure_ascii=False)

        with conn.cursor() as cursor:
            # 使用 INSERT ... ON DUPLICATE KEY UPDATE
            sql = (
                "INSERT INTO users_recommendations "
                "(user_id, algorithm, recommend_movies, updated_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON DUPLICATE KEY UPDATE "
                "recommend_movies = VALUES(recommend_movies), "
                "updated_at = NOW()"
            )
            cursor.execute(sql, (user_id, algorithm, recommend_json))
        conn.commit()
        print(f"[缓存] 已写回用户 {user_id}, 算法: {algorithm}, 条目: {len(items)}")
    except Exception as e:
        print(f"[缓存] 写回失败: {e}")
    finally:
        conn.close()


# ============================================================
# 模型加载（延迟加载，按需加载）
# ============================================================

def load_model(algorithm='svd'):
    """加载训练好的模型（带缓存）"""
    if algorithm in _models:
        return _models[algorithm]

    model_map = {
        'svd': 'svd_model.pkl',
        'user_cf': 'user_cf_model.pkl',
        'item_cf': 'item_cf_model.pkl',
    }

    filename = model_map.get(algorithm)
    if not filename:
        raise ValueError(f"未知算法: {algorithm}")

    filepath = os.path.join(MODEL_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"模型文件不存在: {filepath}")

    print(f"[加载模型] {algorithm}: {filepath}")
    with open(filepath, 'rb') as f:
        model = pickle.load(f)

    # 恢复 User-CF 中的键为 int
    if model.get('algorithm') in ('user_cf',):
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

    # 恢复 Item-CF 中的键为 int
    if model.get('algorithm') in ('item_cf',):
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

    _models[algorithm] = model
    print(f"  算法: {model['algorithm']}, "
          f"训练集大小: {model.get('train_size', 'N/A')}")
    return model


# ============================================================
# 推荐算法实现（复用 recommend.py 中的逻辑）
# ============================================================

def recommend_svd(model, user_id, top_n=10):
    """使用 SVD 模型推荐"""
    user2idx = model['user2idx']
    movie2idx = model['movie2idx']
    user_features = model['user_features']
    movie_features = model['movie_features']
    user_means = model['user_means']

    if user_id not in user2idx:
        return []

    u_idx = user2idx[user_id]
    user_mean = user_means[u_idx]

    predictions = []
    for mid, m_idx in movie2idx.items():
        pred = np.dot(user_features[u_idx], movie_features[m_idx]) + user_mean
        predictions.append((mid, float(pred)))

    predictions.sort(key=lambda x: -x[1])
    return predictions[:top_n]


def recommend_user_cf(model, user_id, top_n=10):
    """使用 User-Based CF 推荐"""
    user_ratings = model['user_ratings']
    user_sim_matrix = model['user_sim_matrix']
    user_mean_rating = model['user_mean_rating']
    all_movies = model['all_movies']
    n_neighbors = model.get('n_neighbors', 30)

    if user_id not in user_ratings:
        return []

    rated_movies = set(user_ratings[user_id].keys())
    sim_users = user_sim_matrix.get(user_id, {})
    if not sim_users:
        return []

    neighbors = []
    for nuid, sim in sim_users.items():
        if nuid in user_ratings:
            neighbors.append((nuid, sim))

    neighbors.sort(key=lambda x: -x[1])
    neighbors = neighbors[:n_neighbors]

    if not neighbors:
        return []

    uid_mean = user_mean_rating.get(user_id, 3.5)
    predictions = []

    for mid in all_movies:
        if mid in rated_movies:
            continue

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

    if user_id not in user_movies:
        return []

    user_rated = set(user_movies[user_id])
    all_movies_set = set(movie_ratings.keys())
    candidate_movies = all_movies_set - user_rated

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
    """混合推荐: 融合 SVD + User-CF + Item-CF"""
    if weights is None:
        weights = {'svd': 0.4, 'user_cf': 0.3, 'item_cf': 0.3}

    n_candidates = top_n * 3
    svd_results = []
    user_cf_results = []
    item_cf_results = []

    try:
        svd_results = recommend_svd(model_svd, user_id, n_candidates)
    except Exception as e:
        print(f"  SVD 推荐失败: {e}")

    try:
        user_cf_results = recommend_user_cf(model_user_cf, user_id, n_candidates)
    except Exception as e:
        print(f"  User-CF 推荐失败: {e}")

    try:
        item_cf_results = recommend_item_cf(model_item_cf, user_id, n_candidates)
    except Exception as e:
        print(f"  Item-CF 推荐失败: {e}")

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

    final_scores = []
    for mid in score_map:
        if weight_sum_map[mid] > 0:
            final_scores.append((mid, score_map[mid] / weight_sum_map[mid]))

    final_scores.sort(key=lambda x: -x[1])
    return final_scores[:top_n]


# ============================================================
# 元数据加载
# ============================================================

def load_metadata():
    """加载模型元数据"""
    filepath = os.path.join(MODEL_DIR, 'metadata.json')
    if not os.path.exists(filepath):
        return {}
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================
# Flask API 端点
# ============================================================

@app.route('/api/recommend/health', methods=['GET'])
def health_check():
    """健康检查"""
    try:
        meta = load_metadata()
        return jsonify({
            'success': True,
            'message': 'recommend_api 服务运行中',
            'data': {
                'models': [m['algorithm'] for m in meta.get('models', [])],
                'n_users': meta.get('dataset', {}).get('n_users', 0),
                'n_movies': meta.get('dataset', {}).get('n_movies', 0)
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/recommend/ai', methods=['GET'])
def ai_recommend():
    """
    AI 推荐接口（使用训练好的模型，缓存优先）

    策略：
      1. 查询 MySQL users_recommendations 缓存表
      2. 缓存命中且未过期 → 直接返回缓存结果
      3. 缓存未命中/过期 → 实时计算，并异步写回缓存

    参数:
      user_id (int): 用户ID (必填)
      algorithm (str): 算法类型 - svd | user_cf | item_cf | hybrid (默认 hybrid)
      top_n (int): 推荐数量 (默认 10)
      skip_cache (bool): 是否跳过缓存直接实时计算 (默认 false)
    """
    try:
        user_id = request.args.get('user_id', type=int)
        algorithm = request.args.get('algorithm', 'hybrid')
        top_n = request.args.get('top_n', 10, type=int)
        skip_cache = request.args.get('skip_cache', 'false').lower() == 'true'

        if not user_id:
            return jsonify({'success': False, 'message': '缺少 user_id 参数'}), 400

        if top_n < 1 or top_n > 100:
            top_n = 10

        # -------- 第 1 步：尝试从缓存读取 --------
        from_cache = None
        if not skip_cache:
            from_cache = get_cached_recommendation(user_id, algorithm)

        if from_cache is not None:
            cached_items, cached_algo = from_cache
            return jsonify({
                'success': True,
                'data': {
                    'userId': user_id,
                    'algorithm': cached_algo,
                    'topN': min(top_n, len(cached_items)),
                    'elapsed': 0.001,  # 缓存几乎无耗时
                    'total': len(cached_items),
                    'recommendations': [
                        {'movieId': int(item['movie_id']),
                         'predictedRating': round(float(item['score']), 4)}
                        for item in cached_items[:top_n]
                    ],
                    'fromCache': True
                }
            })

        # -------- 第 2 步：缓存未命中，实时计算 --------
        start_time = __import__('time').time()

        if algorithm == 'hybrid':
            model_svd = load_model('svd')
            model_user_cf = load_model('user_cf')
            model_item_cf = load_model('item_cf')
            results = recommend_hybrid(model_svd, model_user_cf, model_item_cf,
                                       user_id, top_n)
        elif algorithm == 'svd':
            model_svd = load_model('svd')
            results = recommend_svd(model_svd, user_id, top_n)
        elif algorithm == 'user_cf':
            model_user_cf = load_model('user_cf')
            results = recommend_user_cf(model_user_cf, user_id, top_n)
        elif algorithm == 'item_cf':
            model_item_cf = load_model('item_cf')
            results = recommend_item_cf(model_item_cf, user_id, top_n)
        else:
            return jsonify({'success': False, 'message': f'未知算法: {algorithm}'}), 400

        elapsed = __import__('time').time() - start_time

        # 转换结果为可序列化格式（确保 numpy 类型转换为 Python 原生类型）
        recommendations = [
            {'movieId': int(mid), 'predictedRating': round(float(score), 4)}
            for mid, score in results
        ]

        # -------- 第 3 步：异步写回缓存 --------
        # 仅在条目数达到阈值时写回，避免缓存无用的空结果
        if len(results) >= top_n // 2:
            save_result_to_cache(user_id, results, algorithm)

        return jsonify({
            'success': True,
            'data': {
                'userId': user_id,
                'algorithm': algorithm,
                'topN': top_n,
                'elapsed': round(elapsed, 3),
                'total': len(recommendations),
                'recommendations': recommendations,
                'fromCache': False
            }
        })

    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({'success': False, 'message': str(e)}), 503
    except Exception as e:
        return jsonify({'success': False, 'message': f'推荐失败: {str(e)}'}), 500


@app.route('/api/recommend/models', methods=['GET'])
def list_models():
    """列出可用模型"""
    try:
        meta = load_metadata()
        models_info = []
        for m in meta.get('models', []):
            models_info.append({
                'name': m['name'],
                'algorithm': m['algorithm'],
                'test_rmse': m.get('test_rmse', 0),
                'n_factors': m.get('n_factors', None),
                'n_neighbors': m.get('n_neighbors', None),
                'train_time_sec': round(m.get('train_time', 0), 2)
            })
        return jsonify({
            'success': True,
            'data': {
                'models': models_info,
                'dataset': meta.get('dataset', {})
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='推荐引擎 API 服务')
    parser.add_argument('--port', '-p', type=int, default=5100,
                        help='监听端口 (默认: 5100)')
    parser.add_argument('--host', default='0.0.0.0',
                        help='监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--debug', action='store_true',
                        help='调试模式')
    args = parser.parse_args()

    print(f"\n{'=' * 60}")
    print("  推荐引擎 API 服务 (缓存优先)")
    print(f"{'=' * 60}")
    print(f"  端口: {args.port}")
    print(f"  地址: {args.host}")
    print(f"  模型目录: {MODEL_DIR}")
    print(f"  缓存DB: {CACHE_DB_HOST}:{CACHE_DB_PORT}/{CACHE_DB_NAME}")
    print(f"  pymysql: {'可用' if HAS_PYMYSQL else '未安装(缓存不可用)'}")
    print(f"{'=' * 60}\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()