#!/usr/bin/env node
/**
 * import_recommendations.js - 将离线训练的推荐结果导入 MySQL
 *
 * 读取 scripts/export/ 下的 CSV 文件，通过后端 mysql2 连接池
 * 批量写入 users_recommendations 和 movies_similarities 表。
 *
 * 用法:
 *   node scripts/import_recommendations.js
 *   node scripts/import_recommendations.js --users-only
 *   node scripts/import_recommendations.js --movies-only
 *   node scripts/import_recommendations.js --sql-dir ../export
 */

const path = require('path');
const fs = require('fs');
const readline = require('readline');
const mysql = require('mysql2/promise');
require('dotenv').config({ path: path.join(__dirname, '..', '..', 'backend', '.env') });

// ============================================================
// 配置
// ============================================================
// 训练脚本 (scripts/recommend/train_recommend.py) 输出 CSV 到 scripts/recommend/export/
const EXPORT_DIR = path.join(__dirname, '..', 'recommend', 'export');
const BATCH_SIZE = 500;          // 每批写入行数
const DB_CONFIG = {
  host: process.env.DB_HOST || '192.168.1.38',
  user: process.env.DB_USER || 'newuser',
  password: process.env.DB_PASSWORD || 'yourpassword',
  database: process.env.DB_NAME || 'MovieRecommendSystem',
  charset: 'utf8mb4',
};

// ============================================================
// 辅助函数
// ============================================================

/** 解析 CSV 行（支持引号内的逗号和换行） */
function parseCSVLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === ',' && !inQuotes) {
      result.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  result.push(current);
  return result;
}

/** 读取 CSV 文件，返回行数组 */
async function readCSV(filePath) {
  const rows = [];
  const stream = fs.createReadStream(filePath, { encoding: 'utf-8' });
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });

  for await (const line of rl) {
    if (line.trim()) {
      rows.push(parseCSVLine(line));
    }
  }
  return rows;
}

/** 将包含双引号的 JSON 字符串还原为标准 JSON */
function cleanJSON(str) {
  return str.replace(/""/g, '"');
}

// ============================================================
// 导入函数
// ============================================================

/**
 * 导入 users_recommendations 表
 * CSV 格式: user_id, recommend_movies(JSON), algorithm, updated_at
 */
async function importUsersRecommendations(conn, rows) {
  const total = rows.length;
  console.log(`\n📥 导入 users_recommendations (${total} 行)...`);

  let success = 0;
  let errors = 0;
  const startTime = Date.now();

  for (let i = 0; i < total; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE);
    const values = [];

    for (const row of batch) {
      try {
        const userId = parseInt(row[0], 10);
        const jsonStr = cleanJSON(row[1]);
        const algorithm = row[2] || 'svd';
        const updatedAt = row[3] || new Date().toISOString().slice(0, 19).replace('T', ' ');

        // 验证 JSON 是否合法
        JSON.parse(jsonStr);

        values.push([userId, jsonStr, algorithm, updatedAt]);
      } catch (e) {
        errors++;
        if (errors <= 3) {
          console.error(`  ⚠️ 第 ${i + values.length + 1} 行解析失败: ${e.message}`);
        }
      }
    }

    if (values.length === 0) continue;

    try {
      const sql = `REPLACE INTO \`users_recommendations\` 
        (\`user_id\`, \`recommend_movies\`, \`algorithm\`, \`updated_at\`) VALUES ?`;
      await conn.query(sql, [values]);
      success += values.length;
    } catch (e) {
      errors += values.length;
      console.error(`  ❌ 批次 ${Math.floor(i / BATCH_SIZE) + 1} 导入失败: ${e.message}`);
    }

    // 进度
    const pct = Math.min(100, Math.round((i + batch.length) / total * 100));
    process.stdout.write(`\r  进度: ${Math.min(i + batch.length, total)}/${total} (${pct}%)`);
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  process.stdout.write('\n');
  console.log(`  ✅ 完成: 成功 ${success}/${total}, 耗时 ${elapsed}s`);
  if (errors > 0) console.log(`  ⚠️ 失败: ${errors} 行`);
  return success;
}

/**
 * 导入 movies_similarities 表
 * CSV 格式: movie_id, similar_movies(JSON), updated_at
 */
async function importMoviesSimilarities(conn, rows) {
  const total = rows.length;
  console.log(`\n📥 导入 movies_similarities (${total} 行)...`);

  let success = 0;
  let errors = 0;
  const startTime = Date.now();

  for (let i = 0; i < total; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE);
    const values = [];

    for (const row of batch) {
      try {
        const movieId = parseInt(row[0], 10);
        const jsonStr = cleanJSON(row[1]);
        const updatedAt = row[2] || new Date().toISOString().slice(0, 19).replace('T', ' ');

        // 验证 JSON
        JSON.parse(jsonStr);

        values.push([movieId, jsonStr, updatedAt]);
      } catch (e) {
        errors++;
        if (errors <= 3) {
          console.error(`  ⚠️ 第 ${i + values.length + 1} 行解析失败: ${e.message}`);
        }
      }
    }

    if (values.length === 0) continue;

    try {
      const sql = `REPLACE INTO \`movies_similarities\` 
        (\`movie_id\`, \`similar_movies\`, \`updated_at\`) VALUES ?`;
      await conn.query(sql, [values]);
      success += values.length;
    } catch (e) {
      errors += values.length;
      console.error(`  ❌ 批次 ${Math.floor(i / BATCH_SIZE) + 1} 导入失败: ${e.message}`);
    }

    const pct = Math.min(100, Math.round((i + batch.length) / total * 100));
    process.stdout.write(`\r  进度: ${Math.min(i + batch.length, total)}/${total} (${pct}%)`);
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  process.stdout.write('\n');
  console.log(`  ✅ 完成: 成功 ${success}/${total}, 耗时 ${elapsed}s`);
  if (errors > 0) console.log(`  ⚠️ 失败: ${errors} 行`);
  return success;
}

// ============================================================
// 主函数
// ============================================================

async function main() {
  const args = process.argv.slice(2);
  const usersOnly = args.includes('--users-only');
  const moviesOnly = args.includes('--movies-only');
  const sqlDir = args.includes('--sql-dir')
    ? path.resolve(args[args.indexOf('--sql-dir') + 1])
    : EXPORT_DIR;

  console.log('='.repeat(60));
  console.log('  推荐结果 MySQL 导入工具 (Node.js)');
  console.log('='.repeat(60));
  console.log(`  导出目录: ${sqlDir}`);
  console.log(`  数据库: ${DB_CONFIG.host}/${DB_CONFIG.database}`);
  console.log(`  用户: ${DB_CONFIG.user}`);
  console.log('='.repeat(60));

  // 查找 CSV 文件
  const usersCsv = path.join(sqlDir, 'users_recommendations.csv');
  const moviesCsv = path.join(sqlDir, 'movies_similarities.csv');

  if (!fs.existsSync(usersCsv) && !fs.existsSync(moviesCsv)) {
    console.error('\n❌ 未找到 CSV 文件，请先运行 train_recommend.py');
    console.error(`   期望路径:\n     ${usersCsv}\n     ${moviesCsv}`);
    process.exit(1);
  }

  // 连接数据库
  let conn;
  try {
    conn = await mysql.createConnection(DB_CONFIG);
    console.log('\n✅ 数据库连接成功\n');
  } catch (e) {
    console.error(`\n❌ 数据库连接失败: ${e.message}`);
    console.error('   请检查 backend/.env 中的数据库配置');
    process.exit(1);
  }

  let allOk = true;

  try {
    // 导入用户推荐
    if (!moviesOnly && fs.existsSync(usersCsv)) {
      const rows = await readCSV(usersCsv);
      const ok = await importUsersRecommendations(conn, rows);
      if (!ok) allOk = false;
    }

    // 导入电影相似度
    if (!usersOnly && fs.existsSync(moviesCsv)) {
      const rows = await readCSV(moviesCsv);
      const ok = await importMoviesSimilarities(conn, rows);
      if (!ok) allOk = false;
    }
  } catch (e) {
    console.error(`\n❌ 导入异常: ${e.message}`);
    allOk = false;
  } finally {
    await conn.end();
  }

  // 结果汇总
  console.log('\n' + '='.repeat(60));
  if (allOk) {
    console.log('  ✅ 所有数据导入完成！');
  } else {
    console.log('  ⚠️ 部分导入失败，请检查上述错误信息');
  }
  console.log('='.repeat(60));
  console.log(`
  验证方法:
    SELECT COUNT(*) AS 用户推荐数 FROM users_recommendations;
    SELECT COUNT(*) AS 电影相似度数 FROM movies_similarities;
    SELECT * FROM users_recommendations LIMIT 3;
    SELECT * FROM movies_similarities LIMIT 3;
  `);
}

main().catch(e => {
  console.error(`\n❌ 脚本异常退出: ${e.message}`);
  process.exit(1);
});