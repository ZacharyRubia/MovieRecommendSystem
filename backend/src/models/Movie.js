const { query } = require('../config/db');

// 电影模型
class MovieModel {
  // 获取所有电影
  static async findAll(page = 1, limit = 20) {
    const offset = (page - 1) * limit;
    return query(`
      SELECT id, title, description, cover_url, video_url, release_year, duration, avg_rating, created_at
      FROM movies
      ORDER BY created_at DESC
      LIMIT ? OFFSET ?
    `, [limit, offset]);
  }

  // 获取电影总数
  static async count() {
    const rows = await query('SELECT COUNT(*) as total FROM movies', []);
    return rows[0].total;
  }

  // 根据ID查找电影
  static async findById(id) {
    const rows = await query(`
      SELECT id, title, description, cover_url, video_url, release_year, duration, avg_rating, created_at
      FROM movies
      WHERE id = ?
    `, [id]);
    return rows[0];
  }

  // 创建新电影
  static async create(movieData) {
    const { title, description, cover_url, video_url, release_year, duration } = movieData;
    const result = await query(
      'INSERT INTO movies (title, description, cover_url, video_url, release_year, duration) VALUES (?, ?, ?, ?, ?, ?)',
      [title, description || '', cover_url || '', video_url || '', release_year || null, duration || null]
    );
    return result.insertId;
  }

  // 更新电影评分
  static async updateAvgRating(movieId) {
    await query(`
      UPDATE movies m
      SET avg_rating = (
        SELECT AVG(rating) FROM users_movies_behaviors
        WHERE movie_id = ? AND behavior_type = 'rate' AND rating IS NOT NULL
      )
      WHERE id = ?
    `, [movieId, movieId]);
    return true;
  }

  // 获取电影的所有评论
  static async getComments(movieId, page = 1, limit = 20) {
    const offset = (page - 1) * limit;
    return query(`
      SELECT 
        umb.id,
        umb.user_id,
        u.username,
        umb.rating,
        umb.created_at
      FROM users_movies_behaviors umb
      JOIN users u ON umb.user_id = u.id
      WHERE umb.movie_id = ? AND umb.behavior_type = 'rate' AND umb.rating IS NOT NULL
      ORDER BY umb.created_at DESC
      LIMIT ? OFFSET ?
    `, [movieId, limit, offset]);
  }

  // 获取评论总数
  static async countComments(movieId) {
    const rows = await query(`
      SELECT COUNT(*) as total FROM users_movies_behaviors
      WHERE movie_id = ? AND behavior_type = 'rate' AND rating IS NOT NULL
    `, [movieId]);
    return rows[0].total;
  }

  // 用户评分
  static async addRating(userId, movieId, rating, requestId) {
    const result = await query(
      `INSERT INTO users_movies_behaviors (user_id, movie_id, behavior_type, rating, request_id)
       VALUES (?, ?, 'rate', ?, ?)
       ON DUPLICATE KEY UPDATE rating = ?, updated_at = CURRENT_TIMESTAMP`,
      [userId, movieId, rating, requestId, rating]
    );
    await this.updateAvgRating(movieId);
    return result.insertId;
  }

  // 获取用户对电影的评分
  static async getUserRating(userId, movieId) {
    const rows = await query(`
      SELECT rating FROM users_movies_behaviors
      WHERE user_id = ? AND movie_id = ? AND behavior_type = 'rate' AND rating IS NOT NULL
      ORDER BY created_at DESC
      LIMIT 1
    `, [userId, movieId]);
    return rows[0] ? rows[0].rating : null;
  }
}

module.exports = MovieModel;