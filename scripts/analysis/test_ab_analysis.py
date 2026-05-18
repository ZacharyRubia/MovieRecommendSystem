"""ab_analysis.py 单元测试（mock 模式，不依赖 MySQL/Redis）"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import json
import math
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta

# 在 import ab_analysis 之前 mock 掉 pymysql 和 redis
sys.modules['pymysql'] = MagicMock()
sys.modules['pymysql.cursors'] = MagicMock()
sys.modules['redis'] = MagicMock()

from ab_analysis import (
    compute_metrics, perform_statistical_tests,
    update_bandit_params, store_results,
    check_convergence,
)
import stat_utils  # 真实导入（不依赖 MySQL/Redis）

pass_count = 0
fail_count = 0

def check(name, ok, detail=""):
    global pass_count, fail_count
    if ok:
        print(f"  ✓ {name}" + (f"  ({detail})" if detail else ""))
        pass_count += 1
    else:
        print(f"  ✗ {name}  {detail}")
        fail_count += 1

# =============================================
# 1. compute_metrics — 纯函数，无 mock 需测试
# =============================================
print("\n=== compute_metrics ===")

# 空数据
m = compute_metrics(1, [])
check("空数据: total_exposures=0", m['total_exposures'] == 0)
check("空数据: ctr=0", m['ctr'] == 0.0)

# 混合行为数据
behaviors = [
    {'behavior_type': 'click', 'user_id': 1},
    {'behavior_type': 'rate', 'rating_value': 5, 'user_id': 1},
    {'behavior_type': 'favorite', 'user_id': 2},
    {'behavior_type': 'play', 'watch_seconds': 120, 'user_id': 3},
    {'behavior_type': 'rate', 'rating_value': 2, 'user_id': 4},
    {'behavior_type': 'click', 'user_id': 5},
]
m = compute_metrics(1, behaviors)
check("混合数据: total_exposures=6", m['total_exposures'] == 6)
check("混合数据: total_clicks=2", m['total_clicks'] == 2)
check("混合数据: total_ratings=2", m['total_ratings'] == 2)
check("混合数据: total_collects=1", m['total_collects'] == 1)
check("混合数据: total_watch_seconds=120", m['total_watch_seconds'] == 120)
check("混合数据: unique_users=5", m['unique_users'] == 5)
# 正向: click(2) + rate≥4(1) + favorite(1) + play>30s(1) = 5
check("混合数据: positive_events=5", m['positive_events'] == 5)
check("混合数据: ctr=5/6≈0.8333", abs(m['ctr'] - 5/6) < 0.001)
check("混合数据: CI区间有效", m['ctr_ci_lower'] < m['ctr'] < m['ctr_ci_upper'])
check("混合数据: collect_rate=1/6≈0.1667", abs(m['collect_rate'] - 1/6) < 0.001)
check("混合数据: avg_watch_seconds=24", abs(m['avg_watch_seconds'] - 24) < 0.01)  # 120/5=24

# =============================================
# 2. perform_statistical_tests — 真实 stat_utils，仅 mock pymysql
# =============================================
print("\n=== perform_statistical_tests ===")

# 需要足够大的样本量使 minimum_sample_size 通过
# baseline_rate=0.20, minimum_effect=0.01 → ~39240 样本 → 用 50000
metrics = {
    1: {'strategy_id': 1, 'total_exposures': 50000, 'positive_events': 10000,
        'ctr': 0.20, 'is_control': True},
    2: {'strategy_id': 2, 'total_exposures': 50000, 'positive_events': 12500,
        'ctr': 0.25, 'is_control': False},
}
results = perform_statistical_tests(metrics)
check("策略1(对照组): is_control=True", results[1]['is_control'])
check("策略2: 样本充足", results[2]['sample_sufficient'] == True,
      f"sufficient={results[2].get('sample_sufficient')}")
check("策略2: p_value 存在", results[2].get('p_value') is not None,
      f"p={results[2].get('p_value')}")
check("策略2(CTR更高): is_winner=True", results[2]['is_winner'] == True,
      f"is_winner={results[2]['is_winner']}")

# 样本量不足场景（小样本 + baseline_rate=0 触发 default）
metrics_small = {
    1: {'strategy_id': 1, 'total_exposures': 10, 'positive_events': 2,
        'ctr': 0.20, 'is_control': True},
    2: {'strategy_id': 2, 'total_exposures': 10, 'positive_events': 3,
        'ctr': 0.30, 'is_control': False},
}
results_small = perform_statistical_tests(metrics_small)
check("样本不足: 标记insufficient_data",
      results_small[2]['test_type'] == 'insufficient_data')

# =============================================
# 3. update_bandit_params — mock MySQL/Redis
# =============================================
print("\n=== update_bandit_params ===")

mock_conn = MagicMock()
mock_redis = MagicMock()
mock_cursor = MagicMock()
mock_conn.cursor.return_value = mock_cursor

experiment = {'id': 1, 'name': 'test'}
strategies = [
    {'id': 1, 'bandit_alpha': 1.0, 'bandit_beta': 1.0,
     'coldstart_end_time': None, 'weight_source': 'fixed'},
    {'id': 2, 'bandit_alpha': 1.0, 'bandit_beta': 1.0,
     'coldstart_end_time': None, 'weight_source': 'fixed'},
]
metrics_bandit = {
    1: {'positive_events': 30, 'total_exposures': 100},
    2: {'positive_events': 50, 'total_exposures': 100},
}
params = update_bandit_params(mock_conn, mock_redis, experiment, strategies, metrics_bandit)
check("update: 返回2个策略", len(params) == 2)
check("update: 策略1 alpha=31", abs(params[1][0] - 31) < 0.1)
check("update: 策略1 beta=71", abs(params[1][1] - 71) < 0.1)
check("update: 策略2 alpha=51", abs(params[2][0] - 51) < 0.1)
check("update: 策略2 beta=51", abs(params[2][1] - 51) < 0.1)
check("update: Redis set 被调用", mock_redis.set.call_count >= len(strategies) * 2,
      f"set_call_count={mock_redis.set.call_count}")
check("update: Redis publish 被调用", mock_redis.publish.call_count > 0,
      f"publish_call_count={mock_redis.publish.call_count}")

# =============================================
# 4. store_results — mock MySQL
# =============================================
print("\n=== store_results ===")

mock_conn2 = MagicMock()
mock_cursor2 = MagicMock()
mock_conn2.cursor.return_value = mock_cursor2
mock_cursor2.__enter__.return_value = mock_cursor2

exp_store = {
    'id': 1, 'name': 'test',
    'strategies': [{'id': 1}, {'id': 2}]
}
metrics_store = {
    1: {'total_exposures': 100, 'total_clicks': 20, 'total_ratings': 10,
        'total_collects': 5, 'total_watch_seconds': 3600, 'unique_users': 50,
        'ctr': 0.2, 'avg_watch_seconds': 72.0, 'rating_rate': 0.1, 'collect_rate': 0.05},
    2: {'total_exposures': 200, 'total_clicks': 40, 'total_ratings': 20,
        'total_collects': 10, 'total_watch_seconds': 7200, 'unique_users': 100,
        'ctr': 0.2, 'avg_watch_seconds': 72.0, 'rating_rate': 0.1, 'collect_rate': 0.05},
}
test_results_store = {
    1: {'p_value': 1.0, 'is_winner': False, 'sample_sufficient': True},
    2: {'p_value': 0.03, 'is_winner': True, 'sample_sufficient': True},
}
bandit_params_store = {1: (1.0, 1.0), 2: (31.0, 71.0)}

store_results(mock_conn2, exp_store, metrics_store, test_results_store,
              bandit_params_store, datetime.now(), datetime.now())
check("store: execute 被调用 (每策略至少1次)",
      mock_cursor2.execute.call_count >= 2,
      f"mock_cursor2.execute.call_count={mock_cursor2.execute.call_count}")
check("store: commit 被调用", mock_conn2.commit.called)

# =============================================
# 5. check_convergence — mock MySQL/Redis
# =============================================
print("\n=== check_convergence ===")

mock_conn3 = MagicMock()
mock_redis3 = MagicMock()
mock_cursor3 = MagicMock()
# 模拟最近 6 个周期都是 winner
mock_cursor3.fetchall.return_value = [{'is_winner': 1}] * 6
mock_conn3.cursor.return_value = mock_cursor3
mock_cursor3.__enter__.return_value = mock_cursor3

exp_converge = {
    'id': 2, 'name': 'converge_test', 'split_mode': 'bandit',
    'start_time': datetime.now() - timedelta(hours=48),
}
bandit_params_converge = {
    1: (5, 10),   # α=5, β=10 → CTR≈0.33
    2: (50, 10),  # α=50, β=10 → CTR≈0.83
}

winner = check_convergence(mock_conn3, mock_redis3, exp_converge, bandit_params_converge)
check("收敛: 应返回策略2(CTR更高)", winner == 2, f"winner={winner}")

# 运行时间不足
exp_short = {
    'id': 3, 'name': 'short_test', 'split_mode': 'bandit',
    'start_time': datetime.now() - timedelta(hours=2),
}
winner_short = check_convergence(mock_conn3, mock_redis3, exp_short, bandit_params_converge)
check("运行<24h: 不收敛", winner_short is None, f"winner={winner_short}")

# =============================================
# 6. 汇总
# =============================================
print(f"\n=== 总计: {pass_count} 通过, {fail_count} 失败 ===")
sys.exit(0 if fail_count == 0 else 1)