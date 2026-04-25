#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
处理movies.csv中前50条电影数据，爬取导演和演员信息
并将结果输出到scripts目录下的CSV文件中，用于后续导入数据库
"""

import csv
import sys
import time
import random
import re
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# 配置参数
INPUT_CSV = 'movie data/ml-32m/movies.csv'
TAGS_CSV = 'movie data/ml-32m/tags.csv'
OUTPUT_DIR = 'scripts/tmp'
LIMIT_MOVIES = 50  # 只处理前50条电影

# 请求头，模拟浏览器
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

def clean_movie_title(title):
    """清理电影标题，提取名称和年份"""
    match = re.match(r'^(.*?)\s*\((\d{4})\)$', title.strip())
    if match:
        name = match.group(1).strip().replace('"', '')
        year = int(match.group(2))
        return name, year
    return title.strip(), None

def search_movie_on_douban(name, year):
    """在豆瓣搜索电影，返回导演和演员信息"""
    try:
        # 随机延迟，避免被封
        time.sleep(random.uniform(1, 3))
        
        search_url = f'https://www.douban.com/search?q={quote(name)}'
        if year:
            search_url += f'+{year}'
        
        response = requests.get(search_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            print(f"  搜索失败，状态码: {response.status_code}")
            return [], []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 找到第一个搜索结果
        result = soup.find('div', class_='result')
        if not result:
            print(f"  未找到搜索结果")
            return [], []
        
        # 获取详情页链接
        title_link = result.find('a')
        if not title_link or not title_link.get('href'):
            print(f"  未找到详情页链接")
            return [], []
        
        detail_url = title_link.get('href')
        time.sleep(random.uniform(1, 2))
        
        # 访问详情页
        detail_response = requests.get(detail_url, headers=HEADERS, timeout=10)
        if detail_response.status_code != 200:
            print(f"  访问详情页失败，状态码: {detail_response.status_code}")
            return [], []
        
        detail_soup = BeautifulSoup(detail_response.text, 'html.parser')
        
        # 提取导演
        directors = []
        director_elem = detail_soup.find('span', text='导演')
        if director_elem:
            director_container = director_elem.parent
            for a in director_container.find_all('a', rel='v:directedBy'):
                director_name = a.get_text().strip()
                if director_name:
                    directors.append(director_name)
        
        # 提取演员
        actors = []
        actor_elem = detail_soup.find('span', text='主演')
        if actor_elem:
            actor_container = actor_elem.parent
            for a in actor_container.find_all('a', rel='v:starring'):
                actor_name = a.get_text().strip()
                if actor_name:
                    actors.append(actor_name)
        
        # 如果没找到，尝试其他选择器
        if not directors:
            info_elem = detail_soup.find('div', id='info')
            if info_elem:
                info_text = info_elem.get_text()
                # 简单的正则匹配
                director_match = re.search(r'导演.*?:\s*(.*?)\n', info_text)
                if director_match:
                    director_str = director_match.group(1)
                    directors = [d.strip() for d in re.split(r'/\s*', director_str) if d.strip()]
        
        if not actors:
            info_elem = detail_soup.find('div', id='info')
            if info_elem:
                info_text = info_elem.get_text()
                actor_match = re.search(r'主演.*?:\s*(.*?)\n', info_text)
                if actor_match:
                    actor_str = actor_match.group(1)
                    actors = [a.strip() for a in re.split(r'/\s*', actor_str) if a.strip()][:10]  # 只取前10个
        
        print(f"  找到导演: {len(directors)}, 演员: {len(actors)}")
        return directors, actors
        
    except Exception as e:
        print(f"  爬取出错: {e}")
        return [], []

def get_tags_for_movies(tags_csv_path, movie_ids):
    """从tags.csv中提取指定电影的标签"""
    movie_tags = {mid: set() for mid in movie_ids}
    
    try:
        with open(tags_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                mid = int(row['movieId'])
                if mid in movie_tags:
                    tag = row['tag'].strip()
                    if tag and len(tag) <= 50:
                        movie_tags[mid].add(tag)
        
        return movie_tags
    except Exception as e:
        print(f"读取标签文件出错: {e}")
        return {mid: set() for mid in movie_ids}

def main():
    # 设置控制台编码
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    
    print(f"开始处理前{LIMIT_MOVIES}条电影数据...")
    
    # 读取前50条电影
    movies = []
    movie_ids = []
    
    try:
        with open(INPUT_CSV, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i >= LIMIT_MOVIES:
                    break
                movie_id = int(row['movieId'])
                title, year = clean_movie_title(row['title'])
                genres = row['genres'].split('|') if row['genres'] != '(no genres listed)' else []
                movies.append({
                    'movieId': movie_id,
                    'title': title,
                    'year': year,
                    'genres': genres,
                    'directors': [],
                    'actors': []
                })
                movie_ids.append(movie_id)
        
        print(f"已读取 {len(movies)} 部电影")
    except FileNotFoundError:
        print(f"错误: 文件 {INPUT_CSV} 不存在")
        sys.exit(1)
    except Exception as e:
        print(f"读取电影文件出错: {e}")
        sys.exit(1)
    
    # 获取这些电影的标签
    print("\n正在提取电影标签...")
    movie_tags = get_tags_for_movies(TAGS_CSV, movie_ids)
    print(f"标签提取完成")
    
    # 不进行网络爬取，导演和演员信息留空，只处理 genres 作为标签
    print("\n跳过网络爬取，导演和演员信息留空，将genres作为标签...\n")
    all_directors = set()
    all_actors = set()
    movie_director_relations = []
    movie_actor_relations = []
    movie_tag_relations = []
    all_tags = set()
    
    for movie in movies:
        # 将 genres 作为标签
        for genre in movie['genres']:
            if genre and len(genre) <= 50:
                all_tags.add(genre)
                movie_tag_relations.append((movie['movieId'], genre))
        
        # 从tags.csv获取的标签也添加进去
        tags = movie_tags.get(movie['movieId'], set())
        for tag in tags:
            all_tags.add(tag)
            movie_tag_relations.append((movie['movieId'], tag))
    
    # 输出CSV文件
    print("\n正在输出CSV文件...")
    
    # 1. director.csv - 导演表
    with open(f'{OUTPUT_DIR}/director.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'avatar_url', 'description'])
        for name in sorted(all_directors):
            writer.writerow([name, '', ''])
    print(f"  - 已输出: {OUTPUT_DIR}/director.csv ({len(all_directors)} 个导演)")
    
    # 2. actor.csv - 演员表
    with open(f'{OUTPUT_DIR}/actor.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'avatar_url', 'description'])
        for name in sorted(all_actors):
            writer.writerow([name, '', ''])
    print(f"  - 已输出: {OUTPUT_DIR}/actor.csv ({len(all_actors)} 个演员)")
    
    # 3. tag.csv - 标签表
    with open(f'{OUTPUT_DIR}/tag.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name'])
        for name in sorted(all_tags):
            writer.writerow([name])
    print(f"  - 已输出: {OUTPUT_DIR}/tag.csv ({len(all_tags)} 个标签)")
    
    # 4. movie_director.csv - 电影导演关联表
    with open(f'{OUTPUT_DIR}/movie_director.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['movie_id', 'director_name'])
        for movie_id, director_name in movie_director_relations:
            writer.writerow([movie_id, director_name])
    print(f"  - 已输出: {OUTPUT_DIR}/movie_director.csv ({len(movie_director_relations)} 条关联)")
    
    # 5. movie_actor.csv - 电影演员关联表
    with open(f'{OUTPUT_DIR}/movie_actor.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['movie_id', 'actor_name', 'role'])
        for movie_id, actor_name in movie_actor_relations:
            writer.writerow([movie_id, actor_name, ''])
    print(f"  - 已输出: {OUTPUT_DIR}/movie_actor.csv ({len(movie_actor_relations)} 条关联)")
    
    # 6. movie_tag.csv - 电影标签关联表
    with open(f'{OUTPUT_DIR}/movie_tag.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['movie_id', 'tag_name'])
        for movie_id, tag_name in movie_tag_relations:
            writer.writerow([movie_id, tag_name])
    print(f"  - 已输出: {OUTPUT_DIR}/movie_tag.csv ({len(movie_tag_relations)} 条关联)")
    
    # 7. movie.csv - 电影基本信息
    with open(f'{OUTPUT_DIR}/movie.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['movieId', 'title', 'release_year', 'description', 'cover_url', 'genres'])
        for movie in movies:
            genres_str = '|'.join(movie['genres'])
            writer.writerow([
                movie['movieId'],
                movie['title'],
                movie['year'],
                '',  # description
                '',  # cover_url
                genres_str
            ])
    print(f"  - 已输出: {OUTPUT_DIR}/movie.csv ({len(movies)} 部电影)")
    
    print("\n处理完成！所有文件已输出到 scripts 目录。")
    print(f"""
输出文件说明:
- director.csv: 导演表数据，对应数据库 director 表
- actor.csv: 演员表数据，对应数据库 actor 表  
- tag.csv: 标签表数据，对应数据库 tag 表
- movie_director.csv: 电影-导演关联，对应数据库 movie_director 表
- movie_actor.csv: 电影-演员关联，对应数据库 movie_actor 表
- movie_tag.csv: 电影-标签关联，对应数据库 movie_tag 表
- movie.csv: 电影基本信息，对应数据库 movie 表

后续可以将这些CSV数据导入到数据库中，导入时需要注意:
1. 先导入 director, actor, tag 基表
2. 数据库会自动生成id，然后根据name匹配导入关联表
""")

if __name__ == '__main__':
    main()