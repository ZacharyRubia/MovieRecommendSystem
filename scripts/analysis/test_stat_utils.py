"""stat_utils.py 单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from stat_utils import (
    two_proportion_z_test,
    mean_comparison_test,
    compute_proportion_ci,
    compute_win_probability,
    minimum_sample_size,
)

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
# 1. two_proportion_z_test (successes/trials integers)
# =============================================
r = two_proportion_z_test(3, 10, 6, 10)
check("Z测试: 返回dict含z_stat/p_value/significant",
      all(k in r for k in ('z_stat','p_value','significant')), f"keys={list(r.keys())}")
check("Z测试: z_stat为float", isinstance(r['z_stat'], float), f"z={r['z_stat']:.4f}")
check("Z测试: 3/10 vs 6/10 p<0.3 (有差异)", r['p_value'] < 0.3, f"p={r['p_value']:.4f}")

# 相同比例应 z≈0, p≈1
r2 = two_proportion_z_test(5, 10, 5, 10)
check("Z测试(相同): z≈0", abs(r2['z_stat']) < 1e-10, f"z={r2['z_stat']:.10f}")
check("Z测试(相同): p≈1", abs(r2['p_value']-1) < 1e-10, f"p={r2['p_value']:.10f}")

# 边界: 零试验
r3 = two_proportion_z_test(0, 0, 0, 10)
check("Z测试(trials_a=0): p=1", abs(r3['p_value']-1)<1e-10, f"p={r3['p_value']}")

# 边界: 全零比例
r4 = two_proportion_z_test(0, 10, 0, 10)
check("Z测试(0/10 vs 0/10): p≈1", abs(r4['p_value']-1)<1e-10, f"p={r4['p_value']}")

# =============================================
# 2. mean_comparison_test (list of values)
# =============================================
np.random.seed(42)
a = list(np.random.normal(5, 1, 100))
b = list(np.random.normal(6, 1, 100))
r = mean_comparison_test(a, b)
check("均值检验: 返回dict含statistic/p_value/significant/method",
      all(k in r for k in ('statistic','p_value','significant','method')), f"keys={list(r.keys())}")
check("均值检验(5 vs 6): 显著差异", r['significant'] == True, f"p={r['p_value']:.4f}")
check("均值检验: method=welch_t_test", r['method'] == 'welch_t_test', f"method={r['method']}")

r2 = mean_comparison_test(a, a)
check("均值检验(相同): p≈1", abs(r2['p_value']-1) < 0.01, f"p={r2['p_value']:.4f}")

# 小样本 n<30 且 use_nonparametric=True
small_a = list(np.random.normal(5, 1, 10))
small_b = list(np.random.normal(6, 1, 10))
r3 = mean_comparison_test(small_a, small_b, use_nonparametric=True)
check("非参数检验: method=mann_whitney_u", r3['method'] == 'mann_whitney_u', f"method={r3['method']}")

# 不足2个样本
r4 = mean_comparison_test([1.0], [2.0])
check("均值检验(n<2): method=insufficient_data", r4['method'] == 'insufficient_data', f"method={r4['method']}")
check("均值检验(n<2): p=1", abs(r4['p_value']-1)<1e-10, f"p={r4['p_value']}")

# =============================================
# 3. compute_proportion_ci (successes/trials integers)
# =============================================
r = compute_proportion_ci(50, 100)
check("CI: 返回dict含proportion/ci_lower/ci_upper",
      all(k in r for k in ('proportion','ci_lower','ci_upper')), f"keys={list(r.keys())}")
check("CI: proportion=0.5", abs(r['proportion']-0.5)<0.01, f"proportion={r['proportion']:.4f}")
check("CI: ci_lower < 0.5 < ci_upper", r['ci_lower'] < 0.5 < r['ci_upper'],
      f"CI=[{r['ci_lower']:.4f},{r['ci_upper']:.4f}]")
check("CI: ci_lower > 0", r['ci_lower'] > 0, f"ci_lower={r['ci_lower']:.4f}")

# 边界: 零试验
r2 = compute_proportion_ci(0, 0)
check("CI(0/0): proportion=0", abs(r2['proportion'])<1e-10, f"proportion={r2['proportion']}")

# 边界: 100%
r3 = compute_proportion_ci(10, 10)
check("CI(10/10): proportion=1", abs(r3['proportion']-1)<1e-10, f"proportion={r3['proportion']}")

# =============================================
# 4. compute_win_probability (list of dicts)
# =============================================
params = [
    {'strategy_id': 101, 'alpha': 10, 'beta': 20},
    {'strategy_id': 102, 'alpha': 15, 'beta': 10},
    {'strategy_id': 103, 'alpha': 5, 'beta': 30},
]
wp = compute_win_probability(params, simulations=5000)
check("获胜概率: 返回dict", isinstance(wp, dict), f"type={type(wp)}")
check("获胜概率: 含3个策略ID", sorted(wp.keys()) == [101,102,103], f"keys={sorted(wp.keys())}")
check("获胜概率: 和≈1", abs(sum(wp.values())-1.0)<0.05, f"sum={sum(wp.values()):.4f}")
check("获胜概率: 102(α=15) > 101(α=10) > 103(α=5)", wp[102] > wp[101] > wp[103],
      f"probs={wp}")

# 空列表
wp2 = compute_win_probability([])
check("获胜概率(空): 返回空dict", wp2 == {}, f"result={wp2}")

# =============================================
# 5. minimum_sample_size
# =============================================
n = minimum_sample_size(0.1, 0.12, 0.05, 0.8)
check("最小样本量: 返回正数", n > 0, f"n={n}")
check("最小样本量: 返回int", isinstance(n, int), f"type={type(n)}")
n2 = minimum_sample_size(0.5, 0.55)
check("最小样本量(默认参数): 返回正数", n2 > 0, f"n={n2}")

# 边界: 小效应-> 大样本量
n3 = minimum_sample_size(0.5, 0.01)
check("最小样本量(0.5→0.51差1%): 大样本量", n3 > 5000, f"n={n3}")

# =============================================
# 6. 汇总
# =============================================
print(f"\n=== 总计: {pass_count} 通过, {fail_count} 失败 ===")
sys.exit(0 if fail_count == 0 else 1)