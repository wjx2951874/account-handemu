// src/services/userManageService.js
const bcrypt = require('bcryptjs');
const { pool } = require('../models/db');

const SALT_ROUNDS = 12;

/**
 * 用户列表（分页 + 搜索 + 筛选）
 */
async function getUserList({ page, pageSize, keyword, status, sortBy, sortOrder }) {
  page = Math.max(1, parseInt(page, 10) || 1);
  pageSize = Math.min(100, Math.max(1, parseInt(pageSize, 10) || 20));
  const offset = (page - 1) * pageSize;

  // 允许的排序字段
  const allowedSort = ['id', 'username', 'email', 'status', 'created_at', 'last_login_ip'];
  if (!allowedSort.includes(sortBy)) sortBy = 'id';
  sortOrder = sortOrder === 'asc' ? 'ASC' : 'DESC';

  let where = '1=1';
  const params = [];

  if (keyword) {
    where += ' AND (username LIKE ? OR email LIKE ? OR phone LIKE ?)';
    const kw = `%${keyword}%`;
    params.push(kw, kw, kw);
  }

  if (status && ['active', 'inactive'].includes(status)) {
    where += ' AND status = ?';
    params.push(status);
  }

  // 查总数
  const [countRows] = await pool.execute(
    `SELECT COUNT(*) AS total FROM users WHERE ${where}`,
    params
  );
  const total = countRows[0].total;

  // 查列表
  const [rows] = await pool.execute(
    `SELECT id, username, email, phone, status, last_login_ip, last_login_device, created_at, updated_at
     FROM users WHERE ${where}
     ORDER BY ${sortBy} ${sortOrder}
     LIMIT ${pageSize} OFFSET ${offset}`,
    params
  );

  return {
    list: rows,
    pagination: {
      page,
      pageSize,
      total,
      totalPages: Math.ceil(total / pageSize)
    }
  };
}

/**
 * 获取单个用户详情
 */
async function getUserDetail(userId) {
  const [rows] = await pool.execute(
    `SELECT id, username, email, phone, status, last_login_ip, last_login_device, created_at, updated_at
     FROM users WHERE id = ?`,
    [userId]
  );
  if (rows.length === 0) return null;

  // 最近登录日志
  const [logs] = await pool.execute(
    `SELECT ip, user_agent, status, created_at FROM login_logs
     WHERE user_id = ? ORDER BY created_at DESC LIMIT 20`,
    [userId]
  );

  // 安全日志
  const [secLogs] = await pool.execute(
    `SELECT type, description, ip, created_at FROM security_logs
     WHERE user_id = ? ORDER BY created_at DESC LIMIT 20`,
    [userId]
  );

  return {
    user: rows[0],
    loginLogs: logs,
    securityLogs: secLogs
  };
}

/**
 * 修改用户状态（激活 / 禁用）
 */
async function updateUserStatus(userId, newStatus) {
  if (!['active', 'inactive'].includes(newStatus)) {
    return { success: false, message: '无效的状态值' };
  }
  const [result] = await pool.execute(
    'UPDATE users SET status = ? WHERE id = ?',
    [newStatus, userId]
  );
  if (result.affectedRows === 0) {
    return { success: false, message: '用户不存在' };
  }
  return { success: true, message: `用户状态已更新为 ${newStatus}` };
}

/**
 * 修改用户名
 */
async function updateUsername(userId, newUsername) {
  // 检查唯一性
  const [exist] = await pool.execute(
    'SELECT id FROM users WHERE username = ? AND id != ?',
    [newUsername, userId]
  );
  if (exist.length > 0) {
    return { success: false, message: '用户名已被占用' };
  }
  const [result] = await pool.execute(
    'UPDATE users SET username = ? WHERE id = ?',
    [newUsername, userId]
  );
  if (result.affectedRows === 0) {
    return { success: false, message: '用户不存在' };
  }
  return { success: true, message: '用户名修改成功' };
}

/**
 * 修改用户邮箱
 */
async function updateUserEmail(userId, newEmail) {
  const [exist] = await pool.execute(
    'SELECT id FROM users WHERE email = ? AND id != ?',
    [newEmail, userId]
  );
  if (exist.length > 0) {
    return { success: false, message: '邮箱已被占用' };
  }
  const [result] = await pool.execute(
    'UPDATE users SET email = ? WHERE id = ?',
    [newEmail, userId]
  );
  if (result.affectedRows === 0) {
    return { success: false, message: '用户不存在' };
  }
  return { success: true, message: '邮箱修改成功' };
}

/**
 * 重置用户密码
 */
async function resetUserPassword(userId, newPassword) {
  const hash = await bcrypt.hash(newPassword, SALT_ROUNDS);
  const [result] = await pool.execute(
    'UPDATE users SET password_hash = ? WHERE id = ?',
    [hash, userId]
  );
  if (result.affectedRows === 0) {
    return { success: false, message: '用户不存在' };
  }
  return { success: true, message: '密码已重置' };
}

/**
 * 删除用户
 */
async function deleteUser(userId) {
  const [result] = await pool.execute('DELETE FROM users WHERE id = ?', [userId]);
  if (result.affectedRows === 0) {
    return { success: false, message: '用户不存在' };
  }
  return { success: true, message: '用户已删除' };
}

/**
 * 批量操作
 */
async function batchUpdateStatus(userIds, newStatus) {
  if (!Array.isArray(userIds) || userIds.length === 0) {
    return { success: false, message: '请选择用户' };
  }
  if (!['active', 'inactive'].includes(newStatus)) {
    return { success: false, message: '无效的状态值' };
  }
  const placeholders = userIds.map(() => '?').join(',');
  const [result] = await pool.execute(
    `UPDATE users SET status = ? WHERE id IN (${placeholders})`,
    [newStatus, ...userIds]
  );
  return { success: true, message: `已更新 ${result.affectedRows} 个用户` };
}

async function batchDeleteUsers(userIds) {
  if (!Array.isArray(userIds) || userIds.length === 0) {
    return { success: false, message: '请选择用户' };
  }
  const placeholders = userIds.map(() => '?').join(',');
  const [result] = await pool.execute(
    `DELETE FROM users WHERE id IN (${placeholders})`,
    userIds
  );
  return { success: true, message: `已删除 ${result.affectedRows} 个用户` };
}

/**
 * 统计概览
 */
async function getDashboardStats() {
  const [totalRows] = await pool.execute('SELECT COUNT(*) AS c FROM users');
  const [activeRows] = await pool.execute("SELECT COUNT(*) AS c FROM users WHERE status='active'");
  const [inactiveRows] = await pool.execute("SELECT COUNT(*) AS c FROM users WHERE status='inactive'");
  const [todayRows] = await pool.execute(
    "SELECT COUNT(*) AS c FROM users WHERE DATE(created_at) = CURDATE()"
  );
  const [weekRows] = await pool.execute(
    "SELECT COUNT(*) AS c FROM users WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)"
  );
  const [todayLogins] = await pool.execute(
    "SELECT COUNT(*) AS c FROM login_logs WHERE status='success' AND DATE(created_at) = CURDATE()"
  );
  const [todayFails] = await pool.execute(
    "SELECT COUNT(*) AS c FROM login_logs WHERE status='fail' AND DATE(created_at) = CURDATE()"
  );
  const [securityCount] = await pool.execute(
    "SELECT COUNT(*) AS c FROM security_logs WHERE DATE(created_at) = CURDATE()"
  );
  const [banCount] = await pool.execute(
    "SELECT COUNT(*) AS c FROM ip_bans WHERE expires_at > NOW()"
  );

  return {
    totalUsers: totalRows[0].c,
    activeUsers: activeRows[0].c,
    inactiveUsers: inactiveRows[0].c,
    todayRegistered: todayRows[0].c,
    weekRegistered: weekRows[0].c,
    todayLogins: todayLogins[0].c,
    todayFailedLogins: todayFails[0].c,
    todaySecurityEvents: securityCount[0].c,
    activeBans: banCount[0].c
  };
}

/**
 * 获取登录日志列表
 */
async function getLoginLogs({ page, pageSize, userId, status, ip }) {
  page = Math.max(1, parseInt(page, 10) || 1);
  pageSize = Math.min(100, Math.max(1, parseInt(pageSize, 10) || 20));
  const offset = (page - 1) * pageSize;

  let where = '1=1';
  const params = [];

  if (userId) { where += ' AND ll.user_id = ?'; params.push(userId); }
  if (status) { where += ' AND ll.status = ?'; params.push(status); }
  if (ip) { where += ' AND ll.ip LIKE ?'; params.push(`%${ip}%`); }

  const [countRows] = await pool.execute(
    `SELECT COUNT(*) AS total FROM login_logs ll WHERE ${where}`, params
  );

  const [rows] = await pool.execute(
    `SELECT ll.*, u.username FROM login_logs ll
     LEFT JOIN users u ON u.id = ll.user_id
     WHERE ${where} ORDER BY ll.created_at DESC
     LIMIT ${pageSize} OFFSET ${offset}`, params
  );

  return {
    list: rows,
    pagination: { page, pageSize, total: countRows[0].total, totalPages: Math.ceil(countRows[0].total / pageSize) }
  };
}

/**
 * 获取安全日志列表
 */
async function getSecurityLogs({ page, pageSize, type, userId }) {
  page = Math.max(1, parseInt(page, 10) || 1);
  pageSize = Math.min(100, Math.max(1, parseInt(pageSize, 10) || 20));
  const offset = (page - 1) * pageSize;

  let where = '1=1';
  const params = [];

  if (type) { where += ' AND sl.type = ?'; params.push(type); }
  if (userId) { where += ' AND sl.user_id = ?'; params.push(userId); }

  const [countRows] = await pool.execute(
    `SELECT COUNT(*) AS total FROM security_logs sl WHERE ${where}`, params
  );

  const [rows] = await pool.execute(
    `SELECT sl.*, u.username FROM security_logs sl
     LEFT JOIN users u ON u.id = sl.user_id
     WHERE ${where} ORDER BY sl.created_at DESC
     LIMIT ${pageSize} OFFSET ${offset}`, params
  );

  return {
    list: rows,
    pagination: { page, pageSize, total: countRows[0].total, totalPages: Math.ceil(countRows[0].total / pageSize) }
  };
}

/**
 * IP 封禁管理
 */
async function getIPBans({ page, pageSize }) {
  page = Math.max(1, parseInt(page, 10) || 1);
  pageSize = Math.min(100, Math.max(1, parseInt(pageSize, 10) || 20));
  const offset = (page - 1) * pageSize;

  const [countRows] = await pool.execute('SELECT COUNT(*) AS total FROM ip_bans');
  const [rows] = await pool.execute(
    `SELECT * FROM ip_bans ORDER BY created_at DESC LIMIT ${pageSize} OFFSET ${offset}`
  );

  return {
    list: rows,
    pagination: { page, pageSize, total: countRows[0].total, totalPages: Math.ceil(countRows[0].total / pageSize) }
  };
}

async function removeIPBan(banId) {
  const { redis } = require('../models/db');
  const [rows] = await pool.execute('SELECT ip FROM ip_bans WHERE id = ?', [banId]);
  if (rows.length > 0) {
    await redis.del(`ratelimit:ip_ban:${rows[0].ip}`);
  }
  await pool.execute('DELETE FROM ip_bans WHERE id = ?', [banId]);
  return { success: true, message: 'IP 封禁已解除' };
}

module.exports = {
  getUserList,
  getUserDetail,
  updateUserStatus,
  updateUsername,
  updateUserEmail,
  resetUserPassword,
  deleteUser,
  batchUpdateStatus,
  batchDeleteUsers,
  getDashboardStats,
  getLoginLogs,
  getSecurityLogs,
  getIPBans,
  removeIPBan
};
