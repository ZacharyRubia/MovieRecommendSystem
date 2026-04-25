const mysql = require('mysql2/promise'); // 使用 Promise 版本

// 创建连接池
const pool = mysql.createPool({
  host: process.env.DB_HOST || '192.168.10.128',
  user: process.env.DB_USER || 'newuser',
  password: process.env.DB_PASSWORD || 'yourpassword',
  database: process.env.DB_NAME || 'MovieRecommendSystem',
  waitForConnections: true,
  connectionLimit: 10,      // 连接池最大连接数
  queueLimit: 0,
  charset: 'utf8mb4'
});

// 导出查询辅助函数（可选）
async function query(sql, params) {
  const [rows] = await pool.execute(sql, params);
  return rows;
}

module.exports = { pool, query };