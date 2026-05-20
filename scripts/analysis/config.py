"""
config.py - A/B 测试分析模块配置文件

覆盖设计文档第 5 节要求：
- MySQL 数据库连接配置
- Redis 连接配置
- 分析参数（时间窗口、显著性水平、统计功效等）
"""

# =============================================
# MySQL 数据库配置
# =============================================
MYSQL_CONFIG = {
    'host': '192.168.1.38',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4',
    'connect_timeout': 10,
    'read_timeout': 60,
}

# =============================================
# Redis 配置
# =============================================
REDIS_CONFIG = {
    'host': '192.168.1.39',
    'port': 6379,
    'db': 0,
    'decode_responses': True,
    'socket_timeout': 5,
}

# =============================================
# 分析周期参数 (设计文档 §5.1, §5.2)
# =============================================
ANALYSIS_INTERVAL_MINUTES = 30       # 分析运行间隔（每 30 分钟）
RECENT_WINDOW_HOURS = 24             # 最近行为数据窗口（24h）
COLD_START_HOURS = 2                 # 冷启动保护期（2h，不参与 Bandit 调整）
RUN_MINIMUM_HOURS = 24               # 实验最少运行时间（24h 才允许自动终止）

# =============================================
# 统计检验参数 (设计文档 §5.2)
# =============================================
SIGNIFICANCE_LEVEL = 0.05            # 显著性水平 α = 0.05
STATISTICAL_POWER = 0.8              # 统计功效 1-β = 0.8
MIN_SAMPLE_SIZE_PER_STRATEGY = 100   # 每组最小样本量

# =============================================
# 收敛判定参数 (设计文档 §6.3)
# =============================================
WIN_PROBABILITY_THRESHOLD = 0.95     # 获胜概率阈值 95%
CONVERGE_CONSECUTIVE_CYCLES = 6      # 连续达标周期数（6 × 30min = 3h）
MONTE_CARLO_SIMULATIONS = 10000      # 蒙特卡洛模拟次数

# =============================================
# Bandit 参数 (设计文档 §5.2, §6.1)
# =============================================
BETA_PRIOR_ALPHA = 1.0               # Beta 先验 α = 1
BETA_PRIOR_BETA = 1.0                # Beta 先验 β = 1
BATCH_WINDOW_MINUTES = 10            # 分批采样窗口

# =============================================
# 正向事件定义 (设计文档 §5.2)
# =============================================
POSITIVE_EVENT_TYPES = ('view', 'like', 'rate', 'collect', 'share')
# rate 行为中，评分 ≥ 4 分才算正向事件
POSITIVE_RATING_THRESHOLD = 4

# =============================================
# Redis Key 前缀 (设计文档 §8.1)
# =============================================
REDIS_PREFIX = {
    'bandit_alpha': 'ab:bandit:{exp_id}:{strategy_id}:alpha',
    'bandit_beta': 'ab:bandit:{exp_id}:{strategy_id}:beta',
    'batch_cache': 'ab:batch:{exp_id}:{timestamp}',
    'override_cache': 'ab:override:{exp_id}:{user_id}',
    'experiment_cache': 'ab:experiment:{exp_id}',
}

# =============================================
# Redis Pub/Sub 通道 (设计文档 §8.2)
# =============================================
REDIS_CHANNELS = {
    'bandit_update': 'ab:bandit:update',
    'experiment_stop': 'ab:experiment:stop',
    'experiment_update': 'ab:experiment:update',
}

# =============================================
# 监控告警阈值 (设计文档 §9)
# =============================================
ALERT_THRESHOLDS = {
    'ctr_drop_ratio': 0.5,           # CTR 断崖下降 50%
    'latency_p99_ms': 500,            # P99 延迟超过 500ms
    'bucket_anomaly_rate': 0.001,     # 分桶异常率超过 0.1%
    'data_loss_rate': 0.01,           # 埋点丢失率超过 1%
}

# =============================================
# 分桶参数
# =============================================
TOTAL_BUCKETS = 100                  # 总桶数 0~99