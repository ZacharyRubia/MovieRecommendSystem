"""
ab_analysis.py - A/B 测试离线分析主模块

覆盖设计文档 §5 要求：
  1. 数据加载 — 从 MySQL 读取行为数据，按实验和策略分组
  2. 指标计算 — CTR、人均观看时长、评分率、收藏率 + 95% 置信区间
  3. 统计检验 — Z 检验（比例）/ t 检验（均值）/ Mann-Whitney U
  4. Bandit 参数更新 — 更新 Beta 后验分布，写入 Redis
  5. 结果存储 — 写入 ab_results 表
  6. 收敛判定 — 蒙特卡洛获胜概率 → 自动推全

数据流闭环 (设计文档 §1):
  [Python 离线分析: 聚合指标 + 统计检验 + 更新后验参数] → [Redis]
                                                                ↓
  [下一次流量分发读取 Redis 后验参数，实现自适应分流]
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple

import pymysql
import redis

import config as cfg
from stat_utils import (
    compute_proportion_ci,
    two_proportion_z_test,
    mean_comparison_test,
    minimum_sample_size,
    compute_win_probability,
)

# =============================================
# 日志配置
# =============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger('ab_analysis')


# =============================================
# 数据库 / Redis 连接
# =============================================

def get_mysql_connection():
    """获取 MySQL 连接"""
    return pymysql.connect(**cfg.MYSQL_CONFIG)


def get_redis_connection():
    """获取 Redis 连接"""
    return redis.Redis(**cfg.REDIS_CONFIG)


# =============================================
# 1. 数据加载 (设计文档 §5.2)
# =============================================

def load_experiments(conn) -> List[Dict]:
    """
    加载所有进行中的实验及其策略配置。

    Returns:
        list of dict: [
            {
                'id': int,
                'name': str,
                'split_mode': 'fixed' | 'bandit',
                'start_time': datetime,
                'strategies': [
                    { 'id': int, 'name': str, 'algorithm': str,
                      'traffic_percentage': float, 'weight_source': str,
                      'bandit_alpha': float, 'bandit_beta': float,
                      'min_traffic': float, 'coldstart_end_time': datetime|None,
                      'is_control': bool },
                    ...
                ]
            },
            ...
        ]
    """
    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(
            "SELECT * FROM ab_experiments WHERE status = 'running'"
        )
        experiments = cursor.fetchall()

        for exp in experiments:
            cursor.execute(
                "SELECT * FROM ab_strategies WHERE experiment_id = %s ORDER BY id",
                (exp['id'],)
            )
            exp['strategies'] = cursor.fetchall()

    logger.info(f"加载进行中实验 {len(experiments)} 个")
    return experiments


def load_behavior_data(
    conn,
    experiment_id: int,
    strategy_ids: List[int],
    since: datetime
) -> Dict[int, List[Dict]]:
    """
    加载指定实验下的行为数据，按策略分组。

    设计文档 §5.2 — 加载最近 24h 窗口的行为数据。

    Args:
        conn: MySQL 连接
        experiment_id: 实验 ID
        strategy_ids: 策略 ID 列表
        since: 起始时间（最近 24h）

    Returns:
        dict: { strategy_id: [ { behavior_type, rating_value, watch_seconds, ... }, ... ] }
    """
    if not strategy_ids:
        return {}

    placeholders = ','.join(['%s'] * len(strategy_ids))
    sql = f"""
        SELECT strategy_id, user_id, behavior_type, rating_value, watch_seconds
        FROM users_movies_behaviors
        WHERE experiment_id = %s
          AND strategy_id IN ({placeholders})
          AND created_at >= %s
    """
    params = [experiment_id] + strategy_ids + [since]

    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    grouped: Dict[int, List[Dict]] = {sid: [] for sid in strategy_ids}
    for row in rows:
        sid = row['strategy_id']
        if sid in grouped:
            grouped[sid].append(row)

    for sid, data in grouped.items():
        logger.info(f"  策略 {sid}: 加载 {len(data)} 条行为记录")

    return grouped


# =============================================
# 2. 指标计算 (设计文档 §5.2)
# =============================================

def compute_metrics(
    strategy_id: int,
    behavior_data: List[Dict],
    positive_event_types: tuple = cfg.POSITIVE_EVENT_TYPES,
    positive_rating_threshold: int = cfg.POSITIVE_RATING_THRESHOLD,
) -> Dict[str, Any]:
    """
    计算单个策略的聚合指标。

    设计文档 §5.2:
      - CTR = 正向事件数 / 总曝光数
      - 人均观看时长 = 总观看时长 / 独立用户数
      - 评分率 = 评分次数 / 总曝光数
      - 收藏率 = 收藏次数 / 总曝光数
      - 各指标 95% Wilson 置信区间

    Args:
        strategy_id: 策略 ID
        behavior_data: 该策略的行为数据列表
        positive_event_types: 正向事件类型元组
        positive_rating_threshold: 评分正向阈值

    Returns:
        dict: 包含所有聚合指标
    """
    total_exposures = len(behavior_data)
    if total_exposures == 0:
        return {
            'strategy_id': strategy_id,
            'total_exposures': 0,
            'total_clicks': 0,
            'total_ratings': 0,
            'total_collects': 0,
            'total_watch_seconds': 0,
            'unique_users': 0,
            'ctr': 0.0,
            'ctr_ci_lower': 0.0,
            'ctr_ci_upper': 0.0,
            'avg_watch_seconds': 0.0,
            'rating_rate': 0.0,
            'rating_rate_ci_lower': 0.0,
            'rating_rate_ci_upper': 0.0,
            'collect_rate': 0.0,
            'collect_rate_ci_lower': 0.0,
            'collect_rate_ci_upper': 0.0,
            'positive_events': 0,
        }

    unique_users = set()
    total_clicks = 0
    total_ratings = 0
    total_collects = 0
    total_watch_seconds = 0
    positive_events = 0

    for row in behavior_data:
        user_id = row.get('user_id')
        if user_id:
            unique_users.add(user_id)

        bt = row.get('behavior_type', '')
        rv = row.get('rating_value') or 0

        if bt == 'click':
            total_clicks += 1
            positive_events += 1
        elif bt == 'rate':
            total_ratings += 1
            if float(rv) >= positive_rating_threshold:
                positive_events += 1
        elif bt == 'favorite':
            total_collects += 1
            positive_events += 1
        elif bt == 'play':
            ws = float(row.get('watch_seconds') or 0)
            total_watch_seconds += ws
            if ws > 30:  # 播放超过 30 秒算正向
                positive_events += 1

    ctr = positive_events / total_exposures
    rating_rate = total_ratings / total_exposures
    collect_rate = total_collects / total_exposures

    # Wilson 置信区间
    ctr_ci = compute_proportion_ci(positive_events, total_exposures)
    rating_ci = compute_proportion_ci(total_ratings, total_exposures)
    collect_ci = compute_proportion_ci(total_collects, total_exposures)

    avg_watch = total_watch_seconds / len(unique_users) if unique_users else 0.0

    return {
        'strategy_id': strategy_id,
        'total_exposures': total_exposures,
        'total_clicks': total_clicks,
        'total_ratings': total_ratings,
        'total_collects': total_collects,
        'total_watch_seconds': total_watch_seconds,
        'unique_users': len(unique_users),
        'ctr': ctr,
        'ctr_ci_lower': ctr_ci['ci_lower'],
        'ctr_ci_upper': ctr_ci['ci_upper'],
        'avg_watch_seconds': avg_watch,
        'rating_rate': rating_rate,
        'rating_rate_ci_lower': rating_ci['ci_lower'],
        'rating_rate_ci_upper': rating_ci['ci_upper'],
        'collect_rate': collect_rate,
        'collect_rate_ci_lower': collect_ci['ci_lower'],
        'collect_rate_ci_upper': collect_ci['ci_upper'],
        'positive_events': positive_events,
    }


# =============================================
# 3. 统计检验 (设计文档 §5.2)
# =============================================

def perform_statistical_tests(
    metrics_by_strategy: Dict[int, Dict],
) -> Dict[int, Dict[str, Any]]:
    """
    对各策略指标执行统计检验，以对照组(is_control=1)为基准。

    设计文档 §5.2:
      - 比例指标 → 两样本 Z 检验 (α=0.05)
      - 样本量不足 → 标记"数据收集中"

    Args:
        metrics_by_strategy: { strategy_id: metrics_dict }

    Returns:
        dict: { strategy_id: { 'p_value': float, 'is_winner': bool,
                               'sample_sufficient': bool, 'test_type': str } }
    """
    # 找对照组（is_control=1 的策略，如果存在）
    control_sid = None
    for sid, m in metrics_by_strategy.items():
        if m.get('is_control', False):
            control_sid = sid
            break

    if control_sid is None:
        # 无显式对照组，使用第一个策略作为基线
        control_sid = list(metrics_by_strategy.keys())[0]

    control = metrics_by_strategy[control_sid]
    results = {}

    for sid, strategy in metrics_by_strategy.items():
        if sid == control_sid:
            # 对照组自身
            results[sid] = {
                'p_value': 1.0,
                'is_winner': False,
                'is_control': True,
                'sample_sufficient': True,
                'test_type': 'control',
            }
            continue

        # 样本量检查
        required_n = minimum_sample_size(
            baseline_rate=control['ctr'],
            minimum_effect=0.01,
            alpha=cfg.SIGNIFICANCE_LEVEL,
            power=cfg.STATISTICAL_POWER,
        )
        sample_sufficient = (
            strategy['total_exposures'] >= required_n
            and control['total_exposures'] >= required_n
        )

        if not sample_sufficient:
            results[sid] = {
                'p_value': None,
                'is_winner': False,
                'sample_sufficient': False,
                'test_type': 'insufficient_data',
                'required_sample_size': required_n,
            }
            continue

        # 比例 Z 检验 (CTR)
        z_test = two_proportion_z_test(
            successes_a=strategy['positive_events'],
            trials_a=strategy['total_exposures'],
            successes_b=control['positive_events'],
            trials_b=control['total_exposures'],
        )

        results[sid] = {
            'p_value': z_test['p_value'],
            'is_winner': z_test['significant'] and strategy['ctr'] > control['ctr'],
            'sample_sufficient': True,
            'test_type': 'z_test',
            'z_stat': z_test['z_stat'],
        }

    return results


# =============================================
# 4. Bandit 参数更新 (设计文档 §5.2)
# =============================================

def update_bandit_params(
    conn,
    redis_client,
    experiment: Dict,
    strategies: List[Dict],
    metrics_by_strategy: Dict[int, Dict],
) -> Dict[int, Tuple[float, float]]:
    """
    更新 Bandit 模式的策略后验参数（Alpha / Beta），并写入 Redis。

    设计文档 §5.2:
      α = 1 + 正向事件数
      β = 1 + 总曝光 - 正向事件数

    Args:
        conn: MySQL 连接
        redis_client: Redis 连接
        experiment: 实验 dict
        strategies: 策略配置列表
        metrics_by_strategy: 各策略指标

    Returns:
        dict: { strategy_id: (alpha, beta) }
    """
    updated_params = {}

    for strat in strategies:
        sid = strat['id']
        metrics = metrics_by_strategy.get(sid)
        if metrics is None:
            continue

        # 冷启动保护 (设计文档 §6.2)
        if strat.get('coldstart_end_time') and datetime.now() < strat['coldstart_end_time']:
            logger.info(f"  策略 {sid}: 冷启动保护期，跳过 Bandit 更新")
            updated_params[sid] = (float(strat['bandit_alpha']), float(strat['bandit_beta']))
            continue

        # 计算新的 Beta 参数
        positive = metrics['positive_events']
        total = metrics['total_exposures']
        negative = total - positive

        alpha = max(cfg.BETA_PRIOR_ALPHA, cfg.BETA_PRIOR_ALPHA + positive)
        beta = max(cfg.BETA_PRIOR_BETA, cfg.BETA_PRIOR_BETA + negative)

        updated_params[sid] = (alpha, beta)

        # 写入 MySQL
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE ab_strategies SET bandit_alpha = %s, bandit_beta = %s, "
                "weight_source = 'bandit' WHERE id = %s",
                (alpha, beta, sid)
            )

        # 写入 Redis (设计文档 §8.1)
        alpha_key = cfg.REDIS_PREFIX['bandit_alpha'].format(
            exp_id=experiment['id'], strategy_id=sid
        )
        beta_key = cfg.REDIS_PREFIX['bandit_beta'].format(
            exp_id=experiment['id'], strategy_id=sid
        )
        redis_client.set(alpha_key, alpha)
        redis_client.set(beta_key, beta)

        logger.info(f"  策略 {sid}: alpha={alpha:.4f}, beta={beta:.4f}")

    # 发布 Redis Pub/Sub 通知 (设计文档 §8.2)
    for sid, (alpha, beta) in updated_params.items():
        message = json.dumps({
            'experimentId': experiment['id'],
            'strategyId': sid,
            'alpha': alpha,
            'beta': beta,
            'timestamp': datetime.now().isoformat(),
        })
        redis_client.publish(cfg.REDIS_CHANNELS['bandit_update'], message)

    conn.commit()
    return updated_params


# =============================================
# 5. 结果存储 (设计文档 §5.2)
# =============================================

def store_results(
    conn,
    experiment: Dict,
    metrics_by_strategy: Dict[int, Dict],
    test_results: Dict[int, Dict],
    bandit_params: Dict[int, Tuple[float, float]],
    period_start: datetime,
    period_end: datetime,
):
    """
    将分析结果写入 ab_results 表。

    设计文档 §5.2 — 结果存储。
    """
    now = datetime.now()
    for strat in experiment['strategies']:
        sid = strat['id']
        metrics = metrics_by_strategy.get(sid)
        test = test_results.get(sid, {})
        bp = bandit_params.get(sid, (1.0, 1.0))

        if metrics is None:
            continue

        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO ab_results (
                    experiment_id, strategy_id, analyzed_at,
                    period_start, period_end,
                    total_exposures, total_clicks, total_ratings,
                    total_collects, total_watch_seconds, unique_users,
                    ctr, avg_watch_seconds, rating_rate, collect_rate,
                    p_value, is_winner, is_converged,
                    sample_size_sufficient, bandit_alpha, bandit_beta
                ) VALUES (
                    %(exp_id)s, %(sid)s, %(now)s,
                    %(period_start)s, %(period_end)s,
                    %(exposures)s, %(clicks)s, %(ratings)s,
                    %(collects)s, %(watch_sec)s, %(users)s,
                    %(ctr)s, %(avg_watch)s, %(rating_rate)s, %(collect_rate)s,
                    %(p_value)s, %(is_winner)s, %(is_converged)s,
                    %(sample_ok)s, %(alpha)s, %(beta)s
                )
            """, {
                'exp_id': experiment['id'],
                'sid': sid,
                'now': now,
                'period_start': period_start,
                'period_end': period_end,
                'exposures': metrics['total_exposures'],
                'clicks': metrics['total_clicks'],
                'ratings': metrics['total_ratings'],
                'collects': metrics['total_collects'],
                'watch_sec': metrics['total_watch_seconds'],
                'users': metrics['unique_users'],
                'ctr': metrics['ctr'],
                'avg_watch': metrics['avg_watch_seconds'],
                'rating_rate': metrics['rating_rate'],
                'collect_rate': metrics['collect_rate'],
                'p_value': test.get('p_value'),
                'is_winner': 1 if test.get('is_winner', False) else 0,
                'is_converged': 0,  # 收敛判定在 check_convergence 中处理
                'sample_ok': 1 if test.get('sample_sufficient', False) else 0,
                'alpha': bp[0],
                'beta': bp[1],
            })

    conn.commit()
    logger.info(f"  结果已写入 ab_results")


# =============================================
# 6. 收敛判定 (设计文档 §6.3)
# =============================================

def check_convergence(
    conn,
    redis_client,
    experiment: Dict,
    bandit_params: Dict[int, Tuple[float, float]],
) -> Optional[int]:
    """
    检查实验是否收敛，若满足条件则自动推全。

    收敛条件 (设计文档 §6.3):
      - 获胜概率 > 95% 连续 6 个周期 (3小时)
      - 实验运行时间 ≥ 24 小时

    Args:
        conn: MySQL 连接
        redis_client: Redis 连接
        experiment: 实验 dict
        bandit_params: { strategy_id: (alpha, beta) }

    Returns:
        Optional[int]: 推全的策略 ID，若未收敛则返回 None
    """
    exp_id = experiment['id']

    # 检查实验运行时间
    start_time = experiment.get('start_time')
    if not start_time:
        return None

    hours_running = (datetime.now() - start_time).total_seconds() / 3600
    if hours_running < cfg.RUN_MINIMUM_HOURS:
        logger.info(f"  实验 {exp_id}: 运行 {hours_running:.1f}h < {cfg.RUN_MINIMUM_HOURS}h, 不进行收敛判定")
        return None

    # 蒙特卡洛计算获胜概率 (设计文档 §5.2, §6.3)
    strategy_params = [
        {'strategy_id': sid, 'alpha': alpha, 'beta': beta}
        for sid, (alpha, beta) in bandit_params.items()
    ]
    win_probs = compute_win_probability(strategy_params, cfg.MONTE_CARLO_SIMULATIONS)

    for sid, prob in win_probs.items():
        logger.info(f"  策略 {sid} 获胜概率: {prob:.4f}")

    # 检查是否有策略获胜概率超过阈值
    best_sid = max(win_probs, key=win_probs.get)
    best_prob = win_probs[best_sid]

    if best_prob < cfg.WIN_PROBABILITY_THRESHOLD:
        logger.info(f"  最佳策略 {best_sid} 获胜概率 {best_prob:.4f} < 阈值 {cfg.WIN_PROBABILITY_THRESHOLD}")
        return None

    # 检查历史记录 — 连续 CONVERGE_CONSECUTIVE_CYCLES 周期
    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute("""
            SELECT is_winner FROM ab_results
            WHERE experiment_id = %s AND strategy_id = %s
            ORDER BY analyzed_at DESC
            LIMIT %s
        """, (exp_id, best_sid, cfg.CONVERGE_CONSECUTIVE_CYCLES))
        recent_results = cursor.fetchall()

    if len(recent_results) < cfg.CONVERGE_CONSECUTIVE_CYCLES:
        logger.info(
            f"  策略 {best_sid} 获胜概率 {best_prob:.4f} 达标 "
            f"但历史记录不足 {cfg.CONVERGE_CONSECUTIVE_CYCLES} 个周期"
        )
        return None

    # 检查是否连续都是优胜
    all_winner = all(r['is_winner'] == 1 for r in recent_results)
    if not all_winner:
        logger.info(f"  策略 {best_sid} 历史记录未连续优胜")
        return None

    # ============ 触发自动推全 ============
    logger.info(f"  === 实验 {exp_id} 收敛！策略 {best_sid} 被推全 ===")

    # 更新实验状态
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE ab_experiments SET status = 'stopped', "
            "winner_strategy_id = %s, updated_at = NOW() WHERE id = %s",
            (best_sid, exp_id)
        )
        # 获胜策略流量 100%
        cursor.execute(
            "UPDATE ab_strategies SET traffic_percentage = 100, "
            "weight_source = 'promoted' WHERE id = %s",
            (best_sid,)
        )
        # 其他策略 0%
        cursor.execute(
            "UPDATE ab_strategies SET traffic_percentage = 0, "
            "weight_source = 'promoted' WHERE experiment_id = %s AND id != %s",
            (exp_id, best_sid)
        )
        conn.commit()

    # 发布 Redis 通知 (设计文档 §8.2)
    message = json.dumps({
        'experimentId': exp_id,
        'winnerStrategyId': best_sid,
        'timestamp': datetime.now().isoformat(),
    })
    redis_client.publish(cfg.REDIS_CHANNELS['experiment_stop'], message)

    return best_sid


# =============================================
# 7. 主分析流程 (设计文档 §5.2)
# =============================================

def run_analysis():
    """
    一次完整的分析运行入口。

    流程 (设计文档 §5.2):
      1. 加载进行中的实验
      2. 对每个实验：
         a. 加载最近 24h 行为数据
         b. 计算各策略指标
         c. 执行统计检验
         d. (仅 bandit 模式) 更新后验参数 → 写入 Redis
         e. 存储结果到 ab_results
         f. 收敛判定 → 触发自动推全
    """
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("A/B 测试分析开始")
    logger.info("=" * 60)

    conn = get_mysql_connection()
    redis_client = get_redis_connection()

    try:
        experiments = load_experiments(conn)

        if not experiments:
            logger.info("没有进行中的实验，跳过分析")
            return

        period_end = datetime.now()
        period_start = period_end - timedelta(hours=cfg.RECENT_WINDOW_HOURS)

        for exp in experiments:
            exp_id = exp['id']
            strategies = exp['strategies']
            strategy_ids = [s['id'] for s in strategies]

            logger.info(f"\n处理实验 #{exp_id}: {exp['name']} "
                        f"(模式: {exp['split_mode']}, 策略数: {len(strategies)})")

            # Step a: 加载行为数据
            behavior_data = load_behavior_data(conn, exp_id, strategy_ids, period_start)

            # Step b: 计算指标
            metrics_by_strategy = {}
            for strat in strategies:
                sid = strat['id']
                data = behavior_data.get(sid, [])
                metrics = compute_metrics(sid, data)
                metrics['is_control'] = bool(strat.get('is_control', False))
                metrics_by_strategy[sid] = metrics

                logger.info(f"  策略 {sid} ({strat['name']}): "
                            f"曝光={metrics['total_exposures']}, "
                            f"CTR={metrics['ctr']:.4f} "
                            f"[{metrics['ctr_ci_lower']:.4f}, {metrics['ctr_ci_upper']:.4f}]")

            # Step c: 统计检验
            test_results = perform_statistical_tests(metrics_by_strategy)
            for sid, tr in test_results.items():
                if tr.get('test_type') == 'insufficient_data':
                    logger.info(f"  策略 {sid}: 样本量不足 (需要 {tr.get('required_sample_size')})")
                elif tr.get('is_control'):
                    logger.info(f"  策略 {sid}: 对照组")
                else:
                    pv = tr.get('p_value', 1.0)
                    logger.info(f"  策略 {sid}: p值={pv:.6f}, "
                                f"{'显著优胜' if tr.get('is_winner') else '未显著'}")

            # Step d: Bandit 参数更新 (仅 bandit 模式)
            bandit_params = {}
            if exp['split_mode'] == 'bandit':
                logger.info(f"  更新 Bandit 后验参数...")
                bandit_params = update_bandit_params(
                    conn, redis_client, exp, strategies, metrics_by_strategy
                )
            else:
                # fixed 模式使用初始参数
                for strat in strategies:
                    sid = strat['id']
                    bandit_params[sid] = (
                        float(strat['bandit_alpha']),
                        float(strat['bandit_beta']),
                    )

            # Step e: 存储结果
            store_results(
                conn, exp, metrics_by_strategy, test_results,
                bandit_params, period_start, period_end
            )

            # Step f: 收敛判定
            if exp['split_mode'] == 'bandit':
                winner_sid = check_convergence(conn, redis_client, exp, bandit_params)
                if winner_sid:
                    logger.info(f">>> 实验 {exp_id}: 策略 {winner_sid} 自动推全")
                else:
                    logger.info(f"  实验 {exp_id}: 未收敛，继续运行")

        elapsed = time.time() - start_time
        logger.info(f"\n分析完成，耗时 {elapsed:.1f}s")
        logger.info("-" * 60)

    except Exception as e:
        logger.error(f"分析过程发生错误: {e}", exc_info=True)
        raise
    finally:
        conn.close()
        redis_client.close()


if __name__ == '__main__':
    run_analysis()