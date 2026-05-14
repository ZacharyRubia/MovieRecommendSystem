"""
import_to_mysql.py - 将推荐结果 CSV 文件导入 MySQL 数据库

支持 LOAD DATA INFILE 和逐行 INSERT 两种模式。
配合 export_recommendations.py 生成的 CSV 使用。

用法:
  python import_to_mysql.py
  python import_to_mysql.py --mode insert       # 强制逐行 INSERT（速度较慢但兼容性更好）
  python import_to_mysql.py --verify-only       # 仅校验 CSV 文件和数据库连接

  # 指定自定义 CSV 文件路径
  python scripts/import/import_to_mysql.py --user-csv ../export/users_recommendations.csv
  python scripts/import/import_to_mysql.py --movie-csv ../export/movies_similarities.csv

  # 仅打印将要导入的概览，不实际写入
  python scripts/import/import_to_mysql.py --dry-run

  # 清空目标表后再导入
  python scripts/import/import_to_mysql.py --truncate

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
# scripts/import/import_to_mysql.py → scripts/import/ → scripts/ → 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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