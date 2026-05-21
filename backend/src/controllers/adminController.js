const db = require('../config/db');

// ==================== 电影管理 ====================

// 获取所有电影（带分页、搜索）
exports.getAllMovies = async (req, res) => {
  try {
    const { page = 1, limit = 20, search = '' } = req.query;
    const pageSize = parseInt(limit) || 20;
    const offset = (page - 1) * pageSize;
    
    let sql = `
      SELECT m.*, 
             GROUP_CONCAT(DISTINCT t.name SEPARATOR ', ') as tags,
             GROUP_CONCAT(DISTINCT g.name SEPARATOR ', ') as genres,
             GROUP_CONCAT(DISTINCT d.name SEPARATOR ', ') as directors,
             GROUP_CONCAT(DISTINCT a.name SEPARATOR ', ') as actors
      FROM movies m
      LEFT JOIN movies_tags mt ON m.id = mt.movie_id
      LEFT JOIN tags t ON mt.tag_id = t.id
      LEFT JOIN movies_genres mg ON m.id = mg.movie_id
      LEFT JOIN genres g ON mg.genre_id = g.id
      LEFT JOIN movies_directors md ON m.id = md.movie_id
      LEFT JOIN directors d ON md.director_id = d.id
      LEFT JOIN movies_actors ma ON m.id = ma.movie_id
      LEFT JOIN actors a ON ma.actor_id = a.id
      WHERE 1=1
    `;
    
    const params = [];
    
    if (search) {
      sql += ` AND m.title LIKE ?`;
      params.push(`%${search}%`);
    }
    
    sql += ` GROUP BY m.id ORDER BY m.id ASC LIMIT ? OFFSET ?`;
    params.push(pageSize, parseInt(offset));
    
    const movies = await db.query(sql, params);
    
    // 获取总数
    let countSql = `SELECT COUNT(*) as total FROM movies WHERE 1=1`;
    const countParams = [];
    if (search) {
      countSql += ` AND title LIKE ?`;
      countParams.push(`%${search}%`);
    }
    const countResult = await db.query(countSql, countParams);
    
    res.json({
      success: true,
      data: {
        movies,
        total: countResult[0].total,
        page: parseInt(page),
        pageSize: parseInt(pageSize)
      }
    });
  } catch (err) {
    console.error('获取电影列表失败:', err);
    res.status(500).json({ success: false, message: '获取电影列表失败' });
  }
};

// 获取单个电影详情
exports.getMovieById = async (req, res) => {
  try {
    const { id } = req.params;
    
    // 获取电影基本信息
    const movies = await db.query('SELECT * FROM movies WHERE id = ?', [id]);
    if (movies.length === 0) {
      return res.status(404).json({ success: false, message: '电影不存在' });
    }
    
    const movie = movies[0];
    
    // 获取关联标签
    const tags = await db.query(`
      SELECT t.id, t.name FROM tags t
      JOIN movies_tags mt ON t.id = mt.tag_id
      WHERE mt.movie_id = ?
    `, [id]);
    
    // 获取关联题材
    const genres = await db.query(`
      SELECT g.id, g.name, g.code FROM genres g
      JOIN movies_genres mg ON g.id = mg.genre_id
      WHERE mg.movie_id = ?
    `, [id]);
    
    // 获取关联导演
    const directors = await db.query(`
      SELECT d.id, d.name FROM directors d
      JOIN movies_directors md ON d.id = md.director_id
      WHERE md.movie_id = ?
    `, [id]);
    
    // 获取关联演员
    const actors = await db.query(`
      SELECT a.id, a.name, ma.role FROM actors a
      JOIN movies_actors ma ON a.id = ma.actor_id
      WHERE ma.movie_id = ?
    `, [id]);
    
    res.json({
      success: true,
      data: {
        ...movie,
        tags,
        genres,
        directors,
        actors
      }
    });
  } catch (err) {
    console.error('获取电影详情失败:', err);
    res.status(500).json({ success: false, message: '获取电影详情失败' });
  }
};

// 创建电影
exports.createMovie = async (req, res) => {
  try {
    const { title, description, cover_url, video_url, release_year, duration, tags = [], genres = [], directors = [], actors = [] } = req.body;
    
    if (!title) {
      return res.status(400).json({ success: false, message: '电影标题不能为空' });
    }
    
    // 插入电影
    const result = await db.query(
      'INSERT INTO movies (title, description, cover_url, video_url, release_year, duration) VALUES (?, ?, ?, ?, ?, ?)',
      [title, description || '', cover_url || '', video_url || '', release_year || null, duration || null]
    );
    
    const movieId = result.insertId;
    
    // 关联标签
    if (tags && tags.length > 0) {
      const tagValues = tags.map(tagId => [movieId, tagId]);
      await db.query('INSERT IGNORE INTO movies_tags (movie_id, tag_id) VALUES ?', [tagValues]);
    }
    
    // 关联题材
    if (genres && genres.length > 0) {
      const genreValues = genres.map(genreId => [movieId, genreId]);
      await db.query('INSERT IGNORE INTO movies_genres (movie_id, genre_id) VALUES ?', [genreValues]);
    }
    
    // 关联导演
    if (directors && directors.length > 0) {
      const directorValues = directors.map(directorId => [movieId, directorId]);
      await db.query('INSERT IGNORE INTO movies_directors (movie_id, director_id) VALUES ?', [directorValues]);
    }
    
    // 关联演员
    if (actors && actors.length > 0) {
      const actorValues = actors.map(actor => [movieId, actor.id, actor.role || '']);
      await db.query('INSERT IGNORE INTO movies_actors (movie_id, actor_id, role) VALUES ?', [actorValues]);
    }
    
    res.json({
      success: true,
      message: '电影创建成功',
      data: { id: movieId }
    });
  } catch (err) {
    console.error('创建电影失败:', err);
    res.status(500).json({ success: false, message: '创建电影失败' });
  }
};

// 更新电影
exports.updateMovie = async (req, res) => {
  try {
    const { id } = req.params;
    const { title, description, cover_url, video_url, release_year, duration, tags, genres, directors, actors } = req.body;
    
    // 更新电影基本信息
    const updateFields = [];
    const updateValues = [];
    
    if (title !== undefined) {
      updateFields.push('title = ?');
      updateValues.push(title);
    }
    if (description !== undefined) {
      updateFields.push('description = ?');
      updateValues.push(description);
    }
    if (cover_url !== undefined) {
      updateFields.push('cover_url = ?');
      updateValues.push(cover_url);
    }
    if (video_url !== undefined) {
      updateFields.push('video_url = ?');
      updateValues.push(video_url);
    }
    if (release_year !== undefined) {
      updateFields.push('release_year = ?');
      updateValues.push(release_year);
    }
    if (duration !== undefined) {
      updateFields.push('duration = ?');
      updateValues.push(duration);
    }
    
    if (updateFields.length > 0) {
      updateValues.push(id);
      await db.query(`UPDATE movies SET ${updateFields.join(', ')} WHERE id = ?`, updateValues);
    }
    
    // 更新标签关联
    if (tags !== undefined) {
      await db.query('DELETE FROM movies_tags WHERE movie_id = ?', [id]);
      if (tags.length > 0) {
        const tagValues = tags.map(tagId => [id, tagId]);
        await db.query('INSERT INTO movies_tags (movie_id, tag_id) VALUES ?', [tagValues]);
      }
    }
    
    // 更新题材关联
    if (genres !== undefined) {
      await db.query('DELETE FROM movies_genres WHERE movie_id = ?', [id]);
      if (genres.length > 0) {
        const genreValues = genres.map(genreId => [id, genreId]);
        await db.query('INSERT INTO movies_genres (movie_id, genre_id) VALUES ?', [genreValues]);
      }
    }
    
    // 更新导演关联
    if (directors !== undefined) {
      await db.query('DELETE FROM movies_directors WHERE movie_id = ?', [id]);
      if (directors.length > 0) {
        const directorValues = directors.map(directorId => [id, directorId]);
        await db.query('INSERT INTO movies_directors (movie_id, director_id) VALUES ?', [directorValues]);
      }
    }
    
    // 更新演员关联
    if (actors !== undefined) {
      await db.query('DELETE FROM movies_actors WHERE movie_id = ?', [id]);
      if (actors.length > 0) {
        const actorValues = actors.map(actor => [id, actor.id, actor.role || '']);
        await db.query('INSERT INTO movies_actors (movie_id, actor_id, role) VALUES ?', [actorValues]);
      }
    }
    
    res.json({
      success: true,
      message: '电影更新成功'
    });
  } catch (err) {
    console.error('更新电影失败:', err);
    res.status(500).json({ success: false, message: '更新电影失败' });
  }
};

// 删除电影
exports.deleteMovie = async (req, res) => {
  try {
    const { id } = req.params;
    
    const result = await db.query('DELETE FROM movies WHERE id = ?', [id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '电影不存在' });
    }
    
    res.json({
      success: true,
      message: '电影删除成功'
    });
  } catch (err) {
    console.error('删除电影失败:', err);
    res.status(500).json({ success: false, message: '删除电影失败' });
  }
};

// 批量导入电影
exports.batchImportMovies = async (req, res) => {
  try {
    const { movies } = req.body;
    
    if (!Array.isArray(movies) || movies.length === 0) {
      return res.status(400).json({ success: false, message: '导入数据格式错误' });
    }
    
    let successCount = 0;
    let failCount = 0;
    const errors = [];
    
    for (let i = 0; i < movies.length; i++) {
      const movie = movies[i];
      try {
        const result = await db.query(
          'INSERT INTO movies (title, description, cover_url, video_url, release_year, duration) VALUES (?, ?, ?, ?, ?, ?)',
          [movie.title, movie.description || '', movie.cover_url || '', movie.video_url || '', movie.release_year || null, movie.duration || null]
        );
        successCount++;
      } catch (err) {
        failCount++;
        errors.push(`第${i + 1}行: ${movie.title} - ${err.message}`);
      }
    }
    
    res.json({
      success: true,
      message: `批量导入完成，成功${successCount}条，失败${failCount}条`,
      data: { successCount, failCount, errors }
    });
  } catch (err) {
    console.error('批量导入电影失败:', err);
    res.status(500).json({ success: false, message: '批量导入电影失败' });
  }
};

// ==================== 标签管理 ====================

exports.getAllTags = async (req, res) => {
  try {
    const { page = 1, limit = 50, search = '' } = req.query;
    const pageSize = parseInt(limit) || 50;
    const offset = (page - 1) * pageSize;
    
    let sql = 'SELECT * FROM tags WHERE 1=1';
    const params = [];
    if (search) {
      sql += ' AND name LIKE ?';
      params.push(`%${search}%`);
    }
    sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params.push(pageSize, parseInt(offset));
    
    const tags = await db.query(sql, params);
    
    let countSql = 'SELECT COUNT(*) as total FROM tags WHERE 1=1';
    const countParams = [];
    if (search) {
      countSql += ' AND name LIKE ?';
      countParams.push(`%${search}%`);
    }
    const countResult = await db.query(countSql, countParams);
    
    res.json({
      success: true,
      data: {
        tags,
        total: countResult[0].total,
        page: parseInt(page),
        pageSize
      }
    });
  } catch (err) {
    console.error('获取标签列表失败:', err);
    res.status(500).json({ success: false, message: '获取标签列表失败' });
  }
};

exports.createTag = async (req, res) => {
  try {
    const { name } = req.body;
    if (!name) {
      return res.status(400).json({ success: false, message: '标签名称不能为空' });
    }
    
    const result = await db.query('INSERT INTO tags (name) VALUES (?)', [name]);
    res.json({
      success: true,
      message: '标签创建成功',
      data: { id: result.insertId, name }
    });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '标签名称已存在' });
    }
    console.error('创建标签失败:', err);
    res.status(500).json({ success: false, message: '创建标签失败' });
  }
};

exports.updateTag = async (req, res) => {
  try {
    const { id } = req.params;
    const { name } = req.body;
    
    if (!name) {
      return res.status(400).json({ success: false, message: '标签名称不能为空' });
    }
    
    const result = await db.query('UPDATE tags SET name = ? WHERE id = ?', [name, id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '标签不存在' });
    }
    
    res.json({ success: true, message: '标签更新成功' });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '标签名称已存在' });
    }
    console.error('更新标签失败:', err);
    res.status(500).json({ success: false, message: '更新标签失败' });
  }
};

exports.deleteTag = async (req, res) => {
  try {
    const { id } = req.params;
    const result = await db.query('DELETE FROM tags WHERE id = ?', [id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '标签不存在' });
    }
    
    res.json({ success: true, message: '标签删除成功' });
  } catch (err) {
    console.error('删除标签失败:', err);
    res.status(500).json({ success: false, message: '删除标签失败' });
  }
};

// ==================== 导演管理 ====================

exports.getAllDirectors = async (req, res) => {
  try {
    const { page = 1, limit = 50, search = '' } = req.query;
    const pageSize = parseInt(limit) || 50;
    const offset = (page - 1) * pageSize;
    
    let sql = 'SELECT * FROM directors WHERE 1=1';
    const params = [];
    if (search) {
      sql += ' AND name LIKE ?';
      params.push(`%${search}%`);
    }
    sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params.push(pageSize, parseInt(offset));
    
    const directors = await db.query(sql, params);
    
    let countSql = 'SELECT COUNT(*) as total FROM directors WHERE 1=1';
    const countParams = [];
    if (search) {
      countSql += ' AND name LIKE ?';
      countParams.push(`%${search}%`);
    }
    const countResult = await db.query(countSql, countParams);
    
    res.json({
      success: true,
      data: {
        directors,
        total: countResult[0].total,
        page: parseInt(page),
        pageSize
      }
    });
  } catch (err) {
    console.error('获取导演列表失败:', err);
    res.status(500).json({ success: false, message: '获取导演列表失败' });
  }
};

exports.createDirector = async (req, res) => {
  try {
    const { name, avatar_url, description } = req.body;
    if (!name) {
      return res.status(400).json({ success: false, message: '导演名称不能为空' });
    }
    
    const result = await db.query(
      'INSERT INTO directors (name, avatar_url, description) VALUES (?, ?, ?)',
      [name, avatar_url || '', description || '']
    );
    res.json({
      success: true,
      message: '导演创建成功',
      data: { id: result.insertId, name }
    });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '导演名称已存在' });
    }
    console.error('创建导演失败:', err);
    res.status(500).json({ success: false, message: '创建导演失败' });
  }
};

exports.updateDirector = async (req, res) => {
  try {
    const { id } = req.params;
    const { name, avatar_url, description } = req.body;
    
    if (!name) {
      return res.status(400).json({ success: false, message: '导演名称不能为空' });
    }
    
    const result = await db.query(
      'UPDATE directors SET name = ?, avatar_url = ?, description = ? WHERE id = ?',
      [name, avatar_url || '', description || '', id]
    );
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '导演不存在' });
    }
    
    res.json({ success: true, message: '导演更新成功' });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '导演名称已存在' });
    }
    console.error('更新导演失败:', err);
    res.status(500).json({ success: false, message: '更新导演失败' });
  }
};

exports.deleteDirector = async (req, res) => {
  try {
    const { id } = req.params;
    const result = await db.query('DELETE FROM directors WHERE id = ?', [id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '导演不存在' });
    }
    
    res.json({ success: true, message: '导演删除成功' });
  } catch (err) {
    console.error('删除导演失败:', err);
    res.status(500).json({ success: false, message: '删除导演失败' });
  }
};

// ==================== 演员管理 ====================

exports.getAllActors = async (req, res) => {
  try {
    const { page = 1, limit = 50, search = '' } = req.query;
    const pageSize = parseInt(limit) || 50;
    const offset = (page - 1) * pageSize;
    
    let sql = 'SELECT * FROM actors WHERE 1=1';
    const params = [];
    if (search) {
      sql += ' AND name LIKE ?';
      params.push(`%${search}%`);
    }
    sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params.push(pageSize, parseInt(offset));
    
    const actors = await db.query(sql, params);
    
    let countSql = 'SELECT COUNT(*) as total FROM actors WHERE 1=1';
    const countParams = [];
    if (search) {
      countSql += ' AND name LIKE ?';
      countParams.push(`%${search}%`);
    }
    const countResult = await db.query(countSql, countParams);
    
    res.json({
      success: true,
      data: {
        actors,
        total: countResult[0].total,
        page: parseInt(page),
        pageSize
      }
    });
  } catch (err) {
    console.error('获取演员列表失败:', err);
    res.status(500).json({ success: false, message: '获取演员列表失败' });
  }
};

exports.createActor = async (req, res) => {
  try {
    const { name, avatar_url, description } = req.body;
    if (!name) {
      return res.status(400).json({ success: false, message: '演员名称不能为空' });
    }
    
    const result = await db.query(
      'INSERT INTO actors (name, avatar_url, description) VALUES (?, ?, ?)',
      [name, avatar_url || '', description || '']
    );
    res.json({
      success: true,
      message: '演员创建成功',
      data: { id: result.insertId, name }
    });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '演员名称已存在' });
    }
    console.error('创建演员失败:', err);
    res.status(500).json({ success: false, message: '创建演员失败' });
  }
};

exports.updateActor = async (req, res) => {
  try {
    const { id } = req.params;
    const { name, avatar_url, description } = req.body;
    
    if (!name) {
      return res.status(400).json({ success: false, message: '演员名称不能为空' });
    }
    
    const result = await db.query(
      'UPDATE actors SET name = ?, avatar_url = ?, description = ? WHERE id = ?',
      [name, avatar_url || '', description || '', id]
    );
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '演员不存在' });
    }
    
    res.json({ success: true, message: '演员更新成功' });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '演员名称已存在' });
    }
    console.error('更新演员失败:', err);
    res.status(500).json({ success: false, message: '更新演员失败' });
  }
};

exports.deleteActor = async (req, res) => {
  try {
    const { id } = req.params;
    const result = await db.query('DELETE FROM actors WHERE id = ?', [id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '演员不存在' });
    }
    
    res.json({ success: true, message: '演员删除成功' });
  } catch (err) {
    console.error('删除演员失败:', err);
    res.status(500).json({ success: false, message: '删除演员失败' });
  }
};

// ==================== 题材管理 ====================

exports.getAllGenres = async (req, res) => {
  try {
    const { page = 1, limit = 50, search = '' } = req.query;
    const pageSize = parseInt(limit) || 50;
    const offset = (page - 1) * pageSize;
    
    let sql = 'SELECT * FROM genres WHERE 1=1';
    const params = [];
    if (search) {
      sql += ' AND (name LIKE ? OR code LIKE ?)';
      params.push(`%${search}%`, `%${search}%`);
    }
    sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params.push(pageSize, parseInt(offset));
    
    const genres = await db.query(sql, params);
    
    let countSql = 'SELECT COUNT(*) as total FROM genres WHERE 1=1';
    const countParams = [];
    if (search) {
      countSql += ' AND (name LIKE ? OR code LIKE ?)';
      countParams.push(`%${search}%`, `%${search}%`);
    }
    const countResult = await db.query(countSql, countParams);
    
    res.json({
      success: true,
      data: {
        genres,
        total: countResult[0].total,
        page: parseInt(page),
        pageSize
      }
    });
  } catch (err) {
    console.error('获取题材列表失败:', err);
    res.status(500).json({ success: false, message: '获取题材列表失败' });
  }
};

exports.createGenre = async (req, res) => {
  try {
    const { name, code } = req.body;
    if (!name || !code) {
      return res.status(400).json({ success: false, message: '题材名称和代码不能为空' });
    }
    
    const result = await db.query('INSERT INTO genres (name, code) VALUES (?, ?)', [name, code]);
    res.json({
      success: true,
      message: '题材创建成功',
      data: { id: result.insertId, name, code }
    });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '题材名称或代码已存在' });
    }
    console.error('创建题材失败:', err);
    res.status(500).json({ success: false, message: '创建题材失败' });
  }
};

exports.updateGenre = async (req, res) => {
  try {
    const { id } = req.params;
    const { name, code } = req.body;
    
    if (!name || !code) {
      return res.status(400).json({ success: false, message: '题材名称和代码不能为空' });
    }
    
    const result = await db.query('UPDATE genres SET name = ?, code = ? WHERE id = ?', [name, code, id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '题材不存在' });
    }
    
    res.json({ success: true, message: '题材更新成功' });
  } catch (err) {
    if (err.code === 'ER_DUP_ENTRY') {
      return res.status(400).json({ success: false, message: '题材名称或代码已存在' });
    }
    console.error('更新题材失败:', err);
    res.status(500).json({ success: false, message: '更新题材失败' });
  }
};

exports.deleteGenre = async (req, res) => {
  try {
    const { id } = req.params;
    const result = await db.query('DELETE FROM genres WHERE id = ?', [id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '题材不存在' });
    }
    
    res.json({ success: true, message: '题材删除成功' });
  } catch (err) {
    console.error('删除题材失败:', err);
    res.status(500).json({ success: false, message: '删除题材失败' });
  }
};

// ==================== 评论管理 ====================

// 获取所有评论（管理用）
exports.getAllComments = async (req, res) => {
  try {
    const { page = 1, pageSize = 20, search = '' } = req.query;
    const Comment = require('../models/Comment');
    const comments = await Comment.findAllForAdmin(page, parseInt(pageSize), search);
    const total = await Comment.countAllForAdmin(search);

    res.json({
      success: true,
      data: {
        comments,
        total,
        page: parseInt(page),
        pageSize: parseInt(pageSize),
        totalPages: Math.ceil(total / parseInt(pageSize))
      }
    });
  } catch (err) {
    console.error('获取评论列表失败:', err);
    res.status(500).json({ success: false, message: '获取评论列表失败' });
  }
};

// 管理员删除任意评论
exports.deleteComment = async (req, res) => {
  try {
    const { id } = req.params;
    const Comment = require('../models/Comment');

    const exists = await Comment.exists(id);
    if (!exists) {
      return res.status(404).json({ success: false, message: '评论不存在' });
    }

    const deleted = await Comment.delete(id, null, true);
    if (!deleted) {
      return res.status(500).json({ success: false, message: '删除评论失败' });
    }

    res.json({ success: true, message: '评论删除成功' });
  } catch (err) {
    console.error('删除评论失败:', err);
    res.status(500).json({ success: false, message: '删除评论失败' });
  }
};

// 管理员置顶/取消置顶评论
exports.togglePinComment = async (req, res) => {
  try {
    const { id } = req.params;
    const { isPinned } = req.body;
    const Comment = require('../models/Comment');

    const exists = await Comment.exists(id);
    if (!exists) {
      return res.status(404).json({ success: false, message: '评论不存在' });
    }

    const updated = await Comment.togglePin(id, isPinned);
    if (!updated) {
      return res.status(500).json({ success: false, message: '操作失败' });
    }

    res.json({
      success: true,
      message: isPinned ? '评论已置顶' : '评论已取消置顶',
      data: { isPinned: !!isPinned }
    });
  } catch (err) {
    console.error('置顶评论失败:', err);
    res.status(500).json({ success: false, message: '置顶评论失败' });
  }
};

// ==================== 管理员个人信息 ====================

// 获取管理员个人信息
exports.getAdminProfile = async (req, res) => {
  try {
    const { id } = req.params;
    const users = await db.query('SELECT id, username, email, avatar_url, role_id, created_at FROM users WHERE id = ? AND role_id = 1', [id]);
    
    if (users.length === 0) {
      return res.status(404).json({ success: false, message: '管理员不存在' });
    }
    
    res.json({ success: true, data: users[0] });
  } catch (err) {
    console.error('获取管理员信息失败:', err);
    res.status(500).json({ success: false, message: '获取管理员信息失败' });
  }
};

// 更新管理员个人信息
exports.updateAdminProfile = async (req, res) => {
  try {
    const { id } = req.params;
    const { email, avatar_url } = req.body;
    
    // 验证是否是管理员
    const admins = await db.query('SELECT * FROM users WHERE id = ? AND role_id = 1', [id]);
    if (admins.length === 0) {
      return res.status(404).json({ success: false, message: '管理员不存在' });
    }
    
    const updateFields = [];
    const updateValues = [];
    
    if (email !== undefined) {
      updateFields.push('email = ?');
      updateValues.push(email);
    }
    if (avatar_url !== undefined) {
      updateFields.push('avatar_url = ?');
      updateValues.push(avatar_url);
    }
    
    if (updateFields.length > 0) {
      updateValues.push(id);
      await db.query(`UPDATE users SET ${updateFields.join(', ')} WHERE id = ?`, updateValues);
    }
    
    // 返回更新后的信息
    const updatedUser = await db.query('SELECT id, username, email, avatar_url, role_id, created_at FROM users WHERE id = ?', [id]);
    res.json({ success: true, message: '个人信息更新成功', data: updatedUser[0] });
  } catch (err) {
    console.error('更新管理员信息失败:', err);
    res.status(500).json({ success: false, message: '更新管理员信息失败' });
  }
};

// 修改管理员密码
exports.changeAdminPassword = async (req, res) => {
  try {
    const { id } = req.params;
    const { oldPassword, newPassword } = req.body;
    
    if (!oldPassword || !newPassword) {
      return res.status(400).json({ success: false, message: '旧密码和新密码不能为空' });
    }
    
    if (newPassword.length < 6) {
      return res.status(400).json({ success: false, message: '新密码长度不能少于6位' });
    }
    
    // 验证旧密码
    const admins = await db.query('SELECT * FROM users WHERE id = ? AND role_id = 1', [id]);
    if (admins.length === 0) {
      return res.status(404).json({ success: false, message: '管理员不存在' });
    }
    
    const bcrypt = require('bcryptjs');
    const isValid = await bcrypt.compare(oldPassword, admins[0].password_hash);
    if (!isValid) {
      return res.status(400).json({ success: false, message: '旧密码不正确' });
    }
    
    // 更新密码
    const hashedPassword = await bcrypt.hash(newPassword, 10);
    await db.query('UPDATE users SET password_hash = ? WHERE id = ?', [hashedPassword, id]);
    
    res.json({ success: true, message: '密码修改成功' });
  } catch (err) {
    console.error('修改密码失败:', err);
    res.status(500).json({ success: false, message: '修改密码失败' });
  }
};

// ==================== A/B 测试实验管理 ====================

/**
 * POST /api/admin/experiments
 * 创建新实验（含策略列表）
 */
exports.createExperiment = async (req, res) => {
  try {
    const { name, description, splitMode, startTime, endTime, strategies } = req.body;

    if (!name || !strategies || !Array.isArray(strategies) || strategies.length === 0) {
      return res.status(400).json({ success: false, message: '实验名称和策略列表不能为空' });
    }
    if (!['fixed', 'bandit'].includes(splitMode)) {
      return res.status(400).json({ success: false, message: 'splitMode 必须为 fixed 或 bandit' });
    }

    const conn = await db.pool.getConnection();
    try {
      await conn.beginTransaction();

      const [expResult] = await conn.query(
        `INSERT INTO ab_experiments (name, description, split_mode, start_time, end_time, status, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, 'running', NOW(), NOW())`,
        [name, description || '', splitMode, startTime || null, endTime || null]
      );
      const experimentId = expResult.insertId;

      for (const s of strategies) {
        await conn.query(
          `INSERT INTO ab_strategies (experiment_id, name, algorithm, algorithm_key, traffic_percentage, weight_source, bandit_alpha, bandit_beta, is_control, min_traffic, coldstart_end_time)
           VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, DATE_ADD(NOW(), INTERVAL 2 HOUR))`,
          [
            experimentId,
            s.name || '未命名策略',
            s.algorithm || 'hybrid',
            s.algorithm || 'hybrid',
            s.trafficPercentage || 0,
            splitMode === 'bandit' ? 'bandit' : 'manual',
            s.isControl ? 1 : 0,
            s.minTraffic || 5
          ]
        );
      }

      await conn.commit();
      res.json({ success: true, message: '实验创建成功', data: { id: experimentId } });
    } catch (err) {
      await conn.rollback();
      throw err;
    } finally {
      conn.release();
    }
  } catch (err) {
    console.error('创建实验失败:', err);
    res.status(500).json({ success: false, message: '创建实验失败: ' + err.message });
  }
};

/**
 * PUT /api/admin/experiments/:id
 * 修改实验配置
 */
exports.updateExperiment = async (req, res) => {
  try {
    const { id } = req.params;
    const { name, description, splitMode, startTime, endTime, status } = req.body;

    // 仅允许修改进行中或未开始的实验
    const existing = await db.query('SELECT id, status FROM ab_experiments WHERE id = ?', [id]);
    if (existing.length === 0) {
      return res.status(404).json({ success: false, message: '实验不存在' });
    }
    if (['stopped', 'archived'].includes(existing[0].status)) {
      return res.status(400).json({ success: false, message: '已停止或归档的实验不可修改' });
    }

    const updates = [];
    const params = [];
    if (name !== undefined) { updates.push('name = ?'); params.push(name); }
    if (description !== undefined) { updates.push('description = ?'); params.push(description); }
    if (splitMode !== undefined) { updates.push('split_mode = ?'); params.push(splitMode); }
    if (startTime !== undefined) { updates.push('start_time = ?'); params.push(startTime); }
    if (endTime !== undefined) { updates.push('end_time = ?'); params.push(endTime); }
    if (status !== undefined) { updates.push('status = ?'); params.push(status); }

    if (updates.length === 0) {
      return res.status(400).json({ success: false, message: '没有可更新的字段' });
    }

    updates.push('updated_at = NOW()');
    params.push(id);

    await db.query(`UPDATE ab_experiments SET ${updates.join(', ')} WHERE id = ?`, params);
    res.json({ success: true, message: '实验更新成功' });
  } catch (err) {
    console.error('更新实验失败:', err);
    res.status(500).json({ success: false, message: '更新实验失败: ' + err.message });
  }
};

/**
 * GET /api/admin/experiments
 * 获取所有实验列表，支持按状态筛选
 */
exports.getExperiments = async (req, res) => {
  try {
    const { status } = req.query;
    let sql = `
      SELECT e.*,
        (SELECT COUNT(*) FROM ab_strategies WHERE experiment_id = e.id) AS strategy_count
      FROM ab_experiments e
    `;
    const params = [];
    if (status) {
      sql += ' WHERE e.status = ?';
      params.push(status);
    }
    sql += ' ORDER BY e.created_at DESC';

    const experiments = await db.query(sql, params);

    // 为每个实验加载策略详情
    for (const exp of experiments) {
      const strategies = await db.query(
        'SELECT * FROM ab_strategies WHERE experiment_id = ? ORDER BY id',
        [exp.id]
      );
      exp.strategies = strategies;
    }

    res.json({ success: true, data: experiments });
  } catch (err) {
    console.error('获取实验列表失败:', err);
    res.status(500).json({ success: false, message: '获取实验列表失败' });
  }
};

/**
 * GET /api/admin/experiments/:id
 * 获取单个实验详情及当前各策略实时指标
 */

function wilsonCI(successes, trials, z = 1.96) {
  if (trials === 0) return { lower: 0, upper: 0, center: 0 };
  const p = successes / trials;
  const denominator = 1 + z * z / trials;
  const center = (p + z * z / (2 * trials)) / denominator;
  const margin = z * Math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator;
  return { lower: Math.max(0, center - margin), upper: Math.min(1, center + margin), center };
}

/**
 * 使用 Wilson 置信区间和 z-test 计算与对照组的 p-value
 */
function wilsonPValue(s1, n1, s2, n2) {
  if (n1 === 0 || n2 === 0) return 1;
  const p1 = s1 / n1;
  const p2 = s2 / n2;
  const pPool = (s1 + s2) / (n1 + n2);
  if (pPool === 0 || pPool === 1) return 1;
  const se = Math.sqrt(pPool * (1 - pPool) * (1 / n1 + 1 / n2));
  const z = (p1 - p2) / se;
  // two-sided p-value via normal approximation
  const absZ = Math.abs(z);
  // simple normal CDF approximation
  const pVal = 2 * (1 - normalCDFApprox(absZ));
  return Math.min(1, Math.max(0, pVal));
}

function normalCDFApprox(x) {
  // Abramowitz & Stegun approximation
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-x * x / 2);
  const prob = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - prob : prob;
}

function gammaSample(shape) {
  const a = Math.max(shape, 0.01);
  if (a < 1) {
    let iter = 0;
    const b = (Math.E + a) / Math.E;
    while (iter < 100) {
      iter++;
      const u = Math.random();
      if (u <= 1 / b) {
        const x = Math.pow(u * b, 1 / a);
        if (Math.random() <= Math.exp(-x)) return x;
      } else {
        const x = -Math.log((b - u * b) / a);
        if (Math.random() <= Math.pow(x, a - 1)) return x;
      }
    }
    return a; // fallback after max retries
  }
  const d = a - 1 / 3;
  const c = 1 / Math.sqrt(9 * d);
  let iter = 0;
  while (iter < 100) {
    iter++;
    let x, v;
    do {
      x = normalRand();
      v = 1 + c * x;
    } while (v <= 0);
    v = v * v * v;
    const u = Math.random();
    if (u < 1 - 0.0331 * x * x * x * x) return d * v;
    if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }
  return d; // fallback
}

function normalRand() {
  let u1, u2;
  do { u1 = Math.random(); } while (u1 === 0);
  u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

function sampleBetaSafe(a, b) {
  const alpha = Math.max(Number.isFinite(a) ? a : 1, 0.001);
  const beta = Math.max(Number.isFinite(b) ? b : 1, 0.001);
  const g1 = gammaSample(alpha);
  const g2 = gammaSample(beta);
  const sum = g1 + g2;
  return sum > 1e-12 ? g1 / sum : 0.5;
}

async function bayesianApproxWinProb(a1, b1, others) {
  if (!others || others.length === 0) return null;
  const samples = 2000;
  const batch = 200;
  let wins = 0;
  for (let i = 0; i < samples; i += batch) {
    const end = Math.min(i + batch, samples);
    let batchWins = 0;
    for (let j = i; j < end; j++) {
      const s1 = sampleBetaSafe(a1, b1);
      let best = true;
      for (const o of others) {
        if (sampleBetaSafe(o.alpha || 1, o.beta || 1) > s1) { best = false; break; }
      }
      if (best) batchWins++;
    }
    wins += batchWins;
    if (i + batch < samples) {
      await new Promise(r => setImmediate(r));
    }
  }
  return wins / samples;
}

exports.getExperimentById = async (req, res) => {
  try {
    const { id } = req.params;

    const experiments = await db.query('SELECT * FROM ab_experiments WHERE id = ?', [id]);
    if (experiments.length === 0) {
      return res.status(404).json({ success: false, message: '实验不存在' });
    }

    const exp = experiments[0];
    exp.strategies = await db.query(
      'SELECT * FROM ab_strategies WHERE experiment_id = ? ORDER BY id',
      [id]
    );

    // 第一遍：加载每个策略的 ab_results
    for (const strat of exp.strategies) {
      const results = await db.query(
        'SELECT * FROM ab_results WHERE experiment_id = ? AND strategy_id = ? ORDER BY analysis_time DESC LIMIT 1',
        [id, strat.id]
      );
      if (results.length > 0) {
        strat.latestMetrics = results[0];
        // 兜底：如果 DB 中 CI 为 NULL，实时计算
        const m = strat.latestMetrics;
        if ((m.ctr_ci_lower == null || m.ctr_ci_upper == null) && m.total_exposures > 0) {
          const positives = m.positive_events || m.total_clicks || 0;
          const ci = wilsonCI(positives, m.total_exposures);
          m.ctr_ci_lower = ci.lower;
          m.ctr_ci_upper = ci.upper;
        }
        // 兜底：如果 ctr 为 NULL，实时计算
        if (m.ctr == null && m.total_exposures > 0) {
          const positives = m.positive_events || m.total_clicks || 0;
          m.ctr = positives / m.total_exposures;
        }
        // 兜底：统一 total_clicks (兼容旧记录用 positive_events)
        if ((m.total_clicks == null || m.total_clicks === 0) && m.positive_events > 0) {
          m.total_clicks = m.positive_events;
        }
      }
    }

    // 第二遍：计算 win_probability（需所有策略数据就绪）
    const resultsWithData = exp.strategies.filter(s => s.latestMetrics);
    for (const strat of exp.strategies) {
      if (!strat.latestMetrics) {
        // 实时回退：直接从 behaviors 统计，同时计算 CI 和与对照组的 p-value
        try {
          const behavCounts = await db.query(
            `SELECT
              COUNT(*) AS total_behaviors,
              COUNT(DISTINCT user_id) AS unique_users,
              SUM(CASE WHEN behavior_type IN ('view', 'like', 'collect') OR (behavior_type = 'rate' AND rating >= 4) THEN 1 ELSE 0 END) AS total_positive,
              AVG(CASE WHEN behavior_type = 'rate' THEN rating ELSE NULL END) AS avg_rating
            FROM users_movies_behaviors
            WHERE experiment_id = ? AND strategy_id = ?`,
            [id, strat.id]
          );
          const bc = behavCounts[0];
          if (bc && bc.total_behaviors > 0) {
            const total = parseInt(bc.total_behaviors) || 0;
            const positive = parseInt(bc.total_positive) || 0;
            const ci = wilsonCI(positive, total);
            strat._fallbackData = { total, positive, ci, avgRating: parseFloat(bc.avg_rating) || 0, uniqueUsers: parseInt(bc.unique_users) || 0 };
          }
        } catch (e) { /* behaviors 表可能缺少列 */ }
        if (!strat.latestMetrics) continue;
      }

      // 计算后验获胜概率
      const m = strat.latestMetrics;
      if (m.win_probability == null && m.bandit_alpha != null && m.bandit_beta != null) {
        const others = resultsWithData
          .filter(s => s.id !== strat.id && s.latestMetrics && s.latestMetrics.bandit_alpha != null)
          .map(s => ({ alpha: s.latestMetrics.bandit_alpha, beta: s.latestMetrics.bandit_beta }));
        if (others.length > 0) {
          m.win_probability = await bayesianApproxWinProb(m.bandit_alpha, m.bandit_beta, others);
        }
      }
    }

    // 第三遍：对仅有 behavior 回退数据的策略，构建 latestMetrics 并计算统计量
    const control = exp.strategies.find(s => s.is_control === 1) || exp.strategies[0];
    const controlFb = control._fallbackData;

    // 兜底：为所有治疗组补算缺失的 p-value
    if (control && control.latestMetrics) {
      const cm = control.latestMetrics;
      const cPos = cm.positive_events || cm.total_clicks || 0;
      const cTotal = cm.total_exposures || 0;
      for (const strat of exp.strategies) {
        if (!strat.latestMetrics || strat.id === control.id) continue;
        const m = strat.latestMetrics;
        if (m.p_value == null && cTotal > 0 && m.total_exposures > 0) {
          const tPos = m.positive_events || m.total_clicks || 0;
          m.p_value = wilsonPValue(tPos, m.total_exposures, cPos, cTotal);
        }
      }
    }

    for (const strat of exp.strategies) {
      if (strat.latestMetrics) continue;
      const fb = strat._fallbackData;
      if (!fb) continue;

      const ctr = fb.total > 0 ? (fb.positive / fb.total) : 0;
      let pValue = null;
      let winProb = null;

      if (controlFb && strat.id !== control.id) {
        pValue = wilsonPValue(fb.positive, fb.total, controlFb.positive, controlFb.total);
        // 近似贝叶斯获胜概率
        const a1 = fb.positive + 1, b1 = Math.max(fb.total - fb.positive, 0) + 1;
        const a2 = controlFb.positive + 1, b2 = Math.max(controlFb.total - controlFb.positive, 0) + 1;
        winProb = await bayesianApproxWinProb(a1, b1, [{ alpha: a2, beta: b2 }]);
      }

      strat.latestMetrics = {
        total_exposures: fb.total,
        total_behaviors: fb.total,
        total_positive: fb.positive,
        unique_users: fb.uniqueUsers,
        avg_rating: fb.avgRating,
        ctr: ctr,
        ctr_ci_lower: fb.ci.lower,
        ctr_ci_upper: fb.ci.upper,
        p_value: pValue,
        win_probability: winProb,
        is_converged: (pValue !== null && pValue < 0.05) ? 1 : 0,
        bandit_alpha: fb.positive + 1,
        bandit_beta: Math.max(fb.total - fb.positive, 0) + 1
      };
    }

    // 第四遍：为对照组补算 p 值与获胜概率（与最佳实验组对比）
    if (control && control.latestMetrics) {
      const cm = control.latestMetrics;
      if (cm.p_value == null) {
        const bestTreatment = exp.strategies
          .filter(s => s.id !== control.id && s.latestMetrics && s.latestMetrics.ctr != null)
          .sort((a, b) => b.latestMetrics.ctr - a.latestMetrics.ctr)[0];
        if (bestTreatment) {
          const bm = bestTreatment.latestMetrics;
          const cPos = cm.positive_events || cm.total_clicks || 0;
          const cTotal = cm.total_exposures || 0;
          const bPos = bm.positive_events || bm.total_clicks || 0;
          const bTotal = bm.total_exposures || 0;
          if (cTotal > 0 && bTotal > 0) {
            cm.p_value = wilsonPValue(cPos, cTotal, bPos, bTotal);
            cm.is_converged = (cm.p_value !== null && cm.p_value < 0.05) ? 1 : 0;
          }
          const a1 = cPos + 1, b1 = Math.max(cTotal - cPos, 0) + 1;
          const a2 = bPos + 1, b2 = Math.max(bTotal - bPos, 0) + 1;
          cm.win_probability = await bayesianApproxWinProb(a1, b1, [{ alpha: a2, beta: b2 }]);
        }
      }
    }

    // 清理临时数据
    for (const strat of exp.strategies) {
      delete strat._fallbackData;
    }

    res.json({ success: true, data: exp });
  } catch (err) {
    console.error('获取实验详情失败:', err);
    res.status(500).json({ success: false, message: '获取实验详情失败' });
  }
};

/**
 * POST /api/admin/experiments/:id/stop
 * 手动终止实验：全量推优（推选胜出策略）或回退默认
 */
exports.stopExperiment = async (req, res) => {
  try {
    const { id } = req.params;
    const { pushStrategyId, fallbackToDefault } = req.body;

    const experiments = await db.query('SELECT * FROM ab_experiments WHERE id = ?', [id]);
    if (experiments.length === 0) {
      return res.status(404).json({ success: false, message: '实验不存在' });
    }

    const conn = await db.pool.getConnection();
    try {
      await conn.beginTransaction();

      // 更新实验状态
      await conn.query('UPDATE ab_experiments SET status = ?, updated_at = NOW() WHERE id = ?', ['stopped', id]);

      if (pushStrategyId) {
        // 将指定策略推全：更新其 traffic_percentage 为 100%
        await conn.query(
          'UPDATE ab_strategies SET traffic_percentage = 100, weight_source = ? WHERE id = ?',
          ['promoted', pushStrategyId]
        );
        // 其他策略流量归零
        await conn.query(
          'UPDATE ab_strategies SET traffic_percentage = 0 WHERE experiment_id = ? AND id != ?',
          [id, pushStrategyId]
        );

        // 记录推全日志
        await conn.query(
          'INSERT INTO ab_results (experiment_id, strategy_id, analysis_time, period_start, period_end, is_converged) VALUES (?, ?, NOW(), NOW(), NOW(), 1)',
          [id, pushStrategyId]
        );
      }

      if (fallbackToDefault) {
        // 回退：将所有策略权重重置为手动
        await conn.query(
          'UPDATE ab_strategies SET traffic_percentage = 0, weight_source = ? WHERE experiment_id = ?',
          ['manual', id]
        );
      }

      await conn.commit();
      res.json({ success: true, message: '实验已终止' });
    } catch (err) {
      await conn.rollback();
      throw err;
    } finally {
      conn.release();
    }
  } catch (err) {
    console.error('终止实验失败:', err);
    res.status(500).json({ success: false, message: '终止实验失败: ' + err.message });
  }
};

/**
 * POST /api/admin/experiments/:id/archive
 * 归档实验
 */
exports.archiveExperiment = async (req, res) => {
  try {
    const { id } = req.params;

    const result = await db.query(
      'UPDATE ab_experiments SET status = ?, updated_at = NOW() WHERE id = ? AND status = ?',
      ['archived', id, 'stopped']
    );

    if (result.affectedRows === 0) {
      return res.status(400).json({ success: false, message: '只有已停止的实验才能归档' });
    }

    res.json({ success: true, message: '实验已归档' });
  } catch (err) {
    console.error('归档实验失败:', err);
    res.status(500).json({ success: false, message: '归档实验失败' });
  }
};

/**
 * GET /api/admin/experiments/:id/metrics
 * 返回实验各策略实时指标及趋势图数据
 */
exports.getExperimentMetrics = async (req, res) => {
  try {
    const { id } = req.params;
    const { timeRange } = req.query; // 可选: 7d, 30d, 90d

    const lookbackDays = timeRange ? parseInt(timeRange.replace('d', '')) : 7;

    // 获取策略列表
    const strategies = await db.query(
      'SELECT id, name, algorithm FROM ab_strategies WHERE experiment_id = ? ORDER BY id',
      [id]
    );

    // 获取各策略的聚合指标
    const metricsByStrategy = {};
    for (const strat of strategies) {
      const metrics = await db.query(
        `SELECT
           COUNT(*) AS total_exposures,
           SUM(CASE WHEN behavior_type IN ('view', 'like', 'collect') OR (behavior_type = 'rate' AND rating >= 4) THEN 1 ELSE 0 END) AS total_positive,
           COUNT(DISTINCT user_id) AS unique_users,
           AVG(CASE WHEN behavior_type = 'rate' THEN rating ELSE NULL END) AS avg_rating,
           ROUND(SUM(CASE WHEN behavior_type IN ('view', 'like', 'collect') OR (behavior_type = 'rate' AND rating >= 4) THEN 1 ELSE 0 END) / COUNT(*) * 100, 4) AS ctr
         FROM users_movies_behaviors
         WHERE experiment_id = ? AND strategy_id = ? AND created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)`,
        [id, strat.id, lookbackDays]
      );

      // 获取最近 N 天的时序趋势
      const trend = await db.query(
        `SELECT DATE(created_at) AS date,
                COUNT(*) AS exposures,
                SUM(CASE WHEN behavior_type IN ('view', 'like', 'collect') OR (behavior_type = 'rate' AND rating >= 4) THEN 1 ELSE 0 END) AS positives
         FROM users_movies_behaviors
         WHERE experiment_id = ? AND strategy_id = ? AND created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)
         GROUP BY DATE(created_at) ORDER BY date`,
        [id, strat.id, lookbackDays]
      );

      metricsByStrategy[strat.id] = {
        ...metrics[0],
        trend
      };
    }

    res.json({
      success: true,
      data: {
        experimentId: parseInt(id),
        strategies: strategies.map(s => ({
          ...s,
          metrics: metricsByStrategy[s.id] || null
        })),
        lookbackDays
      }
    });
  } catch (err) {
    console.error('获取实验指标失败:', err);
    res.status(500).json({ success: false, message: '获取实验指标失败: ' + err.message });
  }
};

/**
 * POST /api/admin/experiments/:id/seed
 * 为指定实验生成模拟测试数据（仅限非生产环境使用）
 * body: { userCount?: 30, behaviorCount?: 200 }
 */
exports.seedExperiment = async (req, res) => {
  try {
    const { id } = req.params;
    const { userCount = 30, behaviorCount = 200 } = req.body;

    const exps = await db.query('SELECT * FROM ab_experiments WHERE id = ?', [id]);
    if (exps.length === 0) {
      return res.status(404).json({ success: false, message: '实验不存在' });
    }
    const exp = exps[0];

    const strategies = await db.query(
      'SELECT * FROM ab_strategies WHERE experiment_id = ? ORDER BY id', [id]
    );
    if (strategies.length === 0) {
      return res.status(400).json({ success: false, message: '实验无策略' });
    }

    const users = await db.query(
      'SELECT id FROM users ORDER BY id LIMIT ?', [Math.max(userCount, 10)]
    );
    const movies = await db.query(
      'SELECT id FROM movies ORDER BY RAND() LIMIT ?', [Math.max(Math.ceil(behaviorCount / strategies.length), 20)]
    );

    if (users.length === 0) {
      return res.status(400).json({ success: false, message: '数据库无用户，请先添加用户' });
    }
    if (movies.length === 0) {
      return res.status(400).json({ success: false, message: '数据库无电影，请先添加电影' });
    }

    // 用哈希分桶模拟固定比例分流，计算每个策略的流量区间
    const crypto = require('crypto');
    function computeBucket(uid) {
      const hash = crypto.createHash('md5').update(String(uid)).digest('hex');
      return parseInt(hash.substring(0, 8), 16) % 100;
    }

    let rangeStart = 0;
    const stratRanges = strategies.map(s => {
      const end = rangeStart + Math.round(parseFloat(s.traffic_percentage)) - 1;
      const r = { id: s.id, name: s.name, start: rangeStart, end: end >= 100 ? 99 : end };
      rangeStart = end + 1;
      return r;
    });

    let insertedViews = 0, insertedRates = 0;
    const values = [];
    const batchSize = 100;

    for (let i = 0; i < behaviorCount; i++) {
      const user = users[Math.floor(Math.random() * users.length)];
      const movie = movies[Math.floor(Math.random() * movies.length)];
      const bucket = computeBucket(user.id);
      const sr = stratRanges.find(r => bucket >= r.start && bucket <= r.end) || stratRanges[0];

      // 随机行为类型：70% view, 30% rate
      const isRate = Math.random() < 0.3;

      if (isRate) {
        const rating = Math.floor(Math.random() * 3) + 3;
        const requestId = require('crypto').randomUUID();
        values.push(
          `(${user.id}, ${movie.id}, 'rate', ${rating}, '${requestId}', ${id}, ${sr.id}, 0)`
        );
        insertedRates++;
      } else {
        const requestId = require('crypto').randomUUID();
        const progress = Math.floor(Math.random() * 7200);
        values.push(
          `(${user.id}, ${movie.id}, 'view', NULL, '${requestId}', ${id}, ${sr.id}, ${progress})`
        );
        insertedViews++;
      }

      if (values.length >= batchSize) {
        const cols = 'user_id, movie_id, behavior_type, rating, request_id, experiment_id, strategy_id';
        const extraCols = ', progress_seconds';
        const sql = `INSERT INTO users_movies_behaviors (${cols}${extraCols}) VALUES ${values.join(',')}`;
        await db.query(sql);
        values.length = 0;
      }
    }

    if (values.length > 0) {
      const cols = 'user_id, movie_id, behavior_type, rating, request_id, experiment_id, strategy_id';
      const extraCols = ', progress_seconds';
      const sql = `INSERT INTO users_movies_behaviors (${cols}${extraCols}) VALUES ${values.join(',')}`;
      await db.query(sql);
    }

    res.json({
      success: true,
      message: `已生成 ${insertedViews} 条观看 + ${insertedRates} 条评分记录`,
      data: { views: insertedViews, rates: insertedRates }
    });
  } catch (err) {
    console.error('种子数据生成失败:', err);
    res.status(500).json({ success: false, message: '种子数据生成失败: ' + err.message });
  }
};
