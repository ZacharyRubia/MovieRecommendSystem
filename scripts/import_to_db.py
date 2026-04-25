#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 scripts/tmp 目录下的CSV数据导入到MySQL数据库
按照 database/init.sql 的表结构进行导入
"""

import csv
import sys
import pymysql
from pymysql.constants import CLIENT

# 数据库连接配置（从backend配置中读取）
DB_CONFIG = {
    'host': '192.168.10.128',
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4',
    'client_flag': CLIENT.MULTI_STATEMENTS
}

INPUT_DIR = 'scripts/tmp'

def connect_db():
    """连接数据库"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        print("数据库连接成功")
        return conn
    except Exception as e:
        print(f"数据库连接失败: {e}")
        sys.exit(1)

def import_actors(conn):
    """导入演员数据"""
    cursor = conn.cursor()
    count = 0
    
    try:
        with open(f'{INPUT_DIR}/actor.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['name']
                avatar_url = row['avatar_url'] if row['avatar_url'] else None
                description = row['description'] if row['description'] else None
                
                # 使用INSERT IGNORE避免重复
                sql = """
                INSERT IGNORE INTO `actor` (`name`, `avatar_url`, `description`)
                VALUES (%s, %s, %s)
                """
                cursor.execute(sql, (name, avatar_url, description))
                count += 1
        
        conn.commit()
        print(f"导入演员完成: {count} 条记录")
        return count
    except Exception as e:
        conn.rollback()
        print(f"导入演员失败: {e}")
        return 0
    finally:
        cursor.close()

def import_directors(conn):
    """导入导演数据"""
    cursor = conn.cursor()
    count = 0
    
    try:
        with open(f'{INPUT_DIR}/director.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['name']
                avatar_url = row['avatar_url'] if row['avatar_url'] else None
                description = row['description'] if row['description'] else None
                
                sql = """
                INSERT IGNORE INTO `director` (`name`, `avatar_url`, `description`)
                VALUES (%s, %s, %s)
                """
                cursor.execute(sql, (name, avatar_url, description))
                count += 1
        
        conn.commit()
        print(f"导入导演完成: {count} 条记录")
        return count
    except Exception as e:
        conn.rollback()
        print(f"导入导演失败: {e}")
        return 0
    finally:
        cursor.close()

def import_tags(conn):
    """导入标签数据"""
    cursor = conn.cursor()
    count = 0
    
    try:
        with open(f'{INPUT_DIR}/tag.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['name']
                
                sql = """
                INSERT IGNORE INTO `tag` (`name`)
                VALUES (%s)
                """
                cursor.execute(sql, (name,))
                count += 1
        
        conn.commit()
        print(f"导入标签完成: {count} 条记录")
        return count
    except Exception as e:
        conn.rollback()
        print(f"导入标签失败: {e}")
        return 0
    finally:
        cursor.close()

def import_movies(conn):
    """导入电影数据"""
    cursor = conn.cursor()
    count = 0
    
    try:
        with open(f'{INPUT_DIR}/movie.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                movie_id = int(row['movieId'])
                title = row['title']
                release_year = int(row['release_year']) if row['release_year'] else None
                description = row['description'] if row['description'] else None
                cover_url = row['cover_url'] if row['cover_url'] else None
                
                sql = """
                INSERT IGNORE INTO `movie` (`id`, `title`, `description`, `cover_url`, `release_year`)
                VALUES (%s, %s, %s, %s, %s)
                """
                cursor.execute(sql, (movie_id, title, description, cover_url, release_year))
                count += 1
        
        conn.commit()
        print(f"导入电影完成: {count} 条记录")
        return count
    except Exception as e:
        conn.rollback()
        print(f"导入电影失败: {e}")
        return 0
    finally:
        cursor.close()

def import_movie_tags(conn):
    """导入电影-标签关联"""
    cursor = conn.cursor()
    count = 0
    
    # 先获取tag name到id的映射
    cursor.execute("SELECT id, name FROM tag")
    tag_map = {name: tag_id for tag_id, name in cursor.fetchall()}
    
    try:
        with open(f'{INPUT_DIR}/movie_tag.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                movie_id = int(row['movie_id']) if 'movie_id' in row else int(row['movieId'])
                tag_name = row['tag_name'] if 'tag_name' in row else row['tag_name']
                
                if tag_name in tag_map:
                    tag_id = tag_map[tag_name]
                    sql = """
                    INSERT IGNORE INTO `movie_tag` (`movie_id`, `tag_id`)
                    VALUES (%s, %s)
                    """
                    cursor.execute(sql, (movie_id, tag_id))
                    count += 1
        
        conn.commit()
        print(f"导入电影-标签关联完成: {count} 条记录")
        return count
    except Exception as e:
        conn.rollback()
        print(f"导入电影-标签关联失败: {e}")
        return 0
    finally:
        cursor.close()

def import_movie_directors(conn):
    """导入电影-导演关联"""
    cursor = conn.cursor()
    count = 0
    
    # 获取director name到id的映射
    cursor.execute("SELECT id, name FROM director")
    director_map = {name: director_id for director_id, name in cursor.fetchall()}
    
    try:
        with open(f'{INPUT_DIR}/movie_director.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                movie_id = int(row['movie_id'])
                director_name = row['director_name']
                
                if director_name in director_map:
                    director_id = director_map[director_name]
                    sql = """
                    INSERT IGNORE INTO `movie_director` (`movie_id`, `director_id`)
                    VALUES (%s, %s)
                    """
                    cursor.execute(sql, (movie_id, director_id))
                    count += 1
        
        conn.commit()
        print(f"导入电影-导演关联完成: {count} 条记录")
        return count
    except Exception as e:
        conn.rollback()
        print(f"导入电影-导演关联失败: {e}")
        return 0
    finally:
        cursor.close()

def import_movie_actors(conn):
    """导入电影-演员关联"""
    cursor = conn.cursor()
    count = 0
    
    # 获取actor name到id的映射
    cursor.execute("SELECT id, name FROM actor")
    actor_map = {name: actor_id for actor_id, name in cursor.fetchall()}
    
    try:
        with open(f'{INPUT_DIR}/movie_actor.csv', 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                movie_id = int(row['movie_id'])
                actor_name = row['actor_name']
                role = row.get('role', '')
                
                if actor_name in actor_map:
                    actor_id = actor_map[actor_name]
                    sql = """
                    INSERT IGNORE INTO `movie_actor` (`movie_id`, `actor_id`, `role`)
                    VALUES (%s, %s, %s)
                    """
                    cursor.execute(sql, (movie_id, actor_id, role))
                    count += 1
        
        conn.commit()
        print(f"导入电影-演员关联完成: {count} 条记录")
        return count
    except Exception as e:
        conn.rollback()
        print(f"导入电影-演员关联失败: {e}")
        return 0
    finally:
        cursor.close()

import sys
import os

def main():
    """主函数"""
    # 设置标准输出编码为UTF-8
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("Start importing CSV data to database...")
    print(f"DB Config: {DB_CONFIG['host']}/{DB_CONFIG['database']}")
    
    conn = connect_db()
    
    try:
        print("\n----- Start Import -----")
        # 按照依赖顺序导入
        # 1. 先导入基表
        actors_count = import_actors(conn)
        directors_count = import_directors(conn)
        tags_count = import_tags(conn)
        movies_count = import_movies(conn)
        
        # 2. 再导入关联表
        movie_tags_count = import_movie_tags(conn)
        movie_directors_count = import_movie_directors(conn)
        movie_actors_count = import_movie_actors(conn)
        
        print("\n===== Import Complete =====")
        print(f"""Import Statistics:
- Actors: {actors_count}
- Directors: {directors_count}  
- Tags: {tags_count}
- Movies: {movies_count}
- Movie-Tag relations: {movie_tags_count}
- Movie-Director relations: {movie_directors_count}
- Movie-Actor relations: {movie_actors_count}
""")
        
    finally:
        conn.close()
        print("Database connection closed")

if __name__ == '__main__':
    main()