#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取movies.csv中前50部电影，按照指定格式输出
字段格式: title	id	description	cover_url	video_url	release_year	duration	avg_rating	vector_synced_at	updated_at	created_at
"""

import csv
import sys
import os
import re
from datetime import datetime
from collections import defaultdict

# 配置参数
MOVIES_CSV = 'movie data/ml-32m/movies.csv'
LINKS_CSV = 'movie data/ml-32m/links.csv'
RATINGS_CSV = 'movie data/ml-32m/ratings.csv'
TAGS_CSV = 'movie data/ml-32m/tags.csv'
OUTPUT_DIR = 'scripts/tmp'
LIMIT_MOVIES = 50

def ensure_output_dir():
    """确保输出目录存在"""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

def clean_movie_title(title):
    """清理电影标题，提取名称和年份"""
    match = re.match(r'^(.*?)\s*\((\d{4})\)$', title.strip())
    if match:
        name = match.group(1).strip()
        year = int(match.group(2))
        return name, year
    return title.strip(), None

def load_links():
    """加载links数据"""
    links = {}
    with open(LINKS_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            movie_id = int(row['movieId'])
            links[movie_id] = {
                'imdbId': row.get('imdbId', ''),
                'tmdbId': row.get('tmdbId', '')
            }
    return links

def load_ratings_for_movies(movie_ids):
    """加载指定电影的ratings数据，计算平均评分"""
    movie_ids_set = set(movie_ids)
    movie_ratings = {mid: [] for mid in movie_ids}
    
    with open(RATINGS_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            movie_id = int(row['movieId'])
            if movie_id in movie_ids_set:
                rating = float(row['rating'])
                movie_ratings[movie_id].append(rating)
    
    # 计算平均分
    avg_ratings = {}
    for movie_id, ratings in movie_ratings.items():
        if ratings:
            avg_ratings[movie_id] = sum(ratings) / len(ratings)
    return avg_ratings

def load_tags_for_movies(movie_ids):
    """加载指定电影的tags数据，用于description"""
    movie_ids_set = set(movie_ids)
    movie_tags = defaultdict(list)
    
    with open(TAGS_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            movie_id = int(row['movieId'])
            if movie_id in movie_ids_set:
                tag = row['tag'].strip()
                if tag and len(tag) <= 100:
                    movie_tags[movie_id].append(tag)
    return movie_tags

def main():
    # 设置控制台编码
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    
    ensure_output_dir()
    current_time = datetime.now().isoformat()
    
    print(f"开始处理前{LIMIT_MOVIES}条电影数据...")
    
    # 先读取前50条电影，获取movieId
    movies = []
    movie_ids = []
    with open(MOVIES_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= LIMIT_MOVIES:
                break
            
            movie_id = int(row['movieId'])
            movie_ids.append(movie_id)
            title, release_year = clean_movie_title(row['title'])
            genres = row['genres']
            movies.append({
                'movieId': movie_id,
                'title': title,
                'release_year': release_year,
                'genres': genres
            })
    
    print(f"已读取前{LIMIT_MOVIES}部电影")
    
    # 只加载这些电影的辅助数据
    print("正在加载links数据...")
    links = load_links()
    print("正在加载ratings数据（只加载前50部电影）...")
    avg_ratings = load_ratings_for_movies(movie_ids)
    print("正在加载tags数据...")
    movie_tags = load_tags_for_movies(movie_ids)
    
    # 处理每部电影
    output_movies = []
    for movie in movies:
        movie_id = movie['movieId']
        title = movie['title']
        release_year = movie['release_year']
        genres = movie['genres']
        
        # 获取辅助数据
        link_data = links.get(movie_id, {})
        avg_rating = avg_ratings.get(movie_id, None)
        tags = movie_tags.get(movie_id, [])
        
        # description: 使用genres + tags
        description_parts = []
        if genres and genres != '(no genres listed)':
            description_parts.append(f"Genres: {genres.replace('|', ', ')}")
        if tags:
            description_parts.append(f"Tags: {', '.join(tags[:5])}")
        description = '; '.join(description_parts) if description_parts else ''
        
        # cover_url: 可以用tmdb的图片链接（如果有tmdbId）
        cover_url = ''
        tmdb_id = link_data.get('tmdbId', '')
        if tmdb_id:
            # TMDB图片URL格式，需要API key才能使用，这里留空或使用占位符
            # cover_url = f"https://image.tmdb.org/t/p/w500/..."
            cover_url = ''  # 设置为null
        
        # 格式化输出
        output_movies.append({
            'title': title,
            'id': movie_id,
            'description': description if description else 'NULL',
            'cover_url': cover_url if cover_url else 'NULL',
            'video_url': 'NULL',
            'release_year': release_year if release_year else 'NULL',
            'duration': 'NULL',
            'avg_rating': round(avg_rating, 2) if avg_rating else 'NULL',
            'vector_synced_at': 'NULL',
            'updated_at': current_time,
            'created_at': current_time
        })
    
    # 输出到CSV
    output_file = os.path.join(OUTPUT_DIR, 'movies_output.csv')
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=',')
        # 写入表头
        writer.writerow(['title', 'id', 'description', 'cover_url', 'video_url', 
                         'release_year', 'duration', 'avg_rating', 
                         'vector_synced_at', 'updated_at', 'created_at'])
        # 写入数据
        for movie in output_movies:
            writer.writerow([
                movie['title'],
                movie['id'],
                movie['description'],
                movie['cover_url'],
                movie['video_url'],
                movie['release_year'],
                movie['duration'],
                movie['avg_rating'],
                movie['vector_synced_at'],
                movie['updated_at'],
                movie['created_at']
            ])
    
    print(f"\n处理完成！")
    print(f"已提取 {len(output_movies)} 部电影数据")
    print(f"输出文件: {output_file}")
    print(f"\n字段说明:")
    print(f"  - title: 电影标题")
    print(f"  - id: movieId")
    print(f"  - description: Genres和Tags组合")
    print(f"  - cover_url: 封面URL（暂无）")
    print(f"  - video_url: 视频URL（暂无）")
    print(f"  - release_year: 上映年份")
    print(f"  - duration: 时长（暂无）")
    print(f"  - avg_rating: 平均评分（从ratings.csv计算）")
    print(f"  - vector_synced_at: 向量同步时间（暂无）")
    print(f"  - updated_at: 更新时间")
    print(f"  - created_at: 创建时间")

if __name__ == '__main__':
    main()
