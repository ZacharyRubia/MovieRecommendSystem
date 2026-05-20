-- ============================================
-- 1. 创建数据库
-- ============================================
CREATE DATABASE IF NOT EXISTS `MovieRecommendSystem`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `MovieRecommendSystem`;

-- ============================================
-- 2. 角色字典表 (roles)
-- ============================================
CREATE TABLE IF NOT EXISTS `roles` (
    `id` TINYINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，角色ID',
    `name` VARCHAR(20) UNIQUE NOT NULL COMMENT '角色名称（英文标识）',
    `display_name` VARCHAR(20) NOT NULL COMMENT '角色显示名称',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色字典表';

INSERT IGNORE INTO `roles` (`id`, `name`, `display_name`) VALUES
(1, 'admin', '管理员'),
(2, 'user', '普通用户');

-- ============================================
-- 3. 用户表 (users)
-- ============================================
CREATE TABLE IF NOT EXISTS `users` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，用户ID',
    `username` VARCHAR(50) UNIQUE NOT NULL COMMENT '用户名',
    `email` VARCHAR(100) UNIQUE COMMENT '邮箱',
    `password_hash` VARCHAR(255) NOT NULL COMMENT '加密后的密码',
    `role_id` TINYINT UNSIGNED NOT NULL DEFAULT 2 COMMENT '角色ID',
    `avatar_url` VARCHAR(500) DEFAULT '' COMMENT '头像OSS地址',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    FOREIGN KEY (`role_id`) REFERENCES `roles`(`id`),   -- 引用 roles 表
    INDEX `idx_role_id` (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- ============================================
-- 4. 标签表 (tags)
-- ============================================
CREATE TABLE IF NOT EXISTS `tags` (
    `id` INT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，标签ID',
    `name` VARCHAR(50) UNIQUE NOT NULL COMMENT '标签名称',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标签表';

-- ============================================
-- 5. 导演表 (directors)
-- ============================================
CREATE TABLE IF NOT EXISTS `directors` (
    `id` INT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，导演ID',
    `name` VARCHAR(100) UNIQUE NOT NULL COMMENT '导演姓名',
    `avatar_url` VARCHAR(500) DEFAULT '' COMMENT '导演头像/照片OSS地址',
    `description` TEXT COMMENT '导演简介',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='导演表';

-- ============================================
-- 6. 演员表 (actors)
-- ============================================
CREATE TABLE IF NOT EXISTS `actors` (
    `id` INT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，演员ID',
    `name` VARCHAR(100) UNIQUE NOT NULL COMMENT '演员姓名',
    `avatar_url` VARCHAR(500) DEFAULT '' COMMENT '演员头像/照片OSS地址',
    `description` TEXT COMMENT '演员简介',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='演员表';

-- ============================================
-- 7. 电影类型字典表 (genres)
-- ============================================
CREATE TABLE IF NOT EXISTS `genres` (
    `id` TINYINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，类型ID',
    `name` VARCHAR(20) UNIQUE NOT NULL COMMENT '类型名称（中文）',
    `code` VARCHAR(20) UNIQUE NOT NULL COMMENT '类型代码（英文）',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电影类型字典表';

-- ============================================
-- 8. 电影信息表 (movies)
-- ============================================
CREATE TABLE IF NOT EXISTS `movies` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，电影ID',
    `title` VARCHAR(200) NOT NULL COMMENT '电影标题',
    `description` TEXT COMMENT '电影简介',
    `cover_url` VARCHAR(500) COMMENT '封面图OSS地址',
    `video_url` VARCHAR(500) COMMENT '视频资源m3u8索引文件OSS地址',
    `release_year` YEAR COMMENT '上映年份',
    `duration` INT COMMENT '片长（秒）',
    `avg_rating` DECIMAL(3,2) DEFAULT 0.00 COMMENT '平均评分',
    `vector_synced_at` TIMESTAMP NULL DEFAULT NULL COMMENT '向量最后同步至Qdrant的时间',
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX `idx_release_year` (`release_year`),
    INDEX `idx_avg_rating` (`avg_rating`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电影信息表';

-- ============================================
-- 9. 用户-偏好标签关联表 (users_preferred_tags)
-- ============================================
CREATE TABLE IF NOT EXISTS `users_preferred_tags` (
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `tag_id` INT UNSIGNED NOT NULL COMMENT '标签ID',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`user_id`, `tag_id`),
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`tag_id`) REFERENCES `tags`(`id`) ON DELETE CASCADE,
    INDEX `idx_tag_id` (`tag_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-偏好标签关联表';

-- ============================================
-- 10. 电影-类型关联表 (movies_genres)
-- ============================================
CREATE TABLE IF NOT EXISTS `movies_genres` (
    `movie_id` BIGINT UNSIGNED NOT NULL COMMENT '电影ID',
    `genre_id` TINYINT UNSIGNED NOT NULL COMMENT '类型ID',
    PRIMARY KEY (`movie_id`, `genre_id`),
    FOREIGN KEY (`movie_id`) REFERENCES `movies`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`genre_id`) REFERENCES `genres`(`id`) ON DELETE CASCADE,
    INDEX `idx_genre_id` (`genre_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电影-类型关联表';

-- ============================================
-- 11. 电影-标签关联表 (movies_tags)
-- ============================================
CREATE TABLE IF NOT EXISTS `movies_tags` (
    `movie_id` BIGINT UNSIGNED NOT NULL COMMENT '电影ID',
    `tag_id` INT UNSIGNED NOT NULL COMMENT '标签ID',
    PRIMARY KEY (`movie_id`, `tag_id`),
    FOREIGN KEY (`movie_id`) REFERENCES `movies`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`tag_id`) REFERENCES `tags`(`id`) ON DELETE CASCADE,
    INDEX `idx_tag_id` (`tag_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电影-标签关联表';

-- ============================================
-- 12. 电影-导演关联表 (movies_directors)
-- ============================================
CREATE TABLE IF NOT EXISTS `movies_directors` (
    `movie_id` BIGINT UNSIGNED NOT NULL COMMENT '电影ID',
    `director_id` INT UNSIGNED NOT NULL COMMENT '导演ID',
    PRIMARY KEY (`movie_id`, `director_id`),
    FOREIGN KEY (`movie_id`) REFERENCES `movies`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`director_id`) REFERENCES `directors`(`id`) ON DELETE CASCADE,
    INDEX `idx_director_id` (`director_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电影-导演关联表';

-- ============================================
-- 13. 电影-演员关联表 (movies_actors)
-- ============================================
CREATE TABLE IF NOT EXISTS `movies_actors` (
    `movie_id` BIGINT UNSIGNED NOT NULL COMMENT '电影ID',
    `actor_id` INT UNSIGNED NOT NULL COMMENT '演员ID',
    `role` VARCHAR(100) DEFAULT '' COMMENT '饰演角色名',
    PRIMARY KEY (`movie_id`, `actor_id`),
    FOREIGN KEY (`movie_id`) REFERENCES `movies`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`actor_id`) REFERENCES `actors`(`id`) ON DELETE CASCADE,
    INDEX `idx_actor_id` (`actor_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电影-演员关联表';

-- ============================================
-- 14. 用户-电影行为表 (users_movies_behaviors)
-- ============================================
CREATE TABLE IF NOT EXISTS `users_movies_behaviors` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '自增主键',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `movie_id` BIGINT UNSIGNED NOT NULL COMMENT '电影ID',
    `behavior_type` ENUM('view', 'like', 'collect', 'rate', 'share', 'dislike', 'unlike', 'uncollect') NOT NULL COMMENT '行为类型',
    `rating` TINYINT UNSIGNED NULL CHECK (`rating` BETWEEN 1 AND 5) COMMENT '评分(1-5)',
    `progress_seconds` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '视频播放进度（秒）',
    `duration_seconds` INT UNSIGNED NULL COMMENT '行为持续时间（秒）',
    `client_env` JSON NULL COMMENT '客户端环境信息(JSON)',
    `page_referer` VARCHAR(500) NULL COMMENT '行为触发来源页面',
    `request_id` VARCHAR(64) NOT NULL COMMENT '幂等请求ID，全局唯一',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '行为发生时间',
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`movie_id`) REFERENCES `movies`(`id`) ON DELETE CASCADE,
    UNIQUE INDEX `uk_request_id` (`request_id`),
    INDEX `idx_user_id_behavior` (`user_id`, `behavior_type`, `created_at` DESC),
    INDEX `idx_movie_id_behavior` (`movie_id`, `behavior_type`, `created_at` DESC),
    INDEX `idx_created_at` (`created_at` DESC),
    INDEX `idx_user_movie` (`user_id`, `movie_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-电影行为表（事件流水）';

-- ============================================
-- 15. 评论表 (comments)
-- ============================================
CREATE TABLE IF NOT EXISTS `comments` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键，评论ID',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `movie_id` BIGINT UNSIGNED NOT NULL COMMENT '电影ID',
    `parent_id` BIGINT UNSIGNED NULL COMMENT '父评论ID，用于回复',
    `content` TEXT NOT NULL COMMENT '评论内容',
    `is_pinned` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否置顶(0:否,1:是)',
    `request_id` VARCHAR(64) NOT NULL COMMENT '幂等请求ID，全局唯一',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`movie_id`) REFERENCES `movies`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`parent_id`) REFERENCES `comments`(`id`) ON DELETE CASCADE,
    UNIQUE INDEX `uk_request_id` (`request_id`),
    INDEX `idx_user_id` (`user_id`),
    INDEX `idx_movie_id` (`movie_id`),
    INDEX `idx_parent_id` (`parent_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评论表';


-- ============================================
-- 17. 物品相似度缓存表 (item_similarity_caches)  -- 更名以反映通用性
-- 支持：ItemCF, TurboCF, 基于内容的相似度等
-- ============================================
CREATE TABLE IF NOT EXISTS `item_similarity_caches` (
    `movie_id` BIGINT UNSIGNED NOT NULL COMMENT '电影ID',
    `algorithm` VARCHAR(40) NOT NULL COMMENT '算法类型（item_cf / turbo_cf / content_based 等）',
    `similar_movies` JSON NOT NULL COMMENT '相似电影列表(JSON，含movie_id和score)',
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '离线计算更新时间',
    PRIMARY KEY (`movie_id`, `algorithm`),
    FOREIGN KEY (`movie_id`) REFERENCES `movies`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='物品相似度缓存表（多算法）';


-- ============================================
-- 18. 用户推荐缓存表 (user_recommendation_caches)
-- 支持：SVD, UserCF, 混合推荐等
-- ============================================
CREATE TABLE IF NOT EXISTS `user_recommendation_caches` (
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `algorithm` VARCHAR(40) NOT NULL COMMENT '算法类型（svd / user_cf / hybrid 等）',
    `recommend_movies` JSON NOT NULL COMMENT '推荐电影列表(JSON，含movie_id和score)',
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '离线计算更新时间',
    PRIMARY KEY (`user_id`, `algorithm`),
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户推荐缓存表（多算法）';


-- ============================================
-- 19. A/B 测试 — 实验配置表 (ab_experiments)
-- ============================================
CREATE TABLE IF NOT EXISTS `ab_experiments` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '实验ID',
    `name` VARCHAR(255) NOT NULL COMMENT '实验名称',
    `description` TEXT NULL COMMENT '实验描述',
    `status` ENUM('draft', 'running', 'stopped', 'archived') NOT NULL DEFAULT 'draft' COMMENT '实验状态',
    `split_mode` ENUM('fixed', 'bandit') NOT NULL DEFAULT 'fixed' COMMENT 'fixed=固定比例, bandit=Thompson Sampling自适应',
    `winner_strategy_id` BIGINT UNSIGNED NULL COMMENT '优胜策略ID(收敛后自动设置)',
    `start_time` DATETIME NULL COMMENT '实验开始时间',
    `end_time` DATETIME NULL COMMENT '实验结束时间',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX `idx_status` (`status`),
    INDEX `idx_start_time` (`start_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='A/B测试实验配置表';

-- ============================================
-- 20. A/B 测试 — 策略配置表 (ab_strategies)
-- ============================================
CREATE TABLE IF NOT EXISTS `ab_strategies` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '策略ID',
    `experiment_id` BIGINT UNSIGNED NOT NULL COMMENT '所属实验ID',
    `name` VARCHAR(255) NOT NULL COMMENT '策略名称',
    `algorithm` VARCHAR(40) NOT NULL DEFAULT 'hybrid' COMMENT '推荐算法标识(svd/user_cf/item_cf/turbo_cf/hybrid等)',
    `traffic_percentage` DECIMAL(5,2) NOT NULL DEFAULT 0.00 COMMENT '目标流量百分比(0.00~100.00)',
    `weight_source` ENUM('manual', 'bandit', 'promoted') NOT NULL DEFAULT 'manual' COMMENT '权重来源: manual=人工, bandit=Bandit自动, promoted=推全',
    `bandit_alpha` DECIMAL(10,4) NOT NULL DEFAULT 1.0000 COMMENT 'Thompson Sampling Alpha(成功+1)',
    `bandit_beta` DECIMAL(10,4) NOT NULL DEFAULT 1.0000 COMMENT 'Thompson Sampling Beta(失败+1)',
    `is_control` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为对照组(1=对照组, 0=实验组)',
    `min_traffic` DECIMAL(5,2) NOT NULL DEFAULT 5.00 COMMENT '最小流量下限(%)',
    `coldstart_end_time` DATETIME NULL COMMENT '冷启动结束时间(之前不参与Bandit调整)',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    FOREIGN KEY (`experiment_id`) REFERENCES `ab_experiments`(`id`) ON DELETE CASCADE,
    INDEX `idx_experiment_id` (`experiment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='A/B测试策略配置表';

-- ============================================
-- 21. A/B 测试 — 结果汇总表 (ab_results)
-- Python 离线分析脚本每30分钟写入一次
-- ============================================
CREATE TABLE IF NOT EXISTS `ab_results` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键',
    `experiment_id` BIGINT UNSIGNED NOT NULL COMMENT '实验ID',
    `strategy_id` BIGINT UNSIGNED NOT NULL COMMENT '策略ID',
    `analysis_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分析时间戳',
    `period_start` DATETIME NOT NULL COMMENT '分析窗口起始(最近24h)',
    `period_end` DATETIME NOT NULL COMMENT '分析窗口结束',
    `total_exposures` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '总曝光数',
    `total_clicks` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '总正向事件数(view+like)',
    `total_ratings` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '总评分次数',
    `total_collects` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '总收藏数',
    `total_watch_seconds` DOUBLE NOT NULL DEFAULT 0 COMMENT '总观看时长(秒)',
    `unique_users` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '独立用户数',
    `ctr` DOUBLE NULL COMMENT '正向事件率(CTR)',
    `ctr_ci_lower` DOUBLE NULL COMMENT 'CTR 95%置信区间下界',
    `ctr_ci_upper` DOUBLE NULL COMMENT 'CTR 95%置信区间上界',
    `avg_watch_seconds` DOUBLE NULL COMMENT '人均观看时长(秒)',
    `rating_rate` DOUBLE NULL COMMENT '评分率',
    `collect_rate` DOUBLE NULL COMMENT '收藏率',
    `positive_events` INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '正向事件总数',
    `p_value` DOUBLE NULL COMMENT '与对照组对比的p值(Z检验)',
    `is_winner` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '当前周期是否显著优于对照组',
    `is_converged` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '实验是否已收敛',
    `sample_size_sufficient` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '各组样本量是否充足',
    `bandit_alpha` DOUBLE NULL COMMENT '更新后的Bandit Alpha',
    `bandit_beta` DOUBLE NULL COMMENT '更新后的Bandit Beta',
    FOREIGN KEY (`experiment_id`) REFERENCES `ab_experiments`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`strategy_id`) REFERENCES `ab_strategies`(`id`) ON DELETE CASCADE,
    INDEX `idx_exp_time` (`experiment_id`, `analysis_time` DESC),
    INDEX `idx_exp_strategy_time` (`experiment_id`, `strategy_id`, `analysis_time` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='A/B测试结果汇总表';

-- ============================================
-- 22. 用户桶覆盖表 (user_bucket_override)
-- Bandit模式：首次访问时Thompson Sampling分配策略，写此表确保后续一致性
-- ============================================
CREATE TABLE IF NOT EXISTS `user_bucket_override` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT COMMENT '主键',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `experiment_id` BIGINT UNSIGNED NOT NULL COMMENT '实验ID',
    `strategy_id` BIGINT UNSIGNED NOT NULL COMMENT 'Bandit分配的策略ID',
    `bucket_id` INT UNSIGNED NOT NULL COMMENT 'MD5分桶号(0-99)',
    `assigned_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分配时间',
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`experiment_id`) REFERENCES `ab_experiments`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`strategy_id`) REFERENCES `ab_strategies`(`id`) ON DELETE CASCADE,
    UNIQUE INDEX `uk_user_experiment` (`user_id`, `experiment_id`),
    INDEX `idx_experiment_id` (`experiment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户桶覆盖表(Bandit自适应分流)';

-- ============================================
-- 23. 扩展 users_movies_behaviors：添加实验标识字段
-- 使用存储过程安全添加（字段/索引已存在时不会报错）
-- ============================================
DELIMITER $$

CREATE PROCEDURE IF NOT EXISTS add_ab_columns_if_not_exists()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'users_movies_behaviors'
          AND COLUMN_NAME = 'experiment_id'
    ) THEN
        ALTER TABLE `users_movies_behaviors`
            ADD COLUMN `experiment_id` BIGINT UNSIGNED NULL COMMENT '命中的实验ID(NULL=未参与实验)' AFTER `page_referer`;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'users_movies_behaviors'
          AND COLUMN_NAME = 'strategy_id'
    ) THEN
        ALTER TABLE `users_movies_behaviors`
            ADD COLUMN `strategy_id` BIGINT UNSIGNED NULL COMMENT '命中的策略ID(NULL=未参与实验)' AFTER `experiment_id`;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'users_movies_behaviors'
          AND INDEX_NAME = 'idx_experiment_id'
    ) THEN
        ALTER TABLE `users_movies_behaviors`
            ADD INDEX `idx_experiment_id` (`experiment_id`) USING BTREE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'users_movies_behaviors'
          AND INDEX_NAME = 'idx_strategy_id'
    ) THEN
        ALTER TABLE `users_movies_behaviors`
            ADD INDEX `idx_strategy_id` (`strategy_id`) USING BTREE;
    END IF;
END$$

DELIMITER ;

CALL add_ab_columns_if_not_exists();
DROP PROCEDURE IF EXISTS add_ab_columns_if_not_exists;

-- ============================================
-- 创建完成提示
-- ============================================
SELECT 'MovieRecommendSystem 数据库及所有表创建完成！' AS `提示信息`;