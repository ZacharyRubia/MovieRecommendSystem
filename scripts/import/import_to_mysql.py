"""
import_to_mysql.py - 将推荐结果 CSV 文件导入 MySQL 数据库（多算法版本）

支持 LOAD DATA INFILE 和逐行 INSERT 两种模式。
自动扫描 scripts/recommend/export/ 目录中的多算法 CSV 文件。

相比旧版本的关键变化:
  1. 目标表名: user_recommendation_caches / item_similarity_caches
  2. CSV 文件命名格式如 svd_users_recommendations.csv，自动扫描发现
  3. 支持 --algorithm 筛选特定算法
  4. 兼容四种算法 + 混合推荐 (hybrid)

用法:
  python scripts/import/import_to_mysql.py                         # 导入所有算法
  python scripts/import/import_to_mysql.py --algorithm svd         # 仅导入 SVD
  python scripts/import/import_to_mysql.py --mode insert           # 强制逐行 INSERT
  python scripts/import/import_to_mysql.py --users-only            # 仅导入用户推荐
  python scripts/import/import_to_mysql.py --movies-only           # 仅导入电影相似度
  python scripts/import/import_to_mysql.py --list                  # 列出可导入的文件
  python scripts/import/import_to_mysql.py --verify-only           # 仅校验
  python scripts/import/import_to_mysql.py --dry-run               # 仅打印概览
  python scripts/import/import_to_mysql.py --truncate              # 清空目标表后导入

前置条件:
  1. train_recommend.py 已运行完毕，CSV 文件已存在于 export/ 目录
  2. MySQL 服务已启动，数据库 MovieRecommendSystem 已创建
  3. 表结构已通过 database/init.sql 创建
"""

import os
import sys
import re
import csv
import json
import argparse
from datetime import datetime

# ============================================================
# 配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 多算法 CSV 文件的导出目录（由 train_recommend.py 生成）
EXPORT_DIR = os.path.join(BASE_DIR, 'scripts', 'recommend', 'export')
if not os.path.isdir(EXPORT_DIR):
    # 后备: 也可能是 BASE_DIR/export/
    EXPORT_DIR = os.path.join(BASE_DIR, 'export')

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', '192.168.1.38'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', 'newuser'),
    'password': os.environ.get('DB_PASSWORD', 'yourpassword'),
    'database': os.environ.get('DB_NAME', 'MovieRecommendSystem'),
    'charset': 'utf8mb4',
    'local_infile': 1,
}

# 新表名
TABLE_USERS = 'user_recommendation_caches'
TABLE_MOVIES = 'item_similarity_caches'

BATCH_SIZE = 500


# ============================================================
# 辅助函数
# ============================================================

def parse_csv_filename(filename):
    """从文件名提取算法名和类型
    例如: svd_users_recommendations.csv → ('svd', 'users_recommendations')
          item_cf_movies_similarities.csv → ('item_cf', 'movies_similarities')
    """
    pattern = r'^(.+?)_(users_recommendations|movies_similarities)\.csv$'
    m = re.match(pattern, filename)
    if not m:
        return None
    return m.group(1), m.group(2)


def scan_csv_files(export_dir):
    """扫描导出目录，返回文件信息列表"""
    files = []
    if not os.path.isdir(export_dir):
        return files
    for fname in os.listdir(export_dir):
        info = parse_csv_filename(fname)
        if info:
            files.append({
                'algorithm': info[0],
                'type': info[1],          # 'users_recommendations' | 'movies_similarities'
                'path': os.path.join(export_dir, fname),
                'filename': fname,
            })
    return sorted(files, key=lambda x: (x['algorithm'], x['type']))


def clean_json_str(s):
    """修复 CSV 中 JSON 字符串的双引号转义问题"""
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace('""', '"')


def verify_csv(file_info):
    """验证单个 CSV 文件的格式"""
    errors = []
    try:
        with open(file_info['path'], 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            row_count = 0
            for i, row in enumerate(reader):
                row_count += 1
                if len(row) < 2:
                    errors.append(f"第 {i+1} 行列数不足({len(row)})")
                    continue
                if i == 0:
                    continue  # 跳过可能的表头（实际上无表头）
                try:
                    int(row[0])  # id 字段
                except ValueError:
                    errors.append(f"第 {i+1} 行 ID 非整数: {row[0]}")
                try:
                    json.loads(clean_json_str(row[1]))
                except json.JSONDecodeError as e:
                    if errors.__len__() < 3:
                        errors.append(f"第 {i+1} 行 JSON 解析失败: {e}")
        return True, row_count, errors
    except Exception as e:
        return False, 0, [str(e)]


# ============================================================
# MySQL 操作
# ============================================================

def _get_connection():
    """获取 MySQL 连接"""
    try:
        import pymysql
        conn = pymysql.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            charset=DB_CONFIG['charset'],
            local_infile=DB_CONFIG['local_infile'],
            autocommit=False,
        )
        return conn
    except ImportError:
        # 后备: mysql-connector-python
        import mysql.connector
        conn = mysql.connector.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            charset=DB_CONFIG['charset'],
            allow_local_infile=bool(DB_CONFIG['local_infile']),
            autocommit=False,
        )
        return conn


def _load_data_infile(conn, csv_path, table_name, algorithm):
    """使用 LOAD DATA LOCAL INFILE 批量导入（最快方式）"""
    cursor = conn.cursor()
    
    if table_name == TABLE_USERS:
        columns = '(user_id, recommend_movies, algorithm, updated_at)'
    else:
        columns = '(movie_id, similar_movies, algorithm, updated_at)'
    
    sql = (
        f"LOAD DATA LOCAL INFILE '{csv_path.replace(chr(92), chr(92) * 2)}' "
        f"REPLACE INTO TABLE `{table_name}` "
        f"FIELDS TERMINATED BY ',' "
        f"ENCLOSED BY '\"' "
        f"LINES TERMINATED BY '\\n' "
        f"{columns}"
    )
    
    cursor.execute(sql)
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    return affected


def _insert_batch(conn, batch, table_name, algorithm):
    """使用逐行 INSERT 导入一个批次"""
    cursor = conn.cursor()
    
    if table_name == TABLE_USERS:
        sql = (
            f"REPLACE INTO `{table_name}` "
            f"(`user_id`, `recommend_movies`, `algorithm`, `updated_at`) "
            f"VALUES (%s, %s, %s, %s)"
        )
    else:
        sql = (
            f"REPLACE INTO `{table_name}` "
            f"(`movie_id`, `similar_movies`, `algorithm`, `updated_at`) "
            f"VALUES (%s, %s, %s, %s)"
        )
    
    values = []
    for row in batch:
        try:
            id_val = int(row[0])
            json_str = clean_json_str(row[1])
            algo = row[2] if len(row) > 2 and row[2].strip() else algorithm
            updated_at = row[3] if len(row) > 3 and row[3].strip() else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            json.loads(json_str)  # 验证 JSON
            values.append((id_val, json_str, algo, updated_at))
        except (ValueError, json.JSONDecodeError) as e:
            # 跳过无效行
            pass
    
    if values:
        cursor.executemany(sql, values)
        conn.commit()
    
    affected = cursor.rowcount
    cursor.close()
    return affected


def import_csv_to_mysql(conn, csv_path, table_name, algorithm, mode='auto', dry_run=False):
    """将单个 CSV 文件导入指定表"""
    if not os.path.exists(csv_path):
        print(f"  ⚠️ 文件不存在: {csv_path}")
        return 0
    
    # 读行数
    with open(csv_path, 'r', encoding='utf-8') as f:
        total = sum(1 for _ in f)
    
    table_label = table_name
    print(f"  📥 导入 {table_label} (算法={algorithm}, {total} 行...)")
    
    if dry_run:
        print(f"     [dry-run] 跳过实际写入")
        return total
    
    # 选择导入模式
    use_load_data = False
    if mode == 'auto':
        try:
            conn.ping(reconnect=True)
            cursor = conn.cursor()
            cursor.execute("SHOW VARIABLES LIKE 'local_infile'")
            row = cursor.fetchone()
            use_load_data = row and row[1].upper() == 'ON'
            cursor.close()
        except Exception:
            use_load_data = False
    elif mode == 'load_data':
        use_load_data = True
    
    start_time = datetime.now()
    
    if use_load_data and mode != 'insert':
        try:
            affected = _load_data_infile(conn, csv_path, table_name, algorithm)
            print(f"  ✅ LOAD DATA 完成: {affected} 行, 耗时 {(datetime.now() - start_time).total_seconds():.1f}s")
            return affected
        except Exception as e:
            print(f"  ⚠️ LOAD DATA 失败 ({e}), 回退到 INSERT...")
    
    # INSERT 模式（回退或手动指定）
    affected = 0
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        batch = []
        for row in reader:
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                affected += _insert_batch(conn, batch, table_name, algorithm)
                batch = []
        if batch:
            affected += _insert_batch(conn, batch, table_name, algorithm)
    
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"  ✅ INSERT 完成: {affected} 行, 耗时 {elapsed:.1f}s")
    return affected


def truncate_table(conn, table_name):
    """清空指定表"""
    cursor = conn.cursor()
    cursor.execute(f"TRUNCATE TABLE `{table_name}`")
    conn.commit()
    cursor.close()
    print(f"  🗑️ 已清空: {table_name}")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='将推荐结果 CSV 文件导入 MySQL（多算法版本）'
    )
    parser.add_argument('--mode', choices=['auto', 'load_data', 'insert'], default='auto',
                        help='导入模式 (默认: auto)')
    parser.add_argument('--algorithm', type=str, default=None,
                        help='筛选特定算法 (例如 svd, item_cf, hybrid 等)')
    parser.add_argument('--users-only', action='store_true',
                        help='仅导入用户推荐表')
    parser.add_argument('--movies-only', action='store_true',
                        help='仅导入电影相似度表')
    parser.add_argument('--list', action='store_true',
                        help='列出可导入的文件并退出')
    parser.add_argument('--verify-only', action='store_true',
                        help='仅校验 CSV 文件和数据库连接')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅打印导入概览，不实际写入')
    parser.add_argument('--truncate', action='store_true',
                        help='清空目标表后再导入')
    parser.add_argument('--export-dir', type=str, default=None,
                        help='CSV 文件所在目录 (默认: 自动检测)')
    
    args = parser.parse_args()
    
    export_dir = args.export_dir or EXPORT_DIR
    
    print('=' * 60)
    print('  推荐结果 MySQL 导入工具 (多算法版本)')
    print('=' * 60)
    print(f'  导出目录: {export_dir}')
    print(f'  数据库: {DB_CONFIG["host"]}/{DB_CONFIG["database"]}')
    print(f'  用户: {DB_CONFIG["user"]}')
    if args.algorithm:
        print(f'  算法筛选: {args.algorithm}')
    if args.users_only:
        print(f'  模式: 仅用户推荐')
    if args.movies_only:
        print(f'  模式: 仅电影相似度')
    print(f'  目标表: {TABLE_USERS} / {TABLE_MOVIES}')
    print(f'  导入模式: {args.mode}')
    print('=' * 60)
    
    # 扫描 CSV 文件
    files = scan_csv_files(export_dir)
    
    if not files:
        print(f'\n❌ 未找到可导入的 CSV 文件')
        print(f'   期望路径: {export_dir}')
        print(f'   文件命名示例:')
        print(f'     svd_users_recommendations.csv')
        print(f'     hybrid_users_recommendations.csv')
        print(f'     item_cf_movies_similarities.csv')
        print(f'   tip: 请先运行 python scripts/recommend/train_recommend.py')
        sys.exit(1)
    
    # 筛选
    filtered = files
    if args.algorithm:
        filtered = [f for f in filtered if f['algorithm'] == args.algorithm]
    if args.users_only:
        filtered = [f for f in filtered if f['type'] == 'users_recommendations']
    if args.movies_only:
        filtered = [f for f in filtered if f['type'] == 'movies_similarities']
    
    # 列出文件
    users_files = [f for f in filtered if f['type'] == 'users_recommendations']
    movies_files = [f for f in filtered if f['type'] == 'movies_similarities']
    
    print(f'\n  待导入文件:')
    if users_files:
        print(f'  📋 用户推荐 ({TABLE_USERS}):')
        for f in users_files:
            size = os.path.getsize(f['path']) / (1024 * 1024)
            print(f'     {f["algorithm"]:12s}  → {f["filename"]:40s}  ({size:.2f} MB)')
    if movies_files:
        print(f'  📋 电影相似度 ({TABLE_MOVIES}):')
        for f in movies_files:
            size = os.path.getsize(f['path']) / (1024 * 1024)
            print(f'     {f["algorithm"]:12s}  → {f["filename"]:40s}  ({size:.2f} MB)')
    
    if not filtered:
        print('\n❌ 没有匹配的文件')
        sys.exit(1)
    
    if args.list:
        return
    
    # 验证模式
    if args.verify_only:
        print(f'\n{"=" * 60}')
        print(f'  验证模式')
        print(f'{"=" * 60}')
        all_ok = True
        for f in filtered:
            ok, count, errors = verify_csv(f)
            if ok:
                print(f'  ✅ {f["filename"]}: {count} 行，格式正确')
            else:
                print(f'  ❌ {f["filename"]}: 格式错误!')
                for err in errors[:3]:
                    print(f'      {err}')
                all_ok = False
        
        # 验证数据库连接
        try:
            conn = _get_connection()
            print(f'  ✅ 数据库连接成功: {DB_CONFIG["host"]}/{DB_CONFIG["database"]}')
            
            cursor = conn.cursor()
            for tbl in [TABLE_USERS, TABLE_MOVIES]:
                cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
                cnt = cursor.fetchone()[0]
                print(f'     {tbl}: 当前 {cnt} 行')
            cursor.close()
            conn.close()
        except Exception as e:
            print(f'  ❌ 数据库连接失败: {e}')
            all_ok = False
        
        if all_ok:
            print(f'\n✅ 验证通过！')
        else:
            print(f'\n⚠️ 存在错误，请检查！')
            sys.exit(1)
        return
    
    # 连接数据库
    try:
        conn = _get_connection()
        print(f'\n✅ 数据库连接成功\n')
    except Exception as e:
        print(f'\n❌ 数据库连接失败: {e}')
        print('   请检查环境变量或 .env 中的数据库配置')
        print('   当前配置:')
        print(f'     DB_HOST: {DB_CONFIG["host"]}')
        print(f'     DB_PORT: {DB_CONFIG["port"]}')
        print(f'     DB_USER: {DB_CONFIG["user"]}')
        print(f'     DB_PASSWORD: {DB_CONFIG["password"][:3]}***')
        print(f'     DB_NAME: {DB_CONFIG["database"]}')
        sys.exit(1)
    
    try:
        # 可选：清空目标表
        if args.truncate:
            tables_to_truncate = set()
            if users_files:
                tables_to_truncate.add(TABLE_USERS)
            if movies_files:
                tables_to_truncate.add(TABLE_MOVIES)
            
            for tbl in sorted(tables_to_truncate):
                truncate_table(conn, tbl)
            print()
        
        # 导入全部
        total_affected = 0
        for f in filtered:
            if f['type'] == 'users_recommendations':
                table_name = TABLE_USERS
            else:
                table_name = TABLE_MOVIES
            
            affected = import_csv_to_mysql(
                conn, f['path'], table_name,
                f['algorithm'], mode=args.mode, dry_run=args.dry_run
            )
            total_affected += affected
            print()
    
    except KeyboardInterrupt:
        print('\n⏹️ 用户中断')
        conn.rollback()
    except Exception as e:
        print(f'\n❌ 导入异常: {e}')
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()
    
    if not args.dry_run:
        print(f'✅ 总导入: {total_affected} 行')
        print(f'\n验证 SQL:')
        print(f'  SELECT algorithm, COUNT(*) FROM {TABLE_USERS} GROUP BY algorithm;')
        print(f'  SELECT algorithm, COUNT(*) FROM {TABLE_MOVIES} GROUP BY algorithm;')


if __name__ == '__main__':
    main()