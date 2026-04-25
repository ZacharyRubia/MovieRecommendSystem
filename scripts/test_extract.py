#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import csv
import sys
import os

# 绝对路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOVIES_CSV = os.path.join(BASE_DIR, 'movie data', 'ml-32m', 'movies.csv')
OUTPUT_DIR = os.path.join(BASE_DIR, 'scripts', 'tmp')
OUTPUT_FILE = os.path.join(OUTPUT_DIR, 'movies_output.csv')

print(f"BASE_DIR: {BASE_DIR}")
print(f"MOVIES_CSV: {MOVIES_CSV}")
print(f"OUTPUT_FILE: {OUTPUT_FILE}")

# 检查文件是否存在
if os.path.exists(MOVIES_CSV):
    print(f"✓ movies.csv 存在, 大小: {os.path.getsize(MOVIES_CSV)} bytes")
else:
    print(f"✗ movies.csv 不存在")
    sys.exit(1)

# 读取前3条测试
print("\n读取前3条电影数据:")
with open(MOVIES_CSV, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        if i >= 3:
            break
        print(f"  {i+1}. movieId={row['movieId']}, title={row['title']}")

# 确保输出目录存在
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 写入测试CSV
print(f"\n写入测试CSV到: {OUTPUT_FILE}")
with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
    writer = csv.writer(f, delimiter=',')
    writer.writerow(['title', 'id', 'description', 'cover_url', 'video_url', 
                     'release_year', 'duration', 'avg_rating', 
                     'vector_synced_at', 'updated_at', 'created_at'])
    writer.writerow(['Toy Story', 1, 'Test description', '', '', 1995, '', 3.9, '', '2026-04-25', '2026-04-25'])

print("✓ 测试完成!")
if os.path.exists(OUTPUT_FILE):
    print(f"✓ 输出文件已创建, 大小: {os.path.getsize(OUTPUT_FILE)} bytes")