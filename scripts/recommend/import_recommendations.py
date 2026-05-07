#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_recommendations.py - 推荐结果 MySQL 导入脚本

将 export_recommendations.py 导出的 CSV 文件导入 MySQL。
支持两种导入方式：
  1. LOAD DATA INFILE（推荐，性能最高，需 local_infile 权限）
  2. 逐行 INSERT（备选，免配权限）

用法:
  python import_recommendations.py                          # 导入当前 export/ 下所有 CSV
  python import_recommendations.py --user users_recommendations.csv   # 指定用户推荐 CSV
  python import_recommendations.py --movie movies_similarities.csv    # 指定电影相似度 CSV
  python import_recommendations.py --sql                    # 使用 INSERT 方式（备选）
  python import_recommendations.py --host localhost --user root --password yourpass --db MovieRecommendSystem

LOAD DATA 方式导入前需在 MySQL 中执行:
  SET GLOBAL local_infile = 1;
"""

import os
import sys
import csv
import json
import argparse
import subprocess
from datetime import datetime

# ---------- 路径配置 ----------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXPORT_DIR = os.path.join(BASE_DIR, 'export')

# 默认 MySQL 连接参数（可从环境变量读取）
DB_HOST = os.environ.get('DB_HOST', '192.168.1.38')
DB_USER = os.environ.get('DB_USER', 'newuser')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'yourpassword')
DB_NAME = os.environ.get('DB_NAME', 'MovieRecommendSystem')
DB_PORT = os.environ.get('DB_PORT', '3306')


def find_csv_files(export_dir):
    """自动查找 export/ 下的 CSV 文件"""
    user_csv = None
    movie_csv = None

    for f in os.listdir(export_dir):
        if not f.endswith('.csv'):
            continue
        if 'users_recommendations' in f:
            user_csv = os.path.join(export_dir, f)
        elif 'movies_similarities' in f:
            movie_csv = os.path.join(export_dir, f)

    return user_csv, movie_csv


def import_load_data(csv_path, table_name, columns, host, user, password, db, port):
    """
    使用 LOAD DATA LOCAL INFILE 导入 MySQL（性能最优）
    """
    print(f"\n[LOAD DATA] 开始导入 {table_name} ...")
    print(f"  文件: {csv_path}")
    print(f"  目标表: {table_name}")

    if not os.path.exists(csv_path):
        print(f"  [跳过] 文件不存在: {csv_path}")
        return False

    # 构建 LOAD DATA 命令
    load_cmd = (
        f'LOAD DATA LOCAL INFILE \'{csv_path.replace("\\\\", "/")}\' '
        f'REPLACE INTO TABLE `{table_name}` '
        f'FIELDS TERMINATED BY \',\' '
        f'ENCLOSED BY \'\\"\' '
        f'LINES TERMINATED BY \'\\n\' '
        f'({columns});'
    )

    # 构建 mysql 客户端命令
    mysql_cmd = [
        'mysql',
        '-h', host,
        '-P', port,
        '-u', user,
        f'-p{password}',
        '--local-infile=1',
        db,
        '-e', load_cmd
    ]

    print(f"  执行 MySQL 命令...")
    try:
        result = subprocess.run(
            mysql_cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 分钟超时
        )
        if result.returncode == 0:
            print(f"  ✅ {table_name} 导入成功")
            if result.stdout:
                print(f"  输出: {result.stdout.strip()}")
            return True
        else:
            print(f"  ❌ 导入失败 (returncode={result.returncode})")
            if result.stderr:
                print(f"  错误: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ❌ 导入超时（超过 300 秒）")
        return False
    except FileNotFoundError:
        print(f"  ❌ 未找到 mysql 客户端，请确保 mysql 命令在 PATH 中")
        return False
    except Exception as e:
        print(f"  ❌ 导入异常: {e}")
        return False


def import_insert_rows(csv_path, table_name, id_field, json_field, host, user, password, db, port):
    """
    逐行 INSERT 方式导入（备选，免配 local_infile 权限）
    按批量提交以提升性能
    """
    print(f"\n[INSERT] 开始导入 {table_name} ...")
    print(f"  文件: {csv_path}")
    print(f"  目标表: {table_name}")

    if not os.path.exists(csv_path):
        print(f"  [跳过] 文件不存在: {csv_path}")
        return False

    # 读取 CSV
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    total = len(rows)
    print(f"  总行数: {total}")

    batch_size = 500
    success = 0
    errors = 0

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]

        # 构建批量 INSERT ... ON DUPLICATE KEY UPDATE 语句
        if table_name == 'users_recommendations':
            # user_id, recommend_movies(JSON), algorithm, updated_at
            values_list = []
            for row in batch:
                uid = row[0]
                json_data = row[1].replace("'", "\\'")
                algorithm = row[2] if len(row) > 2 else 'svd'
                updated_at = row[3] if len(row) > 3 else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                values_list.append(
                    f"({uid}, '{json_data}', '{algorithm}', '{updated_at}')"
                )

            insert_sql = (
                f"REPLACE INTO `{table_name}` "
                f"(`{id_field}`, `{json_field}`, `algorithm`, `updated_at`) VALUES\n" +
                ",\n".join(values_list) + ";"
            )
        else:
            # movies_similarities: movie_id, similar_movies(JSON), updated_at
            values_list = []
            for row in batch:
                mid = row[0]
                json_data = row[1].replace("'", "\\'")
                updated_at = row[2] if len(row) > 2 else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                values_list.append(
                    f"({mid}, '{json_data}', '{updated_at}')"
                )

            insert_sql = (
                f"REPLACE INTO `{table_name}` "
                f"(`{id_field}`, `{json_field}`, `updated_at`) VALUES\n" +
                ",\n".join(values_list) + ";"
            )

        # 执行 SQL
        mysql_cmd = [
            'mysql',
            '-h', host,
            '-P', port,
            '-u', user,
            f'-p{password}',
            db,
            '-e', insert_sql
        ]

        try:
            result = subprocess.run(
                mysql_cmd,
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                success += len(batch)
            else:
                errors += len(batch)
                if errors <= 3:
                    print(f"  批次 {i // batch_size + 1} 失败: {result.stderr.strip()}")
        except Exception as e:
            errors += len(batch)
            if errors <= 3:
                print(f"  批次 {i // batch_size + 1} 异常: {e}")

        # 进度
        if (i + batch_size) % 5000 == 0 or (i + batch_size) >= total:
            print(f"  进度: {min(i + batch_size, total)}/{total} (成功: {success}, 错误: {errors})")

    print(f"\n  {'✅' if errors == 0 else '⚠️'} 导入完成: 成功 {success}/{total}{', 失败 ' + str(errors) if errors > 0 else ''}")
    return errors == 0


def print_config_info(host, user, db):
    """打印连接信息供用户确认"""
    print("=" * 60)
    print("  MySQL 导入配置")
    print("=" * 60)
    print(f"  主机: {host}")
    print(f"  用户: {user}")
    print(f"  数据库: {db}")
    print(f"  密码: {'已设置' if DB_PASSWORD != 'yourpassword' else '⚠️ 默认密码'}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='推荐结果 MySQL 导入脚本 - 将 CSV 文件导入数据库缓存表'
    )
    parser.add_argument('--user', '-u', type=str, default=None,
                        help='用户推荐 CSV 文件路径')
    parser.add_argument('--movie', '-m', type=str, default=None,
                        help='电影相似度 CSV 文件路径')
    parser.add_argument('--sql', '-s', action='store_true',
                        help='使用 INSERT 方式（备选，免配 local_infile 权限）')
    parser.add_argument('--host', type=str, default=DB_HOST,
                        help=f'MySQL 主机地址 (默认: {DB_HOST})')
    parser.add_argument('--port', type=str, default=DB_PORT,
                        help=f'MySQL 端口 (默认: {DB_PORT})')
    parser.add_argument('--user-mysql', type=str, default=DB_USER,
                        help=f'MySQL 用户名 (默认: {DB_USER})')
    parser.add_argument('--password', type=str, default=DB_PASSWORD,
                        help='MySQL 密码')
    parser.add_argument('--db', type=str, default=DB_NAME,
                        help=f'数据库名 (默认: {DB_NAME})')
    parser.add_argument('--export-dir', type=str, default=DEFAULT_EXPORT_DIR,
                        help=f'CSV 文件所在目录 (默认: {DEFAULT_EXPORT_DIR})')
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  推荐结果 MySQL 导入工具")
    print("=" * 60)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 显示配置
    print_config_info(args.host, args.user_mysql, args.db)

    # 确定要导入的文件
    user_csv = args.user
    movie_csv = args.movie

    if not user_csv and not movie_csv:
        # 自动查找
        user_csv, movie_csv = find_csv_files(args.export_dir)
        if user_csv:
            print(f"\n  [自动发现] 用户推荐: {os.path.basename(user_csv)}")
        if movie_csv:
            print(f"\n  [自动发现] 电影相似度: {os.path.basename(movie_csv)}")
        if not user_csv and not movie_csv:
            print("\n  [错误] 未找到 CSV 文件，请通过 --user / --movie 参数指定")
            sys.exit(1)

    # 选择导入方式
    if args.sql:
        print("\n使用 INSERT 方式导入（备选方案）")
    else:
        print("\n使用 LOAD DATA INFILE 方式导入（高性能）")
        print("如需使用 INSERT 方式，请添加 --sql 参数")
        print()

    success_all = True

    # 导入用户推荐
    if user_csv:
        if not os.path.isabs(user_csv):
            user_csv = os.path.join(args.export_dir, user_csv)
        if args.sql:
            ok = import_insert_rows(
                user_csv, 'users_recommendations',
                'user_id', 'recommend_movies',
                args.host, args.user_mysql, args.password, args.db, args.port
            )
        else:
            ok = import_load_data(
                user_csv, 'users_recommendations',
                'user_id, recommend_movies, algorithm, updated_at',
                args.host, args.user_mysql, args.password, args.db, args.port
            )
        if not ok:
            success_all = False

    # 导入电影相似度
    if movie_csv:
        if not os.path.isabs(movie_csv):
            movie_csv = os.path.join(args.export_dir, movie_csv)
        if args.sql:
            ok = import_insert_rows(
                movie_csv, 'movies_similarities',
                'movie_id', 'similar_movies',
                args.host, args.user_mysql, args.password, args.db, args.port
            )
        else:
            ok = import_load_data(
                movie_csv, 'movies_similarities',
                'movie_id, similar_movies, updated_at',
                args.host, args.user_mysql, args.password, args.db, args.port
            )
        if not ok:
            success_all = False

    # 结果汇总
    print("\n" + "=" * 60)
    if success_all:
        print("  ✅ 所有导入任务完成！")
    else:
        print("  ⚠️ 部分导入任务失败，请检查上述错误信息")
    print("=" * 60)
    print("""
  验证导入结果:
    SELECT COUNT(*) FROM users_recommendations;
    SELECT COUNT(*) FROM movies_similarities;
    SELECT * FROM users_recommendations WHERE user_id = 1;
    SELECT * FROM movies_similarities WHERE movie_id = 1;
""")


if __name__ == '__main__':
    main()