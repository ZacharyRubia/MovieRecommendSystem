const db = require('../config/db');

// ==================== 电影管理 ====================

// 获取所有电影（带分页、搜索）
exports.getAllMovies = async (req, res) => {
  try {
    const { page = 1, pageSize = 20, search = '' } = req.query;
    const offset = (page - 1) * pageSize;
    
    let sql = `
      SELECT m.*, 
             GROUP_CONCAT(DISTINCT t.name SEPARATOR ', ') as tags,
             GROUP_CONCAT(DISTINCT d.name SEPARATOR ', ') as directors,
             GROUP_CONCAT(DISTINCT a.name SEPARATOR ', ') as actors
      FROM movie m
      LEFT JOIN movie_tag mt ON m.id = mt.movie_id
      LEFT JOIN tag t ON mt.tag_id = t.id
      LEFT JOIN movie_director md ON m.id = md.movie_id
      LEFT JOIN director d ON md.director_id = d.id
      LEFT JOIN movie_actor ma ON m.id = ma.movie_id
      LEFT JOIN actor a ON ma.actor_id = a.id
      WHERE 1=1
    `;
    
    const params = [];
    
    if (search) {
      sql += ` AND m.title LIKE ?`;
      params.push(`%${search}%`);
    }
    
    sql += ` GROUP BY m.id ORDER BY m.created_at DESC LIMIT ? OFFSET ?`;
    params.push(parseInt(pageSize), parseInt(offset));
    
    const movies = await db.query(sql, params);
    
    // 获取总数
    let countSql = `SELECT COUNT(*) as total FROM movie WHERE 1=1`;
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
    const movies = await db.query('SELECT * FROM movie WHERE id = ?', [id]);
    if (movies.length === 0) {
      return res.status(404).json({ success: false, message: '电影不存在' });
    }
    
    const movie = movies[0];
    
    // 获取关联标签
    const tags = await db.query(`
      SELECT t.id, t.name FROM tag t
      JOIN movie_tag mt ON t.id = mt.tag_id
      WHERE mt.movie_id = ?
    `, [id]);
    
    // 获取关联导演
    const directors = await db.query(`
      SELECT d.id, d.name FROM director d
      JOIN movie_director md ON d.id = md.director_id
      WHERE md.movie_id = ?
    `, [id]);
    
    // 获取关联演员
    const actors = await db.query(`
      SELECT a.id, a.name, ma.role FROM actor a
      JOIN movie_actor ma ON a.id = ma.actor_id
      WHERE ma.movie_id = ?
    `, [id]);
    
    res.json({
      success: true,
      data: {
        ...movie,
        tags,
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
    const { title, description, cover_url, video_url, release_year, duration, tags = [], directors = [], actors = [] } = req.body;
    
    if (!title) {
      return res.status(400).json({ success: false, message: '电影标题不能为空' });
    }
    
    // 插入电影
    const result = await db.query(
      'INSERT INTO movie (title, description, cover_url, video_url, release_year, duration) VALUES (?, ?, ?, ?, ?, ?)',
      [title, description || '', cover_url || '', video_url || '', release_year || null, duration || null]
    );
    
    const movieId = result.insertId;
    
    // 关联标签
    if (tags && tags.length > 0) {
      const tagValues = tags.map(tagId => [movieId, tagId]);
      await db.query('INSERT IGNORE INTO movie_tag (movie_id, tag_id) VALUES ?', [tagValues]);
    }
    
    // 关联导演
    if (directors && directors.length > 0) {
      const directorValues = directors.map(directorId => [movieId, directorId]);
      await db.query('INSERT IGNORE INTO movie_director (movie_id, director_id) VALUES ?', [directorValues]);
    }
    
    // 关联演员
    if (actors && actors.length > 0) {
      const actorValues = actors.map(actor => [movieId, actor.id, actor.role || '']);
      await db.query('INSERT IGNORE INTO movie_actor (movie_id, actor_id, role) VALUES ?', [actorValues]);
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
    const { title, description, cover_url, video_url, release_year, duration, tags, directors, actors } = req.body;
    
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
      await db.query(`UPDATE movie SET ${updateFields.join(', ')} WHERE id = ?`, updateValues);
    }
    
    // 更新标签关联
    if (tags !== undefined) {
      await db.query('DELETE FROM movie_tag WHERE movie_id = ?', [id]);
      if (tags.length > 0) {
        const tagValues = tags.map(tagId => [id, tagId]);
        await db.query('INSERT INTO movie_tag (movie_id, tag_id) VALUES ?', [tagValues]);
      }
    }
    
    // 更新导演关联
    if (directors !== undefined) {
      await db.query('DELETE FROM movie_director WHERE movie_id = ?', [id]);
      if (directors.length > 0) {
        const directorValues = directors.map(directorId => [id, directorId]);
        await db.query('INSERT INTO movie_director (movie_id, director_id) VALUES ?', [directorValues]);
      }
    }
    
    // 更新演员关联
    if (actors !== undefined) {
      await db.query('DELETE FROM movie_actor WHERE movie_id = ?', [id]);
      if (actors.length > 0) {
        const actorValues = actors.map(actor => [id, actor.id, actor.role || '']);
        await db.query('INSERT INTO movie_actor (movie_id, actor_id, role) VALUES ?', [actorValues]);
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
    
    const result = await db.query('DELETE FROM movie WHERE id = ?', [id]);
    
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
          'INSERT INTO movie (title, description, cover_url, video_url, release_year, duration) VALUES (?, ?, ?, ?, ?, ?)',
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
    const tags = await db.query('SELECT * FROM tag ORDER BY created_at DESC');
    res.json({ success: true, data: tags });
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
    
    const result = await db.query('INSERT INTO tag (name) VALUES (?)', [name]);
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
    
    const result = await db.query('UPDATE tag SET name = ? WHERE id = ?', [name, id]);
    
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
    const result = await db.query('DELETE FROM tag WHERE id = ?', [id]);
    
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
    const directors = await db.query('SELECT * FROM director ORDER BY created_at DESC');
    res.json({ success: true, data: directors });
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
      'INSERT INTO director (name, avatar_url, description) VALUES (?, ?, ?)',
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
      'UPDATE director SET name = ?, avatar_url = ?, description = ? WHERE id = ?',
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
    const result = await db.query('DELETE FROM director WHERE id = ?', [id]);
    
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
    const actors = await db.query('SELECT * FROM actor ORDER BY created_at DESC');
    res.json({ success: true, data: actors });
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
      'INSERT INTO actor (name, avatar_url, description) VALUES (?, ?, ?)',
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
      'UPDATE actor SET name = ?, avatar_url = ?, description = ? WHERE id = ?',
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
    const result = await db.query('DELETE FROM actor WHERE id = ?', [id]);
    
    if (result.affectedRows === 0) {
      return res.status(404).json({ success: false, message: '演员不存在' });
    }
    
    res.json({ success: true, message: '演员删除成功' });
  } catch (err) {
    console.error('删除演员失败:', err);
    res.status(500).json({ success: false, message: '删除演员失败' });
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
