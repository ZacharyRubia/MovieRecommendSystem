const mysql = require('mysql2/promise'); // 使用 Promise 版本

// 创建连接池
const pool = mysql.createPool({
  host: process.env.DB_HOST || '192.168.1.38',
  user: process.env.DB_USER || 'newuser',
  password: process.env.DB_PASSWORD || 'yourpassword',
  database: process.env.DB_NAME || 'MovieRecommendSystem',
  waitForConnections: true,
  connectionLimit: 10,      // 连接池最大连接数
  queueLimit: 0,
  charset: 'utf8mb4'
});

// 导出查询辅助函数
// 使用 query() 而不是 execute() 避免 LIMIT/OFFSET 参数类型问题
async function query(sql, params) {
  const [rows] = await pool.query(sql, params);
  return rows;
}

module.exports = { pool, query };