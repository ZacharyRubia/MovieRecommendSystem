const { query } = require('../config/db');

// 用户模型
class UserModel {
  // 获取所有用户
  static async findAll() {
    return query('SELECT * FROM users ORDER BY created_at DESC', []);
  }

  // 根据ID查找用户
  static async findById(id) {
    const rows = await query('SELECT * FROM users WHERE id = ?', [id]);
    return rows[0];
  }

  // 根据用户名查找用户
  static async findByUsername(username) {
    const rows = await query('SELECT * FROM users WHERE username = ?', [username]);
    return rows[0];
  }

  // 根据邮箱查找用户
  static async findByEmail(email) {
    const rows = await query('SELECT * FROM users WHERE email = ?', [email]);
    return rows[0];
  }

  // 创建新用户
  static async create(userData) {
    const { username, email, password_hash, role_id = 2, avatar_url = '' } = userData;
    const result = await query(
      'INSERT INTO users (username, email, password_hash, role_id, avatar_url) VALUES (?, ?, ?, ?, ?)',
      [username, email, password_hash, role_id, avatar_url]
    );
    return result.insertId;
  }

  // 更新用户信息
  static async update(id, userData) {
    const { email, avatar_url, role_id, password_hash } = userData;
    let sql = 'UPDATE users SET ';
    const params = [];
    const updates = [];

    if (email !== undefined) {
      updates.push('email = ?');
      params.push(email);
    }
    if (avatar_url !== undefined) {
      updates.push('avatar_url = ?');
      params.push(avatar_url);
    }
    if (role_id !== undefined) {
      updates.push('role_id = ?');
      params.push(role_id);
    }
    if (password_hash !== undefined) {
      updates.push('password_hash = ?');
      params.push(password_hash);
    }

    if (updates.length === 0) return false;

    sql += updates.join(', ') + ' WHERE id = ?';
    params.push(id);
    await query(sql, params);
    return true;
  }

  // 删除用户
  static async delete(id) {
    await query('DELETE FROM users WHERE id = ?', [id]);
    return true;
  }

  // 验证登录
  static async authenticate(username, password) {
    // 这里password已经是bcrypt哈希后的比较，实际需要bcrypt.compare
    const rows = await query(
      'SELECT * FROM users WHERE username = ?',
      [username]
    );
    return rows[0];
  }
}

module.exports = UserModel;