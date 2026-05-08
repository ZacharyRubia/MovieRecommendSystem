#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_to_mysql.py - 离线训练结果 MySQL 导入工具

功能:
  将 train_recommend.py 导出的 CSV 文件（在 scripts/export/ 目录下）
  批量写入 users_recommendations 和 movies_similarities 表。

用法:
  # 默认导入 export/ 下所有 CSV
  python scripts/import_to_mysql.py

  # 手动指定数据库连接
  python scripts/import_to_mysql.py --host 127.0.0.1 --user root --password 123456 --db MovieRecommendSystem

  # 只导入用户推荐，或只导入电影相似度
  python scripts/import_to_mysql.py --users-only
  python scripts/import_to_mysql.py --movies-only

  # 指定自定义 CSV 文件路径
  python scripts/import_to_mysql.py --user-csv ../export/users_recommendations.csv
  python scripts/import_to_mysql.py --movie-csv ../export/movies_similarities.csv

  # 仅打印将要导入的概览，不实际写入
  python scripts/import_to_mysql.py --dry-run

  # 清空目标表后再导入
  python scripts/import_to_mysql.py --truncate

前置条件:
  1. train_recommend.py 已运行完毕，CSV 文件已存在于 export/ 目录
  2. MySQL 服务已启动，数据库 MovieRecommendSystem 已创建
  3. 表结构已通过 database/init.sql 创建
"""

import os
import sys
import csv
import json
import argparse
from datetime import datetime

# ============================================================
# 配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(BASE_DIR, 'export')

# 默认数据库连接（可从环境变量覆盖）
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', '192.168.1.38'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', 'newuser'),
    'password': os.environ.get('DB_PASSWORD', 'yourpassword'),
    'database': os.environ.get('DB_NAME', 'MovieRecommendSystem'),
    'charset': 'utf8mb4',
    'local_infile': 1,  # 开启 LOAD DATA LOCAL
}

BATCH_SIZE = 500  # 每批 INSERT 行数


# ============================================================
# CSV 解析
# ============================================================

def parse_csv_line(line):
    """手动解析 CSV 行（支持引号内逗号），兼容 csv.reader"""
    result = []
    current = ''
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ',' and not in_quotes:
            result.append(current)
            current = ''
        else:
            current += ch
    result.append(current)
    return result


def read_csv_rows(filepath):
    """读取 CSV 文件，返回列表（每行是字符串列表）"""
    rows = []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for line in reader:
            if line and any(cell.strip() for cell in line):
                rows.append(line)
    return rows


def validate_json_str(json_str):
    """验证字符串是否为合法 JSON，返回清洗后的字符串"""
    # CSV 中可能因为引号转义产生多余引号，需要清理
    cleaned = json_str.replace('""', '"')
    # 尝试解析验证
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        # 如果失败，尝试直接使用原字符串
        try:
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            raise ValueError(f"无效 JSON: {e}") from e


# ============================================================
# 数据库导入
# ============================================================

def get_connection(config):
    """创建 MySQL 连接"""
    try:
        import pymysql
    except ImportError:
        print("❌ 未安装 pymysql，请执行: pip install pymysql")
        sys.exit(1)

    try:
        conn = pymysql.connect(
            host=config['host'],
            port=config['port'],
            user=config['user'],
            password=config['password'],
            database=config['database'],
            charset=config['charset'],
            autocommit=False,
        )
        return conn
    except pymysql.err.OperationalError as e:
        print(f"❌ 数据库连接失败: {e}")
        print(f"   连接信息: {config['user']}@{config['host']}:{config['port']}/{config['database']}")
        sys.exit(1)


def batch_import_users(conn, rows, dry_run=False):
    """
    批量导入 users_recommendations 表。
    CSV 格式: user_id, recommend_movies(JSON), algorithm, updated_at
    """
    total = len(rows)
    print(f"\n📥 导入 users_recommendations ({total} 条)...")

    if dry_run:
        print(f"   [DRY RUN] 将导入 {total} 条用户推荐记录")
        return total, 0

    success = 0
    errors = 0
    start_time = datetime.now()

    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        values = []

        for row_idx, row in enumerate(batch):
            try:
                user_id = int(row[0])
                json_str = validate_json_str(row[1])
                algorithm = row[2] if len(row) > 2 and row[2].strip() else 'svd'
                updated_at = (
                    row[3] if len(row) > 3 and row[3].strip()
                    else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                )
                values.append((user_id, json_str, algorithm, updated_at))
            except (ValueError, IndexError, json.JSONDecodeError) as e:
                errors += 1
                if errors <= 3:
                    print(f"   ⚠️ 第 {i + row_idx + 1} 行解析失败: {e}")

        if not values:
            continue

        sql = """REPLACE INTO `users_recommendations`
                 (`user_id`, `recommend_movies`, `algorithm`, `updated_at`)
                 VALUES (%s, %s, %s, %s)"""

        try:
            with conn.cursor() as cursor:
                cursor.executemany(sql, values)
            conn.commit()
            success += len(values)
        except Exception as e:
            conn.rollback()
            errors += len(values)
            print(f"   ❌ 批次 {i // BATCH_SIZE + 1} 导入失败: {e}")

        # 进度
        processed = min(i + BATCH_SIZE, total)
        pct = processed / total * 100
        elapsed = (datetime.now() - start_time).total_seconds()
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"   进度: {processed}/{total} ({pct:.1f}%) | "
              f"耗时 {elapsed:.1f}s | 速率 {rate:.0f} 条/秒")

    print(f"  ✅ 用户推荐导入完成: 成功 {success}, 失败 {errors}")
    return success, errors


def batch_import_movies(conn, rows, dry_run=False):
    """
    批量导入 movies_similarities 表。
    CSV 格式: movie_id, similar_movies(JSON), updated_at
    """
    total = len(rows)
    print(f"\n📥 导入 movies_similarities ({total} 条)...")

    if dry_run:
        print(f"   [DRY RUN] 将导入 {total} 条电影相似度记录")
        return total, 0

    success = 0
    errors = 0
    start_time = datetime.now()

    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        values = []

        for row_idx, row in enumerate(batch):
            try:
                movie_id = int(row[0])
                json_str = validate_json_str(row[1])
                updated_at = (
                    row[2] if len(row) > 2 and row[2].strip()
                    else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                )
                values.append((movie_id, json_str, updated_at))
            except (ValueError, IndexError, json.JSONDecodeError) as e:
                errors += 1
                if errors <= 3:
                    print(f"   ⚠️ 第 {i + row_idx + 1} 行解析失败: {e}")

        if not values:
            continue

        sql = """REPLACE INTO `movies_similarities`
                 (`movie_id`, `similar_movies`, `updated_at`)
                 VALUES (%s, %s, %s)"""

        try:
            with conn.cursor() as cursor:
                cursor.executemany(sql, values)
            conn.commit()
            success += len(values)
        except Exception as e:
            conn.rollback()
            errors += len(values)
            print(f"   ❌ 批次 {i // BATCH_SIZE + 1} 导入失败: {e}")

        processed = min(i + BATCH_SIZE, total)
        pct = processed / total * 100
        elapsed = (datetime.now() - start_time).total_seconds()
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"   进度: {processed}/{total} ({pct:.1f}%) | "
              f"耗时 {elapsed:.1f}s | 速率 {rate:.0f} 条/秒")

    print(f"  ✅ 电影相似度导入完成: 成功 {success}, 失败 {errors}")
    return success, errors


def count_table(conn, table_name):
    """查询表中记录数"""
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
        return cursor.fetchone()[0]


def truncate_table(conn, table_name):
    """清空表"""
    with conn.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE `{table_name}`")
    conn.commit()
    print(f"  🗑️ 已清空表: {table_name}")


# ============================================================
# LOAD DATA LOCAL 模式（高性能，适合大文件）
# ============================================================

def load_data_users(conn, csv_path, dry_run=False):
    """使用 LOAD DATA LOCAL INFILE 导入 users_recommendations"""
    if not os.path.exists(csv_path):
        print(f"  [跳过] 文件不存在: {csv_path}")
        return False

    if dry_run:
        print(f"  [DRY RUN] LOAD DATA: {csv_path} -> users_recommendations")
        return True

    sql = f"""
        LOAD DATA LOCAL INFILE '{csv_path.replace(os.sep, '/')}'
        REPLACE INTO TABLE `users_recommendations`
        FIELDS TERMINATED BY ',' ENCLOSED BY '"'
        LINES TERMINATED BY '\\r\\n'
        (`user_id`, `recommend_movies`, `algorithm`, `updated_at`)
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
        print(f"  ✅ LOAD DATA 完成 -> users_recommendations")
        return True
    except Exception as e:
        print(f"  ❌ LOAD DATA 失败: {e}")
        return False


def load_data_movies(conn, csv_path, dry_run=False):
    """使用 LOAD DATA LOCAL INFILE 导入 movies_similarities"""
    if not os.path.exists(csv_path):
        print(f"  [跳过] 文件不存在: {csv_path}")
        return False

    if dry_run:
        print(f"  [DRY RUN] LOAD DATA: {csv_path} -> movies_similarities")
        return True

    sql = f"""
        LOAD DATA LOCAL INFILE '{csv_path.replace(os.sep, '/')}'
        REPLACE INTO TABLE `movies_similarities`
        FIELDS TERMINATED BY ',' ENCLOSED BY '"'
        LINES TERMINATED BY '\\r\\n'
        (`movie_id`, `similar_movies`, `updated_at`)
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
        print(f"  ✅ LOAD DATA 完成 -> movies_similarities")
        return True
    except Exception as e:
        print(f"  ❌ LOAD DATA 失败: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def print_config(csv_user, csv_movie, db_config, method, dry_run, truncate):
    """打印导入配置"""
    print("=" * 60)
    print("  离线推荐结果 → MySQL 导入工具")
    print("=" * 60)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {'🟡 DRY RUN (仅预览)' if dry_run else '🟢 实际导入'}")
    print(f"  方法: {'LOAD DATA LOCAL INFILE (高性能)' if method == 'load' else '批量 INSERT (兼容)'}")
    print(f"  数据库: {db_config['user']}@{db_config['host']}:{db_config['port']}/{db_config['database']}")

    if truncate:
        print(f"  清空表: 导入前将清空目标表")

    print(f"\n  CSV 文件:")
    print(f"    用户推荐: {csv_user or '(未指定, 跳过)'}")
    print(f"    电影相似度: {csv_movie or '(未指定, 跳过)'}")

    if csv_user and os.path.exists(csv_user):
        size = os.path.getsize(csv_user) / (1024 * 1024)
        print(f"      大小: {size:.2f} MB")
    if csv_movie and os.path.exists(csv_movie):
        size = os.path.getsize(csv_movie) / (1024 * 1024)
        print(f"      大小: {size:.2f} MB")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='将离线训练的推荐结果 CSV 导入 MySQL',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/import_to_mysql.py
  python scripts/import_to_mysql.py --host 127.0.0.1 --user root --password 123456
  python scripts/import_to_mysql.py --users-only --load-data
  python scripts/import_to_mysql.py --truncate --dry-run
        """,
    )

    # CSV 来源
    parser.add_argument('--user-csv', type=str, default=None,
                        help='用户推荐 CSV 文件路径（默认: export/users_recommendations.csv）')
    parser.add_argument('--movie-csv', type=str, default=None,
                        help='电影相似度 CSV 文件路径（默认: export/movies_similarities.csv）')
    parser.add_argument('--export-dir', type=str, default=EXPORT_DIR,
                        help=f'CSV 文件所在目录 (默认: {EXPORT_DIR})')

    # 导入范围
    parser.add_argument('--users-only', action='store_true',
                        help='只导入用户推荐')
    parser.add_argument('--movies-only', action='store_true',
                        help='只导入电影相似度')

    # 导入方式
    parser.add_argument('--load-data', action='store_true',
                        help='使用 LOAD DATA LOCAL INFILE（高速，需 MySQL 服务端开启 local_infile）')
    parser.add_argument('--truncate', action='store_true',
                        help='导入前清空目标表')

    # 预览模式
    parser.add_argument('--dry-run', action='store_true',
                        help='仅预览统计信息，不实际写入数据库')

    # 数据库连接
    parser.add_argument('--host', type=str, default=DB_CONFIG['host'],
                        help=f'MySQL 主机地址 (默认: {DB_CONFIG["host"]})')
    parser.add_argument('--port', type=int, default=DB_CONFIG['port'],
                        help=f'MySQL 端口 (默认: {DB_CONFIG["port"]})')
    parser.add_argument('--user', type=str, default=DB_CONFIG['user'],
                        help=f'MySQL 用户名 (默认: {DB_CONFIG["user"]})')
    parser.add_argument('--password', type=str, default=DB_CONFIG['password'],
                        help='MySQL 密码')
    parser.add_argument('--db', type=str, default=DB_CONFIG['database'],
                        help=f'数据库名 (默认: {DB_CONFIG["database"]})')

    args = parser.parse_args()

    # ---- 构建数据库配置 ----
    db_config = DB_CONFIG.copy()
    db_config.update({
        'host': args.host,
        'port': args.port,
        'user': args.user,
        'password': args.password,
        'database': args.db,
    })

    # ---- 查找 CSV 文件 ----
    user_csv = args.user_csv
    movie_csv = args.movie_csv

    if not user_csv and not args.movies_only:
        default_path = os.path.join(args.export_dir, 'users_recommendations.csv')
        if os.path.exists(default_path):
            user_csv = default_path
    if not movie_csv and not args.users_only:
        default_path = os.path.join(args.export_dir, 'movies_similarities.csv')
        if os.path.exists(default_path):
            movie_csv = default_path

    # ---- 打印配置 ----
    print_config(user_csv, movie_csv, db_config,
                 'load' if args.load_data else 'insert',
                 args.dry_run, args.truncate)

    # ---- 检查输入 ----
    has_any_input = [
        user_csv and os.path.exists(user_csv),
        movie_csv and os.path.exists(movie_csv),
    ]
    if not any(has_any_input):
        print("\n❌ 未找到任何 CSV 文件！")
        print(f"   期望目录: {args.export_dir}")
        print("   请先运行: python scripts/recommend/train_recommend.py")
        sys.exit(1)

    # ---- 连接数据库 ----
    conn = None
    if args.dry_run:
        print("\n[DRY RUN] 跳过数据库连接")
    else:
        conn = get_connection(db_config)

        # 导入前记录数
        before_users = count_table(conn, 'users_recommendations')
        before_movies = count_table(conn, 'movies_similarities')
        print(f"\n📊 导入前统计:")
        print(f"   users_recommendations: {before_users} 条")
        print(f"   movies_similarities:   {before_movies} 条")

        # 可选清空表
        if args.truncate:
            if user_csv:
                truncate_table(conn, 'users_recommendations')
            if movie_csv:
                truncate_table(conn, 'movies_similarities')

    # ---- 导入 ----
    all_ok = True

    # 用户推荐
    if user_csv and not args.movies_only:
        if not os.path.exists(user_csv):
            print(f"\n⚠️ 文件不存在, 跳过: {user_csv}")
        else:
            if args.load_data and not args.dry_run:
                ok = load_data_users(conn, user_csv, args.dry_run)
            else:
                rows = read_csv_rows(user_csv)
                print(f"\n  CSV 文件: {os.path.basename(user_csv)} ({len(rows)} 行)")
                if rows:
                    ok = batch_import_users(conn, rows, args.dry_run)
                    all_ok = all_ok and (ok[1] == 0)  # 无错误
                else:
                    print("  ⚠️ CSV 文件为空")

    # 电影相似度
    if movie_csv and not args.users_only:
        if not os.path.exists(movie_csv):
            print(f"\n⚠️ 文件不存在, 跳过: {movie_csv}")
        else:
            if args.load_data and not args.dry_run:
                ok = load_data_movies(conn, movie_csv, args.dry_run)
            else:
                rows = read_csv_rows(movie_csv)
                print(f"\n  CSV 文件: {os.path.basename(movie_csv)} ({len(rows)} 行)")
                if rows:
                    ok = batch_import_movies(conn, rows, args.dry_run)
                    all_ok = all_ok and (ok[1] == 0)
                else:
                    print("  ⚠️ CSV 文件为空")

    # ---- 导入后统计 ----
    if not args.dry_run:
        after_users = count_table(conn, 'users_recommendations')
        after_movies = count_table(conn, 'movies_similarities')
        print(f"\n📊 导入后统计:")
        print(f"   users_recommendations: {after_users} 条 ({after_users - before_users:+,d})")
        print(f"   movies_similarities:   {after_movies} 条 ({after_movies - before_movies:+,d})")

        conn.close()

    # ---- 结果汇总 ----
    print("\n" + "=" * 60)
    if args.dry_run:
        print("  🟡 DRY RUN 完成 - 未实际写入数据库")
        print("  移除 --dry-run 执行实际导入")
    elif all_ok:
        print("  ✅ 全部导入成功！")
    else:
        print("  ⚠️ 部分导入失败，请检查上述错误信息")
    print("=" * 60)

    # 验证提示
    print(f"""
  验证导入:
    SELECT COUNT(*) FROM users_recommendations;
    SELECT COUNT(*) FROM movies_similarities;
    SELECT * FROM users_recommendations WHERE user_id = 1\\G;
    SELECT * FROM movies_similarities WHERE movie_id = 1\\G;
""")


if __name__ == '__main__':
    main()