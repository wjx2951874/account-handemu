// src/services/securityService.js
const { pool, redis } = require('../models/db');

/**
 * 写入登录日志
 */
async function writeLoginLog({ userId, ip, userAgent, status }) {
  await pool.execute(
    `INSERT INTO login_logs (user_id, ip, user_agent, status) VALUES (?, ?, ?, ?)`,
    [userId || null, ip, userAgent || null, status]
  );
}

/**
 * 写入安全日志
 */
async function writeSecurityLog({ userId, type, description, ip }) {
  await pool.execute(
    `INSERT INTO security_logs (user_id, type, description, ip) VALUES (?, ?, ?, ?)`,
    [userId || null, type, description || null, ip || null]
  );
}

/**
 * 写入 IP 封禁记录（持久化到 MySQL）
 */
async function writeIPBan({ ip, durationSeconds, reason }) {
  const expiresAt = new Date(Date.now() + durationSeconds * 1000);
  await pool.execute(
    `INSERT INTO ip_bans (ip, expires_at, reason) VALUES (?, ?, ?)`,
    [ip, expiresAt, reason || null]
  );
}

/**
 * 异地登录检测
 * 比较当前 IP 和 User-Agent 与用户上次登录记录
 * 任一不同则视为异常
 */
async function detectAnomalousLogin({ userId, currentIP, currentUA }) {
  const [rows] = await pool.execute(
    `SELECT ip, user_agent FROM login_logs
     WHERE user_id = ? AND status = 'success'
     ORDER BY created_at DESC LIMIT 1`,
    [userId]
  );

  if (rows.length === 0) {
    // 首次登录，不算异常
    return false;
  }

  const last = rows[0];
  const ipChanged = last.ip !== currentIP;
  const uaChanged = last.user_agent !== currentUA;

  if (ipChanged || uaChanged) {
    const parts = [];
    if (ipChanged) parts.push(`IP 变更: ${last.ip} → ${currentIP}`);
    if (uaChanged) parts.push(`设备变更`);

    await writeSecurityLog({
      userId,
      type: 'anomalous_login',
      description: parts.join('；'),
      ip: currentIP
    });
    return true;
  }

  return false;
}

/**
 * 更新用户最后登录信息
 */
async function updateLastLogin({ userId, ip, userAgent }) {
  await pool.execute(
    `UPDATE users SET last_login_ip = ?, last_login_device = ? WHERE id = ?`,
    [ip, userAgent, userId]
  );
}

module.exports = {
  writeLoginLog,
  writeSecurityLog,
  writeIPBan,
  detectAnomalousLogin,
  updateLastLogin
};
