// src/services/authService.js
const bcrypt = require('bcryptjs');
const { pool } = require('../models/db');
const { signJWT } = require('../utils/token');
const { createVerificationToken } = require('./emailService');
const {
  writeLoginLog,
  writeSecurityLog,
  writeIPBan,
  detectAnomalousLogin,
  updateLastLogin
} = require('./securityService');
const {
  isIPBanned,
  recordIPFail,
  isAccountLocked,
  recordAccountFail,
  clearAccountFail,
  clearIPFail,
  IP_BAN_DURATION
} = require('./rateLimitService');

const SALT_ROUNDS = 12;

/**
 * 注册
 */
async function register({ username, email, password }) {
  // 检查用户名唯一
  const [existUser] = await pool.execute(
    'SELECT id FROM users WHERE username = ?', [username]
  );
  if (existUser.length > 0) {
    return { success: false, message: '用户名已被注册' };
  }

  // 检查邮箱唯一
  const [existEmail] = await pool.execute(
    'SELECT id FROM users WHERE email = ?', [email]
  );
  if (existEmail.length > 0) {
    return { success: false, message: '邮箱已被注册' };
  }

  // 哈希密码
  const passwordHash = await bcrypt.hash(password, SALT_ROUNDS);

  // 插入用户
  const [result] = await pool.execute(
    `INSERT INTO users (username, email, password_hash, status) VALUES (?, ?, ?, 'inactive')`,
    [username, email, passwordHash]
  );

  const userId = result.insertId;

  // 生成邮箱验证 token
  const { verifyLink } = await createVerificationToken(userId);

  return {
    success: true,
    message: '注册成功，请查收验证邮件激活账号',
    data: { userId, verifyLink }
  };
}

/**
 * 登录
 */
async function login(account, password, ip, userAgent) {
  const userService = require('./userService');

  if (!account || !password) {
    return { success: false, message: '请输入账号和密码' };
  }

  // 支持用户名、邮箱、手机号登录
  let sql, params;
  if (account.includes('@')) {
    sql = 'SELECT id, username, email, phone, nickname, password_hash, status FROM users WHERE email = ?';
    params = [account];
  } else if (/^1\d{10}$/.test(account)) {
    sql = 'SELECT id, username, email, phone, nickname, password_hash, status FROM users WHERE phone = ?';
    params = [account];
  } else {
    sql = 'SELECT id, username, email, phone, nickname, password_hash, status FROM users WHERE username = ?';
    params = [account];
  }

  const [users] = await pool.execute(sql, params);

  if (users.length === 0) {
    // 记录失败日志（用户不存在）
    await userService.recordLoginLog(0, ip, userAgent, 'failed', '用户不存在').catch(() => {});
    return { success: false, message: '账号或密码错误' };
  }

  const user = users[0];

  if (user.status === 'deleted') {
    return { success: false, message: '该账号已注销' };
  }

  if (user.status === 'disabled') {
    await userService.recordLoginLog(user.id, ip, userAgent, 'failed', '账号已禁用').catch(() => {});
    return { success: false, message: '账号已被禁用，请联系管理员' };
  }

  const valid = await bcrypt.compare(password, user.password_hash);
  if (!valid) {
    await userService.recordLoginLog(user.id, ip, userAgent, 'failed', '密码错误').catch(() => {});
    return { success: false, message: '账号或密码错误' };
  }

  // 登录成功，记录日志
  await userService.recordLoginLog(user.id, ip, userAgent, 'success', '').catch(() => {});

  // 生成 session token 并存入 user_sessions
  const crypto = require('crypto');
  const sessionToken = crypto.randomBytes(32).toString('hex');
  const ua = userService.parseUA(userAgent);
  const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000); // 7天

  await pool.execute(
    `INSERT INTO user_sessions (user_id, session_token, ip, user_agent, device, browser, os, expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [user.id, sessionToken, ip || '', userAgent || '', ua.device, ua.browser, ua.os, expiresAt]
  ).catch(() => {});

  // 生成 JWT
  const jwt = require('jsonwebtoken');
  const secret = process.env.JWT_SECRET || 'handemu_jwt_secret_2024';
  const token = jwt.sign(
    {
      userId: user.id,
      username: user.username,
      sessionId: sessionToken
    },
    secret,
    { expiresIn: '7d' }
  );

  return {
    success: true,
    message: '登录成功',
    token: token,
    data: {
      id: user.id,
      username: user.username,
      nickname: user.nickname || user.username
    }
  };
}

/**
 * 处理登录失败的统一逻辑
 */
async function handleLoginFail({ identifier, ip, userAgent, userId }) {
  // 记录登录日志
  await writeLoginLog({ userId, ip, userAgent, status: 'fail' });

  // IP 失败计数
  const ipResult = await recordIPFail(ip);
  if (ipResult.banned) {
    await writeIPBan({ ip, durationSeconds: IP_BAN_DURATION, reason: '登录失败次数过多' });
    await writeSecurityLog({
      userId,
      type: 'ip_banned',
      description: `IP ${ip} 因 5 分钟内失败 ${ipResult.count} 次被封禁 10 分钟`,
      ip
    });
  }

  // 账号失败计数
  const acctCount = await recordAccountFail(identifier);
  if (acctCount >= 5) {
    await writeSecurityLog({
      userId,
      type: 'account_locked',
      description: `账号 ${identifier} 连续失败 ${acctCount} 次，已锁定`,
      ip
    });
  }
}

/**
 * 发送密码重置邮件
 */
async function forgotPassword(method, account) {
  let sql = '';
  if (method === 'email') {
    sql = 'SELECT id, username, email, phone FROM users WHERE email = ? LIMIT 1';
  } else if (method === 'username') {
    sql = 'SELECT id, username, email, phone FROM users WHERE username = ? LIMIT 1';
  } else if (method === 'phone') {
    sql = 'SELECT id, username, email, phone FROM users WHERE phone = ? LIMIT 1';
  } else {
    return { success: false, message: '不支持的找回方式' };
  }

  const [rows] = await pool.execute(sql, [account]);

  if (rows.length === 0) {
    // 返回统一格式，不暴露用户是否存在
    return {
      success: true,
      found: false,
      message: '如果存在对应的账户，重置链接将发送到绑定的邮箱。如果没有收到，说明该账户不存在或未绑定邮箱。如果确定信息正确，请联系我们进一步处理。',
      account: account,
      method: method
    };
  }

  const user = rows[0];

  if (!user.email) {
    return {
      success: true,
      found: false,
      message: '该账户未绑定邮箱，无法通过邮件重置密码。请联系我们进一步处理。',
      account: account,
      method: method
    };
  }

  // 限流：同一用户 5 分钟内只能请求一次
  const [recent] = await pool.execute(
    "SELECT id FROM email_verification_tokens WHERE user_id = ? AND token LIKE 'reset\\_%' AND created_at > DATE_SUB(NOW(), INTERVAL 5 MINUTE) LIMIT 1",
    [user.id]
  );
  if (recent.length > 0) {
    // 脱敏邮箱
    const parts = user.email.split('@');
    const masked = parts[0].substring(0, 2) + '***@' + parts[1];
    return {
      success: false,
      message: '操作过于频繁，请 5 分钟后再试。重置链接已发送到 ' + masked,
      account: account,
      method: method
    };
  }

  // 同一 IP 1小时内最多请求 5 次（通过 controller 传入 ip）
  // 这里先清理旧 token
  await pool.execute(
    "DELETE FROM email_verification_tokens WHERE user_id = ? AND token LIKE 'reset\\_%'",
    [user.id]
  );

  const crypto = require('crypto');
  const resetToken = crypto.randomBytes(32).toString('hex');
  const expiresAt = new Date(Date.now() + 30 * 60 * 1000);

  await pool.execute(
    'INSERT INTO email_verification_tokens (user_id, token, expires_at) VALUES (?, ?, ?)',
    [user.id, 'reset_' + resetToken, expiresAt]
  );

  // 脱敏邮箱
  const emailParts = user.email.split('@');
  const maskedEmail = emailParts[0].substring(0, 2) + '***@' + emailParts[1];

  // 发送重置邮件
  try {
    const emailService = require('./emailService');
    const baseUrl = process.env.BASE_URL || 'https://account.handemu.com';
    const resetLink = baseUrl + '/reset-password.html?token=' + resetToken;

    await emailService.sendEmail({
      to: user.email,
      subject: 'Demu账号 密码重置',
      html: '<div style="max-width:480px;margin:0 auto;font-family:sans-serif;color:#1e293b;">' +
        '<h2 style="color:#2563eb;">Demu账号 密码重置</h2>' +
        '<p>你好 <strong>' + user.username + '</strong>，</p>' +
        '<p>我们收到了你的密码重置请求。点击下方按钮设置新密码：</p>' +
        '<div style="text-align:center;margin:28px 0;">' +
          '<a href="' + resetLink + '" style="display:inline-block;padding:12px 32px;background:#2563eb;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">重置密码</a>' +
        '</div>' +
        '<p style="font-size:13px;color:#64748b;">此链接 30 分钟内有效。如果不是你本人操作，请忽略此邮件。</p>' +
        '<p style="font-size:13px;color:#64748b;">链接地址：' + resetLink + '</p>' +
        '<hr style="margin:24px 0;border:none;border-top:1px solid #e2e8f0;" />' +
        '<p style="font-size:12px;color:#94a3b8;">一个Demu尽享翰德姆全部服务</p>' +
      '</div>'
    });
  } catch (emailErr) {
    console.error('发送重置邮件失败:', emailErr);
    return {
      success: false,
      message: '邮件发送失败，请稍后重试或联系我们处理。',
      account: account,
      method: method
    };
  }

  return {
    success: true,
    found: true,
    message: '重置链接已发送到 ' + maskedEmail + '，请在 30 分钟内完成操作。',
    maskedEmail: maskedEmail,
    account: account,
    method: method
  };
}

/**
 * 验证重置 token 并设置新密码
 */
async function resetPassword(token, newPassword) {
  const [rows] = await pool.execute(
    `SELECT evt.id AS token_id, evt.user_id, evt.expires_at, u.username
     FROM email_verification_tokens evt
     JOIN users u ON u.id = evt.user_id
     WHERE evt.token = ? LIMIT 1`,
    ['reset_' + token]
  );

  if (rows.length === 0) {
    return { success: false, message: '重置链接无效' };
  }

  const record = rows[0];

  if (new Date(record.expires_at) < new Date()) {
    await pool.execute('DELETE FROM email_verification_tokens WHERE id = ?', [record.token_id]);
    return { success: false, message: '重置链接已过期，请重新申请' };
  }

  // 更新密码
  const hash = await bcrypt.hash(newPassword, SALT_ROUNDS);
  await pool.execute('UPDATE users SET password_hash = ? WHERE id = ?', [hash, record.user_id]);

  // 删除已使用的 token
  await pool.execute('DELETE FROM email_verification_tokens WHERE id = ?', [record.token_id]);

  return { success: true, message: '密码重置成功，请使用新密码登录' };
}

module.exports = { register, login, forgotPassword, resetPassword };
