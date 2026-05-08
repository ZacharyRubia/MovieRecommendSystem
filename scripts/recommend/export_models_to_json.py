#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_models_to_json.py - 将训练好的 pickle 模型导出为 JSON 格式

Node.js 无法直接读取 pickle 文件，因此需要先将模型导出为 JSON，
供 Node.js 端的 AI 推荐服务加载使用。

导出内容：
  - SVD 模型: user2idx, movie2idx, idx2movie, user_features, movie_features, user_means
  - User-CF 模型: user_sim_matrix, user_ratings, user_mean_rating, all_movies
  - Item-CF 模型: user_movies, movie_sim_matrix, movie_ratings, movie_mean_rating

用法:
  python export_models_to_json.py [--model-dir ../../models] [--output-dir ../../backend/models]
"""

import os
import sys
import pickle
import json
import argparse
import numpy as np


def convert_numpy(obj):
    """递归地将 numpy 类型转换为 Python 原生类型"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {convert_numpy(k): convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy(item) for item in obj]
    elif isinstance(obj, tuple):
        return [convert_numpy(item) for item in obj]
    elif isinstance(obj, set):
        return list(obj)
    return obj


def export_svd_model(model, output_path):
    """导出 SVD 模型为 JSON"""
    print("[导出] SVD 模型...")
    
    # 提取需要的数据
    data = {
        'algorithm': model.get('algorithm', 'svd'),
        'train_size': model.get('train_size', None),
        'user2idx': {int(k): int(v) for k, v in model.get('user2idx', {}).items()},
        'movie2idx': {int(k): int(v) for k, v in model.get('movie2idx', {}).items()},
        'idx2movie': {int(k): int(v) for k, v in model.get('idx2movie', {}).items()},
        'user_features': convert_numpy(model.get('user_features')),
        'movie_features': convert_numpy(model.get('movie_features')),
        'user_means': convert_numpy(model.get('user_means')),
    }
    
    # 估算数据大小（MB）
    size_mb = len(json.dumps(data, ensure_ascii=False)) / (1024 * 1024)
    print(f"  数据大小: 约 {size_mb:.1f} MB")
    
    # 如果数据太大，分拆成多个文件
    if size_mb > 100:
        print("  数据较大，将分拆保存...")
        # 保存元数据
        meta = {k: v for k, v in data.items() if k not in ('user_features', 'movie_features')}
        output_meta = os.path.join(output_path, 'svd_meta.json')
        with open(output_meta, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)
        print(f"  已保存: {output_meta}")
        
        # 保存特征矩阵（分行保存以减少内存占用）
        uf_path = os.path.join(output_path, 'svd_user_features.json')
        with open(uf_path, 'w', encoding='utf-8') as f:
            json.dump(data['user_features'], f, ensure_ascii=False)
        print(f"  已保存: {uf_path}")
        
        mf_path = os.path.join(output_path, 'svd_movie_features.json')
        with open(mf_path, 'w', encoding='utf-8') as f:
            json.dump(data['movie_features'], f, ensure_ascii=False)
        print(f"  已保存: {mf_path}")
    else:
        output_file = os.path.join(output_path, 'svd_model.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"  已保存: {output_file}")
    
    print(f"  用户数: {len(data['user2idx'])}, 电影数: {len(data['movie2idx'])}")
    print(f"  user_features shape: ({len(data['user_features'])}, {len(data['user_features'][0]) if data['user_features'] else 0})")
    print(f"  movie_features shape: ({len(data['movie_features'])}, {len(data['movie_features'][0]) if data['movie_features'] else 0})")
    print()


def export_user_cf_model(model, output_path):
    """导出 User-CF 模型为 JSON"""
    print("[导出] User-CF 模型...")
    
    # 重建 user_sim_matrix: 将 int 键转换为字符串（JSON 不支持 int 键）
    user_sim = model.get('user_sim_matrix', {})
    user_sim_str = {}
    for k, v in user_sim.items():
        user_sim_str[str(k)] = {str(kk): float(vv) for kk, vv in v.items()}
    
    user_ratings = model.get('user_ratings', {})
    user_ratings_str = {}
    for k, v in user_ratings.items():
        user_ratings_str[str(k)] = {str(kk): float(vv) for kk, vv in v.items()}
    
    user_mean = model.get('user_mean_rating', {})
    user_mean_str = {str(k): float(v) for k, v in user_mean.items()}
    
    data = {
        'algorithm': model.get('algorithm', 'user_cf'),
        'train_size': model.get('train_size', None),
        'n_neighbors': model.get('n_neighbors', 30),
        'user_sim_matrix': user_sim_str,
        'user_ratings': user_ratings_str,
        'user_mean_rating': user_mean_str,
        'all_movies': [int(m) for m in model.get('all_movies', [])],
    }
    
    size_mb = len(json.dumps(data, ensure_ascii=False)) / (1024 * 1024)
    print(f"  数据大小: 约 {size_mb:.1f} MB")
    
    output_file = os.path.join(output_path, 'user_cf_model.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  已保存: {output_file}")
    print(f"  用户数: {len(user_mean)}, 电影数: {len(data['all_movies'])}")
    print()


def export_item_cf_model(model, output_path):
    """导出 Item-CF 模型为 JSON"""
    print("[导出] Item-CF 模型...")
    
    user_movies = model.get('user_movies', {})
    user_movies_str = {}
    for k, v in user_movies.items():
        user_movies_str[str(k)] = [int(x) for x in v]
    
    movie_sim = model.get('movie_sim_matrix', {})
    movie_sim_str = {}
    for k, v in movie_sim.items():
        movie_sim_str[str(k)] = {str(kk): float(vv) for kk, vv in v.items()}
    
    movie_ratings = model.get('movie_ratings', {})
    movie_ratings_str = {}
    for k, v in movie_ratings.items():
        movie_ratings_str[str(k)] = {}
        for kk, vv in v.items():
            movie_ratings_str[str(k)][str(kk)] = float(vv)
    
    movie_mean = model.get('movie_mean_rating', {})
    movie_mean_str = {str(k): float(v) for k, v in movie_mean.items()}
    
    data = {
        'algorithm': model.get('algorithm', 'item_cf'),
        'train_size': model.get('train_size', None),
        'n_neighbors': model.get('n_neighbors', 30),
        'user_movies': user_movies_str,
        'movie_sim_matrix': movie_sim_str,
        'movie_ratings': movie_ratings_str,
        'movie_mean_rating': movie_mean_str,
    }
    
    size_mb = len(json.dumps(data, ensure_ascii=False)) / (1024 * 1024)
    print(f"  数据大小: 约 {size_mb:.1f} MB")
    
    output_file = os.path.join(output_path, 'item_cf_model.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  已保存: {output_file}")
    print()


def main():
    parser = argparse.ArgumentParser(description='Export pickle models to JSON')
    parser.add_argument('--model-dir', '-m', default=None,
                        help='Model directory (default: scripts/models)')
    parser.add_argument('--output-dir', '-o', default=None,
                        help='Output directory (default: backend/models)')
    args = parser.parse_args()
    
    # 路径配置
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)  # scripts/
    base_dir = os.path.dirname(project_dir)    # project root
    
    model_dir = args.model_dir or os.path.join(base_dir, 'models')
    output_dir = args.output_dir or os.path.join(base_dir, 'backend', 'models')
    
    print(f"{'=' * 60}")
    print(f"  模型导出工具 - Pickle → JSON")
    print(f"{'=' * 60}")
    print(f"  模型目录: {model_dir}")
    print(f"  输出目录: {output_dir}")
    print(f"{'=' * 60}\n")
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 导出 SVD 模型
    svd_path = os.path.join(model_dir, 'svd_model.pkl')
    if os.path.exists(svd_path):
        with open(svd_path, 'rb') as f:
            model_svd = pickle.load(f)
        export_svd_model(model_svd, output_dir)
    else:
        print(f"[跳过] SVD 模型不存在: {svd_path}\n")
    
    # 导出 User-CF 模型
    ucf_path = os.path.join(model_dir, 'user_cf_model.pkl')
    if os.path.exists(ucf_path):
        with open(ucf_path, 'rb') as f:
            model_ucf = pickle.load(f)
        export_user_cf_model(model_ucf, output_dir)
    else:
        print(f"[跳过] User-CF 模型不存在: {ucf_path}\n")
    
    # 导出 Item-CF 模型
    icf_path = os.path.join(model_dir, 'item_cf_model.pkl')
    if os.path.exists(icf_path):
        with open(icf_path, 'rb') as f:
            model_icf = pickle.load(f)
        export_item_cf_model(model_icf, output_dir)
    else:
        print(f"[跳过] Item-CF 模型不存在: {icf_path}\n")
    
    print(f"{'=' * 60}")
    print(f"  导出完成！输出目录: {output_dir}")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()