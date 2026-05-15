#!/usr/bin/env node
/**
 * import_recommendations.js - 将离线训练的推荐结果导入 MySQL（多算法版本）
 *
 * 读取 scripts/export/ 下的多算法 CSV 文件，通过 mysql2 批量写入
 *   - user_recommendation_caches（用户推荐缓存表）
 *   - item_similarity_caches（物品相似度缓存表）
 *
 * 相比旧版本（v2）的关键变化:
 *   1. 目标表名改为: user_recommendation_caches / item_similarity_caches
 *   2. CSV 文件按算法命名（如 svd_users_recommendations.csv）
 *   3. 支持 --algorithm 筛选特定算法
 *   4. 导入向导模式可自动发现所有算法 CSV
 *
 * 用法:
 *   node scripts/import/import_recommendations.js                        # 导入所有算法
 *   node scripts/import/import_recommendations.js --algorithm svd       # 仅导入 SVD
 *   node scripts/import/import_recommendations.js --users-only           # 仅导入用户推荐
 *   node scripts/import/import_recommendations.js --movies-only          # 仅导入电影相似度
 *   node scripts/import/import_recommendations.js --sql-dir ../export    # 自定义导出目录
 *   node scripts/import/import_recommendations.js --list                 # 列出可导入的 CSV 文件
 */

const path = require('path');
const fs = require('fs');
const readline = require('readline');
const mysql = require('mysql2/promise');
require('dotenv').config({ path: path.join(__dirname, '..', '..', 'backend', '.env') });

// ============================================================
// 配置
// ============================================================
const EXPORT_DIR = path.join(__dirname, '..', 'recommend', 'export');
const BATCH_SIZE = 500;
const DB_CONFIG = {
  host: process.env.DB_HOST || '192.168.1.38',
  user: process.env.DB_USER || 'newuser',
  password: process.env.DB_PASSWORD || 'yourpassword',
  database: process.env.DB_NAME || 'MovieRecommendSystem',
  charset: 'utf8mb4',
};

// 支持的目标表名
const TABLE_USERS = 'user_recommendation_caches';
const TABLE_MOVIES = 'item_similarity_caches';

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

/** 提取算法名和类型 */
function parseFilename(filename) {
  // 匹配: svd_users_recommendations.csv 或 item_cf_movies_similarities.csv
  const match = filename.match(/^(.+?)_(users_recommendations|movies_similarities)\.csv$/);
  if (!match) return null;
  return { algorithm: match[1], type: match[2] };
}

/** 扫描导出目录，按类型和算法列出 CSV 文件 */
function scanExports(exportDir) {
  const files = [];
  try {
    const allFiles = fs.readdirSync(exportDir);
    for (const file of allFiles) {
      const info = parseFilename(file);
      if (info) {
        files.push({
          algorithm: info.algorithm,
          type: info.type,   // 'users_recommendations' | 'movies_similarities'
          path: path.join(exportDir, file),
          filename: file,
        });
      }
    }
  } catch (e) {
    console.error(`  目录不存在或无法读取: ${exportDir}`);
  }
  return files;
}

/** 列出所有可导入的 CSV 文件 */
function listFiles(exportDir) {
  const files = scanExports(exportDir);
  if (files.length === 0) {
    console.log(`  (无匹配的 CSV 文件)`);
    return;
  }

  // 按类型分组
  const usersFiles = files.filter(f => f.type === 'users_recommendations');
  const moviesFiles = files.filter(f => f.type === 'movies_similarities');

  if (usersFiles.length > 0) {
    console.log(`  📋 用户推荐文件 (${usersFiles.length} 个):`);
    for (const f of usersFiles) {
      const size = (fs.statSync(f.path).size / (1024 * 1024)).toFixed(2);
      console.log(`     - ${f.algorithm}  → ${f.filename}  (${size} MB)`);
    }
  }

  if (moviesFiles.length > 0) {
    console.log(`  📋 电影相似度文件 (${moviesFiles.length} 个):`);
    for (const f of moviesFiles) {
      const size = (fs.statSync(f.path).size / (1024 * 1024)).toFixed(2);
      console.log(`     - ${f.algorithm}  → ${f.filename}  (${size} MB)`);
    }
  }

  console.log(`\n  目标表:`);
  console.log(`     - ${TABLE_USERS}`);
  console.log(`     - ${TABLE_MOVIES}`);
}

// ============================================================
// 导入函数
// ============================================================

/**
 * 导入 user_recommendation_caches 表
 * CSV 格式: user_id, recommend_movies(JSON), algorithm, updated_at
 * 目标表: user_recommendation_caches
 * 主键: (user_id, algorithm)
 */
async function importUsersRecommendations(conn, rows, algorithm) {
  const total = rows.length;
  console.log(`  📥 导入 ${TABLE_USERS} (算法=${algorithm}, ${total} 行)...`);

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
        const algo = row[2] || algorithm;
        const updatedAt = row[3] || new Date().toISOString().slice(0, 19).replace('T', ' ');

        // 验证 JSON 是否合法
        JSON.parse(jsonStr);

        values.push([userId, jsonStr, algo, updatedAt]);
      } catch (e) {
        errors++;
        if (errors <= 3) {
          console.error(`    ⚠️ 第 ${i + values.length + 1} 行解析失败: ${e.message}`);
        }
      }
    }

    if (values.length === 0) continue;

    try {
      const sql = `REPLACE INTO \`${TABLE_USERS}\` 
        (\`user_id\`, \`recommend_movies\`, \`algorithm\`, \`updated_at\`) VALUES ?`;
      await conn.query(sql, [values]);
      success += values.length;
    } catch (e) {
      errors += values.length;
      console.error(`    ❌ 批次 ${Math.floor(i / BATCH_SIZE) + 1} 导入失败: ${e.message}`);
    }

    // 进度
    const pct = Math.min(100, Math.round((i + batch.length) / total * 100));
    process.stdout.write(`\r   进度: ${Math.min(i + batch.length, total)}/${total} (${pct}%)`);
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  process.stdout.write('\n');
  console.log(`  ✅ 完成: 成功 ${success}/${total}, 耗时 ${elapsed}s`);
  if (errors > 0) console.log(`  ⚠️ 失败: ${errors} 行`);
  return success;
}

/**
 * 导入 item_similarity_caches 表
 * CSV 格式: movie_id, similar_movies(JSON), algorithm, updated_at
 * 目标表: item_similarity_caches
 * 主键: (movie_id, algorithm)
 */
async function importMoviesSimilarities(conn, rows, algorithm) {
  const total = rows.length;
  console.log(`  📥 导入 ${TABLE_MOVIES} (算法=${algorithm}, ${total} 行)...`);

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
        const algo = row[2] || algorithm;
        const updatedAt = row[3] || new Date().toISOString().slice(0, 19).replace('T', ' ');

        // 验证 JSON
        JSON.parse(jsonStr);

        values.push([movieId, jsonStr, algo, updatedAt]);
      } catch (e) {
        errors++;
        if (errors <= 3) {
          console.error(`    ⚠️ 第 ${i + values.length + 1} 行解析失败: ${e.message}`);
        }
      }
    }

    if (values.length === 0) continue;

    try {
      const sql = `REPLACE INTO \`${TABLE_MOVIES}\` 
        (\`movie_id\`, \`similar_movies\`, \`algorithm\`, \`updated_at\`) VALUES ?`;
      await conn.query(sql, [values]);
      success += values.length;
    } catch (e) {
      errors += values.length;
      console.error(`    ❌ 批次 ${Math.floor(i / BATCH_SIZE) + 1} 导入失败: ${e.message}`);
    }

    const pct = Math.min(100, Math.round((i + batch.length) / total * 100));
    process.stdout.write(`\r   进度: ${Math.min(i + batch.length, total)}/${total} (${pct}%)`);
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
  const listOnly = args.includes('--list');
  const algoFilter = args.includes('--algorithm')
    ? args[args.indexOf('--algorithm') + 1]
    : null;
  const sqlDir = args.includes('--sql-dir')
    ? path.resolve(args[args.indexOf('--sql-dir') + 1])
    : EXPORT_DIR;

  console.log('='.repeat(60));
  console.log('  推荐结果 MySQL 导入工具 v3 (多算法 + 新表)');
  console.log('='.repeat(60));
  console.log(`  导出目录: ${sqlDir}`);
  console.log(`  数据库: ${DB_CONFIG.host}/${DB_CONFIG.database}`);
  console.log(`  用户: ${DB_CONFIG.user}`);
  if (algoFilter) console.log(`  筛选算法: ${algoFilter}`);
  if (usersOnly) console.log(`  模式: 仅用户推荐`);
  if (moviesOnly) console.log(`  模式: 仅电影相似度`);
  console.log(`  目标表: ${TABLE_USERS} / ${TABLE_MOVIES}`);
  console.log('='.repeat(60));

  // 扫描 CSV 文件
  const files = scanExports(sqlDir);

  if (files.length === 0) {
    console.error('\n❌ 未找到可导入的 CSV 文件');
    console.error(`   期望路径: ${sqlDir}`);
    console.error(`   文件命名示例:`);
    console.error(`     svd_users_recommendations.csv`);
    console.error(`     hybrid_users_recommendations.csv`);
    console.error(`     item_cf_movies_similarities.csv`);
    console.error(`   tip: 请先运行 scripts/recommend/train_recommend.py`);
    process.exit(1);
  }

  if (listOnly) {
    listFiles(sqlDir);
    return;
  }

  // 筛选
  const filtered = files.filter(f => {
    if (algoFilter && f.algorithm !== algoFilter) return false;
    if (usersOnly && f.type !== 'users_recommendations') return false;
    if (moviesOnly && f.type !== 'movies_similarities') return false;
    return true;
  });

  if (filtered.length === 0) {
    console.error(`\n❌ 没有匹配的 CSV 文件`);
    if (algoFilter) console.error(`   算法筛选: ${algoFilter}`);
    process.exit(1);
  }

  console.log(`\n  待导入: ${filtered.length} 个文件`);
  for (const f of filtered) {
    const size = (fs.statSync(f.path).size / (1024 * 1024)).toFixed(2);
    const table = f.type === 'users_recommendations' ? TABLE_USERS : TABLE_MOVIES;
    console.log(`     ${f.algorithm} → ${table}  (${size} MB)`);
  }
  console.log();

  // 连接数据库
  let conn;
  try {
    conn = await mysql.createConnection(DB_CONFIG);
    console.log('✅ 数据库连接成功\n');
  } catch (e) {
    console.error(`\n❌ 数据库连接失败: ${e.message}`);
    console.error('   请检查 backend/.env 中的数据库配置');
    process.exit(1);
  }

  let globalSuccess = true;

  try {
    for (const file of filtered) {
      console.log(`\n  ── 处理文件: ${file.filename} ──`);
      const rows = await readCSV(file.path);
      if (rows.length === 0) {
        console.log(`  ⚠️ 文件为空，跳过`);
        continue;
      }

      let ok;
      if (file.type === 'users_recommendations') {
        ok = await importUsersRecommendations(conn, rows, file.algorithm);
      } else {
        ok = await importMoviesSimilarities(conn, rows, file.algorithm);
      }
      if (!ok) globalSuccess = false;
    }
  } catch (e) {
    console.error(`\n❌ 导入异常: ${e.message}`);
    globalSuccess = false;
  } finally {
    await conn.end();
  }

  // 结果汇总
  console.log('\n' + '='.repeat(60));
  if (globalSuccess) {
    console.log('  ✅ 所有数据导入完成！');
  } else {
    console.log('  ⚠️ 部分导入失败，请检查上述错误信息');
  }
  console.log('='.repeat(60));
  console.log(`
  验证方法:
    SELECT algorithm, COUNT(*) AS 用户推荐数 FROM ${TABLE_USERS} GROUP BY algorithm;
    SELECT algorithm, COUNT(*) AS 电影相似度数 FROM ${TABLE_MOVIES} GROUP BY algorithm;

    -- 查看某算法的推荐内容
    SELECT user_id, algorithm, recommend_movies FROM ${TABLE_USERS}
    WHERE algorithm = 'hybrid' LIMIT 3;

    -- 查看某算法的相似内容
    SELECT movie_id, algorithm, similar_movies FROM ${TABLE_MOVIES}
    WHERE algorithm = 'item_cf' LIMIT 3;
  `);
}

main().catch(e => {
  console.error(`\n❌ 脚本异常退出: ${e.message}`);
  process.exit(1);
});