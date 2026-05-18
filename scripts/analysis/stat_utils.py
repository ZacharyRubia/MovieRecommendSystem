"""
stat_utils.py - A/B 测试统计工具函数

覆盖设计文档 §5.3 要求：
- compute_proportion_ci() — Wilson Score 置信区间
- two_proportion_z_test() — 两样本 Z 检验
- mean_comparison_test() — t 检验 / Mann-Whitney U 检验
- minimum_sample_size() — 样本量估算
- compute_win_probability() — 蒙特卡洛获胜概率
"""

import math
import random
from typing import Tuple, Optional, Dict, Any

from scipy import stats as scipy_stats
import numpy as np


# =============================================
# 1. 比例置信区间 (Wilson Score)
# =============================================

def compute_proportion_ci(
    successes: int,
    trials: int,
    confidence: float = 0.95
) -> Dict[str, float]:
    """
    使用 Wilson Score 方法计算比例置信区间。

    设计文档 §5.2 — 比例指标（CTR 等）置信区间计算。

    Args:
        successes: 正向事件数
        trials: 总试验数
        confidence: 置信水平（默认 0.95）

    Returns:
        dict: { 'proportion': float, 'ci_lower': float, 'ci_upper': float }
    """
    if trials <= 0:
        return {'proportion': 0.0, 'ci_lower': 0.0, 'ci_upper': 0.0}

    proportion = successes / trials
    z = scipy_stats.norm.ppf(1 - (1 - confidence) / 2)

    denominator = 1 + z**2 / trials
    center = (proportion + z**2 / (2 * trials)) / denominator
    margin = z * math.sqrt(
        (proportion * (1 - proportion) / trials) +
        (z**2 / (4 * trials**2))
    ) / denominator

    return {
        'proportion': proportion,
        'ci_lower': max(0.0, center - margin),
        'ci_upper': min(1.0, center + margin),
    }


# =============================================
# 2. 两样本 Z 检验
# =============================================

def two_proportion_z_test(
    successes_a: int,
    trials_a: int,
    successes_b: int,
    trials_b: int
) -> Dict[str, Any]:
    """
    两样本比例 Z 检验（双尾）。

    设计文档 §5.2 — 比较各策略间 CTR、转化率等比例指标。

    Args:
        successes_a: A 组正向事件数
        trials_a: A 组总试验数
        successes_b: B 组正向事件数
        trials_b: B 组总试验数

    Returns:
        dict: { 'z_stat': float, 'p_value': float, 'significant': bool }
    """
    if trials_a <= 0 or trials_b <= 0:
        return {'z_stat': 0.0, 'p_value': 1.0, 'significant': False}

    p_a = successes_a / trials_a
    p_b = successes_b / trials_b
    p_pool = (successes_a + successes_b) / (trials_a + trials_b)

    # 防止除零
    if p_pool * (1 - p_pool) == 0:
        return {'z_stat': 0.0, 'p_value': 1.0, 'significant': False}

    se = math.sqrt(p_pool * (1 - p_pool) * (1 / trials_a + 1 / trials_b))
    if se == 0:
        return {'z_stat': 0.0, 'p_value': 1.0, 'significant': False}

    z_stat = (p_a - p_b) / se
    p_value = 2 * (1 - scipy_stats.norm.cdf(abs(z_stat)))

    return {
        'z_stat': z_stat,
        'p_value': p_value,
        'significant': p_value < 0.05,
    }


# =============================================
# 3. 均值比较检验
# =============================================

def mean_comparison_test(
    values_a: list,
    values_b: list,
    use_nonparametric: bool = False
) -> Dict[str, Any]:
    """
    均值比较：独立样本 t 检验 或 Mann-Whitney U 检验。

    设计文档 §5.2 — 比较各策略间人均观看时长等均值指标。
    当样本量较小或分布偏态时，推荐使用非参数 Mann-Whitney U 检验。

    Args:
        values_a: A 组数值列表
        values_b: B 组数值列表
        use_nonparametric: 是否使用 Mann-Whitney U 检验（默认 False，使用 t 检验）

    Returns:
        dict: 包含统计量、p 值和显著性标记
    """
    if len(values_a) < 2 or len(values_b) < 2:
        return {'statistic': 0.0, 'p_value': 1.0, 'significant': False, 'method': 'insufficient_data'}

    if use_nonparametric:
        # Mann-Whitney U 检验 (非参数)
        stat, p_value = scipy_stats.mannwhitneyu(values_a, values_b, alternative='two-sided')
        method = 'mann_whitney_u'
    else:
        # 独立样本 t 检验
        stat, p_value = scipy_stats.ttest_ind(values_a, values_b, equal_var=False)
        method = 'welch_t_test'

    return {
        'statistic': stat,
        'p_value': p_value,
        'significant': p_value < 0.05,
        'method': method,
        'mean_a': float(np.mean(values_a)),
        'mean_b': float(np.mean(values_b)),
        'std_a': float(np.std(values_a, ddof=1)),
        'std_b': float(np.std(values_b, ddof=1)),
        'n_a': len(values_a),
        'n_b': len(values_b),
    }


# =============================================
# 4. 最小样本量估算
# =============================================

def minimum_sample_size(
    baseline_rate: float,
    minimum_effect: float,
    alpha: float = 0.05,
    power: float = 0.8
) -> int:
    """
    估算比例检验所需的最小样本量。

    设计文档 §5.2 — 样本量不足时标记"数据收集中"。
    基于两样本比例 Z 检验的近似公式。

    Args:
        baseline_rate: 基线比例（对照组的预期 CTR）
        minimum_effect: 最小可检测效应（绝对差值）
        alpha: 显著性水平（默认 0.05）
        power: 统计功效（默认 0.8）

    Returns:
        int: 每组所需最小样本量
    """
    if baseline_rate <= 0 or minimum_effect <= 0:
        return 1

    z_alpha = scipy_stats.norm.ppf(1 - alpha / 2)
    z_beta = scipy_stats.norm.ppf(power)

    p_avg = baseline_rate + minimum_effect / 2
    variance = p_avg * (1 - p_avg)

    n = int(math.ceil(
        2 * variance * (z_alpha + z_beta)**2 / minimum_effect**2
    ))
    return max(n, 2)  # 至少 2


# =============================================
# 5. 获胜概率 (蒙特卡洛模拟)
# =============================================

def compute_win_probability(
    strategy_params: list,
    simulations: int = 10000
) -> Dict[int, float]:
    """
    通过蒙特卡洛 Beta 采样计算各策略的获胜概率。

    设计文档 §5.2, §6.3 — 收敛判定：获胜概率 > 95% 连续 6 周期触发自动推全。

    Args:
        strategy_params: 列表，每个元素为 dict { 'strategy_id': int, 'alpha': float, 'beta': float }
        simulations: 模拟次数（默认 10000）

    Returns:
        dict: { strategy_id: win_probability (0~1) }
    """
    if not strategy_params:
        return {}

    strategy_ids = [s['strategy_id'] for s in strategy_params]
    wins = {sid: 0 for sid in strategy_ids}

    for _ in range(simulations):
        samples = []
        for s in strategy_params:
            theta = random.betavariate(s['alpha'], s['beta'])
            samples.append((s['strategy_id'], theta))

        # 找出本次模拟的获胜者（采样值最大）
        winner = max(samples, key=lambda x: x[1])[0]
        wins[winner] += 1

    return {
        sid: count / simulations
        for sid, count in wins.items()
    }