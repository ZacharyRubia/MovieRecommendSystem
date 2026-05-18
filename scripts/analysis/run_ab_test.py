"""
run_ab_test.py - A/B 测试分析调度入口

设计文档 §5.1 — 通过 crontab/Celery 每 30 分钟执行一次。

使用方式（三种模式）：
  1. crontab:  * /30 * * * * cd /path && python run_ab_test.py
  2. Celery:   celery -A run_ab_test.celery_app beat
  3. 单次运行:  python run_ab_test.py --once

依赖: pip install celery redis
"""

import argparse
import logging
import sys
import time

from ab_analysis import run_analysis

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] run_ab_test: %(message)s',
)
logger = logging.getLogger('run_ab_test')


def run_once():
    """单次执行分析（供 crontab 调度）"""
    logger.info("--- 单次 A/B 测试分析 ---")
    try:
        run_analysis()
        logger.info("分析完成")
    except Exception as e:
        logger.error(f"分析失败: {e}", exc_info=True)
        sys.exit(1)


def run_loop(interval_minutes: int = 30):
    """
    循环执行分析（供直接运行或 Docker 容器使用）。

    Args:
        interval_minutes: 分析间隔（分钟）
    """
    logger.info(f"启动 A/B 测试分析循环，间隔 {interval_minutes} 分钟")
    while True:
        run_once()
        logger.info(f"等待 {interval_minutes} 分钟后进行下一次分析...")
        time.sleep(interval_minutes * 60)


# =============================================
# Celery Beat 定时任务配置（可选）
# =============================================
# 如果项目使用 Celery，取消以下注释并使用 celery beat 调度
#
# from celery import Celery
# from celery.schedules import crontab
#
# celery_app = Celery('ab_test', broker='redis://localhost:6379/0')
#
# @celery_app.on_after_configure.connect
# def setup_periodic_tasks(sender, **kwargs):
#     sender.add_periodic_task(
#         crontab(minute='*/30'),
#         run_analysis.s(),
#         name='ab_test_analysis_every_30min'
#     )
#
# @celery_app.task
# def run_analysis_task():
#     run_analysis()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='A/B 测试分析调度入口')
    parser.add_argument(
        '--once', action='store_true',
        help='单次运行分析（用于 crontab 调度）'
    )
    parser.add_argument(
        '--interval', type=int, default=30,
        help='循环运行间隔（分钟），默认 30'
    )
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_loop(args.interval)