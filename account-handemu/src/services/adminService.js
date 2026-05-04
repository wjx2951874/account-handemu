// src/services/adminService.js
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const { pool } = require('../models/db');

const SALT_ROUNDS = 12;

/**
 * 管理员登录
 */
async function adminLogin({ username, password, ip }) {
  const [rows] = await pool.execute(
    'SELECT * FROM admins WHERE username = ? LIMIT 1',
    [username]
  );

  if (rows.length === 0) {
    return { success: false, message: '用户名或密码错误' };
  }

  const admin = rows[0];

  if (admin.status === 'disabled') {
    return { success: false, message: '账号已被禁用' };
  }

  const match = await bcrypt.compare(password, admin.password_hash);
  if (!match) {
    return { success: false, message: '用户名或密码错误' };
  }

  // 更新最后登录信息
  await pool.execute(
    'UPDATE admins SET last_login_at = NOW(), last_login_ip = ? WHERE id = ?',
    [ip, admin.id]
  );

  const token = jwt.sign(
    {
      adminId: admin.id,
      username: admin.username,
      role: admin.role,
      isAdmin: true
    },
    process.env.JWT_SECRET,
    { expiresIn: '12h' }
  );

  return {
    success: true,
    data: {
      token,
      admin: {
        id: admin.id,
        username: admin.username,
        role: admin.role
      }
    }
  };
}

/**
 * 修改管理员密码
 */
async function changeAdminPassword({ adminId, oldPassword, newPassword }) {
  const [rows] = await pool.execute('SELECT password_hash FROM admins WHERE id = ?', [adminId]);
  if (rows.length === 0) return { success: false, message: '管理员不存在' };

  const match = await bcrypt.compare(oldPassword, rows[0].password_hash);
  if (!match) return { success: false, message: '原密码错误' };

  const hash = await bcrypt.hash(newPassword, SALT_ROUNDS);
  await pool.execute('UPDATE admins SET password_hash = ? WHERE id = ?', [hash, adminId]);

  return { success: true, message: '密码修改成功' };
}

/**
 * 写入管理员操作日志
 */
async function writeAdminLog({ adminId, action, targetType, targetId, detail, ip }) {
  await pool.execute(
    `INSERT INTO admin_logs (admin_id, action, target_type, target_id, detail, ip)
     VALUES (?, ?, ?, ?, ?, ?)`,
    [adminId, action, targetType || null, targetId || null, detail || null, ip || null]
  );
}

module.exports = { adminLogin, changeAdminPassword, writeAdminLog };
