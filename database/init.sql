-- MovieRecommendSystem 数据库初始化脚本
-- 注意：此文件仅供参考，实际表结构请以数据库迁移为准

-- 创建数据库
CREATE DATABASE IF NOT EXISTS movie_recommend DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE movie_recommend;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
  id INT PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(50) NOT NULL UNIQUE,
  email VARCHAR(100) DEFAULT '',
  password_hash VARCHAR(255) NOT NULL,
  avatar_url VARCHAR(500) DEFAULT '',
  role_id INT DEFAULT 2 COMMENT '1=管理员, 2=普通用户',
  request_id VARCHAR(64) DEFAULT '' COMMENT '请求唯一标识，用于并发控制',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_users_role_id (role_id),
  INDEX idx_users_created_at (created_at DESC)
) ENGINE=InnoDB;

-- 电影表
CREATE TABLE IF NOT EXISTS movies (
  id INT PRIMARY KEY AUTO_INCREMENT,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  cover_url VARCHAR(500) DEFAULT '',
  video_url VARCHAR(500) DEFAULT '',
  release_year INT,
  duration INT COMMENT '分钟',
  avg_rating DECIMAL(3,1) DEFAULT 0.0 COMMENT '平均评分',
  rating_count INT DEFAULT 0 COMMENT '评分人数',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_movies_created_at (created_at DESC),
  INDEX idx_movies_avg_rating (avg_rating DESC),
  INDEX idx_movies_release_year (release_year DESC),
  INDEX idx_movies_title (title)
) ENGINE=InnoDB;

-- 标签表
CREATE TABLE IF NOT EXISTS tags (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_tags_created_at (created_at DESC),
  INDEX idx_tags_name (name)
) ENGINE=InnoDB;

-- 电影-标签关联表
CREATE TABLE IF NOT EXISTS movies_tags (
  movie_id INT NOT NULL,
  tag_id INT NOT NULL,
  PRIMARY KEY (movie_id, tag_id),
  FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 题材表
CREATE TABLE IF NOT EXISTS genres (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL UNIQUE,
  code VARCHAR(50) NOT NULL UNIQUE COMMENT '题材代码',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_genres_created_at (created_at DESC),
  INDEX idx_genres_name (name),
  INDEX idx_genres_code (code)
) ENGINE=InnoDB;

-- 电影-题材关联表
CREATE TABLE IF NOT EXISTS movies_genres (
  movie_id INT NOT NULL,
  genre_id INT NOT NULL,
  PRIMARY KEY (movie_id, genre_id),
  FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
  FOREIGN KEY (genre_id) REFERENCES genres(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 导演表
CREATE TABLE IF NOT EXISTS directors (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL UNIQUE,
  avatar_url VARCHAR(500) DEFAULT '',
  description TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_directors_created_at (created_at DESC),
  INDEX idx_directors_name (name)
) ENGINE=InnoDB;

-- 电影-导演关联表
CREATE TABLE IF NOT EXISTS movies_directors (
  movie_id INT NOT NULL,
  director_id INT NOT NULL,
  PRIMARY KEY (movie_id, director_id),
  FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
  FOREIGN KEY (director_id) REFERENCES directors(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 演员表
CREATE TABLE IF NOT EXISTS actors (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL UNIQUE,
  avatar_url VARCHAR(500) DEFAULT '',
  description TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_actors_created_at (created_at DESC),
  INDEX idx_actors_name (name)
) ENGINE=InnoDB;

-- 电影-演员关联表
CREATE TABLE IF NOT EXISTS movies_actors (
  movie_id INT NOT NULL,
  actor_id INT NOT NULL,
  role VARCHAR(100) DEFAULT '' COMMENT '饰演角色',
  PRIMARY KEY (movie_id, actor_id),
  FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
  FOREIGN KEY (actor_id) REFERENCES actors(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 评论表
CREATE TABLE IF NOT EXISTS comments (
  id INT PRIMARY KEY AUTO_INCREMENT,
  movie_id INT NOT NULL,
  user_id INT NOT NULL,
  content TEXT NOT NULL,
  rating DECIMAL(2,1) DEFAULT NULL COMMENT '评分 0.0-5.0',
  is_pinned TINYINT(1) DEFAULT 0 COMMENT '是否置顶',
  parent_id INT DEFAULT NULL COMMENT '回复的评论ID',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_comments_movie_id (movie_id),
  INDEX idx_comments_user_id (user_id),
  INDEX idx_comments_created_at (created_at DESC),
  INDEX idx_comments_is_pinned (is_pinned, created_at DESC)
) ENGINE=InnoDB;

-- 评分表（如需独立存储评分）
CREATE TABLE IF NOT EXISTS ratings (
  id INT PRIMARY KEY AUTO_INCREMENT,
  movie_id INT NOT NULL,
  user_id INT NOT NULL,
  rating DECIMAL(2,1) NOT NULL COMMENT '评分 0.5-5.0',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ratings_user_movie (user_id, movie_id),
  FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_ratings_movie_id (movie_id),
  INDEX idx_ratings_user_id (user_id),
  INDEX idx_ratings_created_at (created_at DESC)
) ENGINE=InnoDB;