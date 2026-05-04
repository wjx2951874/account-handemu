// src/models/db.js
const mysql = require('mysql2/promise');
const Redis = require('ioredis');

// ---- MySQL 连接池 ----
const pool = mysql.createPool({
  host: process.env.DB_HOST,
  port: parseInt(process.env.DB_PORT, 10) || 3306,
  user: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
  database: process.env.DB_NAME,
  waitForConnections: true,
  connectionLimit: 20,
  queueLimit: 0,
  charset: 'utf8mb4'
});

// ---- Redis 客户端 ----
const redis = new Redis({
  host: process.env.REDIS_HOST || '127.0.0.1',
  port: parseInt(process.env.REDIS_PORT, 10) || 6379,
  password: process.env.REDIS_PASSWORD || undefined,
  db: 0,
  retryStrategy(times) {
    const delay = Math.min(times * 200, 5000);
    return delay;
  }
});

redis.on('connect', () => {
  console.log('[Redis] 连接成功');
});

redis.on('error', (err) => {
  console.error('[Redis] 连接错误:', err.message);
});

module.exports = { pool, redis };
