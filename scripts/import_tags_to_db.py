#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导入ml-32m数据集中的tags到MovieRecommendSystem数据库的tag表中
去重后插入所有唯一标签
"""

import csv
import sys
import pymysql
from tqdm import tqdm

# 数据库配置 - 根据实际情况修改
DB_CONFIG = {
    'host': '192.168.10.128',
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4',
    'port': 3306
}

import os
def main():
    # 设置控制台编码
    import sys
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    
    # CSV文件路径
    csv_path = 'movie data/ml-32m/tags.csv'
    
    print(f"正在读取文件: {csv_path}")
    
    # 提取所有唯一标签
    unique_tags = set()
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # 统计总行数用于进度条
            total_rows = sum(1 for _ in reader)
            f.seek(0)
            next(reader)  # 跳过表头
            
            for row in tqdm(reader, total=total_rows, desc='提取标签'):
                tag = row['tag'].strip()
                if tag and len(tag) <= 50:  # 限制长度不超过数据库定义的50字符
                    unique_tags.add(tag)
                    
    except FileNotFoundError:
        print(f"错误: 文件 {csv_path} 不存在")
        sys.exit(1)
    except Exception as e:
        print(f"读取文件出错: {e}")
        sys.exit(1)
    
    print(f"\n提取完成，共找到 {len(unique_tags)} 个唯一标签")
    
    # 连接数据库
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("数据库连接成功")
    except Exception as e:
        print(f"数据库连接失败: {e}")
        sys.exit(1)
    
    # 插入标签
    inserted = 0
    skipped = 0
    
    print("\n开始插入数据库...")
    for tag in tqdm(unique_tags, desc='插入标签'):
        try:
            # 使用INSERT IGNORE跳过重复标签
            sql = "INSERT IGNORE INTO tag (name) VALUES (%s)"
            cursor.execute(sql, (tag,))
            if cursor.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"插入标签 '{tag}' 失败: {e}")
            skipped += 1
    
    # 提交事务
    conn.commit()
    
    # 获取总标签数
    cursor.execute("SELECT COUNT(*) FROM tag")
    total_tags = cursor.fetchone()[0]
    
    print(f"\n导入完成:")
    print(f"  - 本次新增: {inserted}")
    print(f"  - 本次跳过(已存在): {skipped}")
    print(f"  - 数据库中总标签数: {total_tags}")
    
    # 关闭连接
    cursor.close()
    conn.close()

if __name__ == '__main__':
    main()