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
-- 创建完成提示
-- ============================================
SELECT 'MovieRecommendSystem 数据库及所有表创建完成！' AS `提示信息`;