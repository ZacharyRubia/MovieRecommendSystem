-- ============================================
-- 1. 创建数据库（移除不兼容的 COMMENT 子句）
-- ============================================
CREATE DATABASE IF NOT EXISTS `MovieRecommendSystem`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `MovieRecommendSystem`;

-- ============================================
-- 2. 创建角色字典表 (role)
-- ============================================
CREATE TABLE IF NOT EXISTS `role` (
    `id` TINYINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(20) UNIQUE NOT NULL,
    `display_name` VARCHAR(20) NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入初始角色数据 (id=1: admin, id=2: user)
INSERT IGNORE INTO `role` (`id`, `name`, `display_name`) VALUES
(1, 'admin', '管理员'),
(2, 'user', '普通用户');

-- ============================================
-- 3. 创建用户表 (users)
-- ============================================
CREATE TABLE IF NOT EXISTS `users` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    `username` VARCHAR(50) UNIQUE NOT NULL,
    `email` VARCHAR(100) UNIQUE,
    `password_hash` VARCHAR(255) NOT NULL,
    `role_id` TINYINT UNSIGNED NOT NULL DEFAULT 2,
    `avatar_url` VARCHAR(500) DEFAULT '',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (`role_id`) REFERENCES `role`(`id`),
    INDEX `idx_role_id` (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 4. 创建标签表 (tag)
-- ============================================
CREATE TABLE IF NOT EXISTS `tag` (
    `id` INT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(50) UNIQUE NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 5. 创建导演表 (director)
-- ============================================
CREATE TABLE IF NOT EXISTS `director` (
    `id` INT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(100) UNIQUE NOT NULL,
    `avatar_url` VARCHAR(500) DEFAULT '',
    `description` TEXT,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 6. 创建演员表 (actor)
-- ============================================
CREATE TABLE IF NOT EXISTS `actor` (
    `id` INT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    `name` VARCHAR(100) UNIQUE NOT NULL,
    `avatar_url` VARCHAR(500) DEFAULT '',
    `description` TEXT,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 7. 创建电影信息表 (movie)
-- ============================================
CREATE TABLE IF NOT EXISTS `movie` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    `title` VARCHAR(200) NOT NULL,
    `description` TEXT,
    `cover_url` VARCHAR(500),
    `video_url` VARCHAR(500),
    `release_year` YEAR,
    `duration` INT,
    `avg_rating` DECIMAL(3,2) DEFAULT 0.00,
    `vector_synced_at` TIMESTAMP NULL DEFAULT NULL,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_release_year` (`release_year`),
    INDEX `idx_avg_rating` (`avg_rating`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 8. 创建用户-偏好标签关联表 (user_preferred_tag)
-- ============================================
CREATE TABLE IF NOT EXISTS `user_preferred_tag` (
    `user_id` BIGINT UNSIGNED NOT NULL,
    `tag_id` INT UNSIGNED NOT NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`user_id`, `tag_id`),
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`tag_id`) REFERENCES `tag`(`id`) ON DELETE CASCADE,
    INDEX `idx_tag_id` (`tag_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 9. 创建电影-标签关联表 (movie_tag)
-- ============================================
CREATE TABLE IF NOT EXISTS `movie_tag` (
    `movie_id` BIGINT UNSIGNED NOT NULL,
    `tag_id` INT UNSIGNED NOT NULL,
    PRIMARY KEY (`movie_id`, `tag_id`),
    FOREIGN KEY (`movie_id`) REFERENCES `movie`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`tag_id`) REFERENCES `tag`(`id`) ON DELETE CASCADE,
    INDEX `idx_tag_id` (`tag_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 10. 创建电影-导演关联表 (movie_director)
-- ============================================
CREATE TABLE IF NOT EXISTS `movie_director` (
    `movie_id` BIGINT UNSIGNED NOT NULL,
    `director_id` INT UNSIGNED NOT NULL,
    PRIMARY KEY (`movie_id`, `director_id`),
    FOREIGN KEY (`movie_id`) REFERENCES `movie`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`director_id`) REFERENCES `director`(`id`) ON DELETE CASCADE,
    INDEX `idx_director_id` (`director_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 11. 创建电影-演员关联表 (movie_actor)
-- ============================================
CREATE TABLE IF NOT EXISTS `movie_actor` (
    `movie_id` BIGINT UNSIGNED NOT NULL,
    `actor_id` INT UNSIGNED NOT NULL,
    `role` VARCHAR(100) DEFAULT '',
    PRIMARY KEY (`movie_id`, `actor_id`),
    FOREIGN KEY (`movie_id`) REFERENCES `movie`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`actor_id`) REFERENCES `actor`(`id`) ON DELETE CASCADE,
    INDEX `idx_actor_id` (`actor_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 12. 创建用户-电影行为表 (user_movie_behavior)
-- ============================================
CREATE TABLE IF NOT EXISTS `user_movie_behavior` (
    `id` BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `movie_id` BIGINT UNSIGNED NOT NULL,
    `behavior_type` ENUM('view', 'like', 'collect', 'rate', 'share', 'dislike', 'unlike', 'uncollect') NOT NULL,
    `rating` TINYINT UNSIGNED NULL CHECK (`rating` BETWEEN 1 AND 5),
    `progress_seconds` INT UNSIGNED NOT NULL DEFAULT 0,
    `duration_seconds` INT UNSIGNED NULL,
    `client_env` JSON NULL,
    `page_referer` VARCHAR(500) NULL,
    `request_id` VARCHAR(64) NOT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`movie_id`) REFERENCES `movie`(`id`) ON DELETE CASCADE,
    UNIQUE INDEX `uk_request_id` (`request_id`),
    INDEX `idx_user_id_behavior` (`user_id`, `behavior_type`, `created_at` DESC),
    INDEX `idx_movie_id_behavior` (`movie_id`, `behavior_type`, `created_at` DESC),
    INDEX `idx_created_at` (`created_at` DESC),
    INDEX `idx_user_movie` (`user_id`, `movie_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 创建完成提示
-- ============================================
SELECT 'MovieRecommendSystem 数据库及所有表创建完成！' AS `提示信息`;