const pool = require('../config/database');
const bcrypt = require('bcryptjs');
const path = require('path');
const fs = require('fs');

/**
 * 解析 User-Agent 简易版
 */
function parseUA(ua) {
  ua = ua || '';
  let browser = 'Unknown', os = 'Unknown', device = 'Desktop';

  if (/Mobile|Android|iPhone|iPad/.test(ua)) device = 'Mobile';
  if (/Tablet|iPad/.test(ua)) device = 'Tablet';

  if (/Edg\//.test(ua)) browser = 'Edge';
  else if (/Chrome\//.test(ua)) browser = 'Chrome';
  else if (/Firefox\//.test(ua)) browser = 'Firefox';
  else if (/Safari\//.test(ua) && !/Chrome/.test(ua)) browser = 'Safari';

  if (/Windows NT/.test(ua)) os = 'Windows';
  else if (/Mac OS X/.test(ua)) os = 'macOS';
  else if (/Linux/.test(ua)) os = 'Linux';
  else if (/Android/.test(ua)) os = 'Android';
  else if (/iPhone|iPad/.test(ua)) os = 'iOS';

  return { browser, os, device };
}

/**
 * 获取用户完整信息
 */
async function getUserInfo(userId) {
  const [users] = await pool.execute(
    `SELECT u.id, u.username, u.email, u.phone, u.nickname, u.status,
            u.last_login_at, u.last_login_ip, u.created_at,
            p.avatar_url, p.bio
     FROM users u
     LEFT JOIN user_profiles p ON p.user_id = u.id
     WHERE u.id = ?`,
    [userId]
  );

  if (users.length === 0) {
    return { success: false, message: '用户不存在' };
  }

  const user = users[0];

  // 安全概况
  const security = {
    passwordSet: true,
    emailBound: !!user.email,
    phoneBound: !!user.phone
  };

  // 脱敏
  const maskedEmail = user.email
    ? user.email.replace(/^(.{2}).*(@.*)$/, '$1***$2')
    : null;
  const maskedPhone = user.phone
    ? user.phone.replace(/^(.{3}).*(.{4})$/, '$1****$2')
    : null;

  // 最近3条登录记录
  const [recentLogs] = await pool.execute(
    `SELECT ip, device, browser, os, status, created_at
     FROM login_logs WHERE user_id = ?
     ORDER BY created_at DESC LIMIT 3`,
    [userId]
  );

  return {
    success: true,
    data: {
      id: user.id,
      username: user.username,
      email: maskedEmail,
      emailBound: security.emailBound,
      phone: maskedPhone,
      phoneBound: security.phoneBound,
      nickname: user.nickname || user.username,
      avatarUrl: user.avatar_url || '',
      bio: user.bio || '',
      status: user.status,
      lastLoginAt: user.last_login_at,
      lastLoginIp: user.last_login_ip,
      createdAt: user.created_at,
      security: security,
      recentLogs: recentLogs
    }
  };
}

/**
 * 更新个人资料
 */
async function updateProfile(userId, data) {
  const { nickname, bio } = data;

  if (nickname !== undefined) {
    if (nickname.length > 50) {
      return { success: false, message: '昵称不能超过50个字符' };
    }
    if (nickname.trim().length === 0) {
      return { success: false, message: '昵称不能为空' };
    }
    await pool.execute('UPDATE users SET nickname = ? WHERE id = ?',
      [nickname.trim(), userId]);
  }

  await pool.execute(
    `INSERT INTO user_profiles (user_id, nickname, bio)
     VALUES (?, ?, ?)
     ON DUPLICATE KEY UPDATE
       nickname = VALUES(nickname),
       bio = VALUES(bio)`,
    [userId, (nickname || '').trim(), (bio || '').substring(0, 200)]
  );

  return { success: true, message: '资料更新成功' };
}

/**
 * 上传头像
 */
async function updateAvatar(userId, filePath) {
  await pool.execute(
    `INSERT INTO user_profiles (user_id, avatar_url)
     VALUES (?, ?)
     ON DUPLICATE KEY UPDATE avatar_url = VALUES(avatar_url)`,
    [userId, filePath]
  );
  return { success: true, message: '头像更新成功', data: { avatarUrl: filePath } };
}

/**
 * 修改密码
 */
async function changePassword(userId, oldPassword, newPassword) {
  const [users] = await pool.execute(
    'SELECT password_hash FROM users WHERE id = ?', [userId]
  );
  if (users.length === 0) {
    return { success: false, message: '用户不存在' };
  }

  const valid = await bcrypt.compare(oldPassword, users[0].password_hash);
  if (!valid) {
    return { success: false, message: '原密码不正确' };
  }

  if (newPassword.length < 8) {
    return { success: false, message: '新密码至少8位' };
  }
  if (!/[a-z]/.test(newPassword) || !/[A-Z]/.test(newPassword) || !/[0-9]/.test(newPassword)) {
    return { success: false, message: '新密码需包含大小写字母和数字' };
  }

  const hash = await bcrypt.hash(newPassword, 12);
  await pool.execute('UPDATE users SET password_hash = ? WHERE id = ?', [hash, userId]);

  return { success: true, message: '密码修改成功，下次登录请使用新密码' };
}

/**
 * 绑定/换绑邮箱
 */
async function bindEmail(userId, email, code) {
  // 验证码校验（复用注册验证码逻辑）
  const [codes] = await pool.execute(
    `SELECT id FROM email_verification_tokens
     WHERE token = ? AND expires_at > NOW()
     LIMIT 1`,
    ['bind_' + code + '_' + email]
  );

  if (codes.length === 0) {
    return { success: false, message: '验证码无效或已过期' };
  }

  await pool.execute('DELETE FROM email_verification_tokens WHERE id = ?', [codes[0].id]);

  const [existing] = await pool.execute(
    'SELECT id FROM users WHERE email = ? AND id != ?', [email, userId]
  );
  if (existing.length > 0) {
    return { success: false, message: '该邮箱已被其他账号绑定' };
  }

  await pool.execute('UPDATE users SET email = ? WHERE id = ?', [email, userId]);
  return { success: true, message: '邮箱绑定成功' };
}

/**
 * 发送绑定邮箱验证码
 */
async function sendBindEmailCode(userId, email) {
  // 检查邮箱是否已被绑定
  const [existing] = await pool.execute(
    'SELECT id FROM users WHERE email = ? AND id != ?', [email, userId]
  );
  if (existing.length > 0) {
    return { success: false, message: '该邮箱已被其他账号绑定' };
  }

  // 限流
  const [recent] = await pool.execute(
    `SELECT id FROM email_verification_tokens
     WHERE token LIKE ? AND created_at > DATE_SUB(NOW(), INTERVAL 1 MINUTE)
     LIMIT 1`,
    ['bind_%_' + email]
  );
  if (recent.length > 0) {
    return { success: false, message: '发送过于频繁，请1分钟后重试' };
  }

  const code = String(Math.floor(100000 + Math.random() * 900000));
  const expiresAt = new Date(Date.now() + 10 * 60 * 1000);

  await pool.execute(
    `INSERT INTO email_verification_tokens (user_id, token, expires_at)
     VALUES (?, ?, ?)`,
    [userId, 'bind_' + code + '_' + email, expiresAt]
  );

  const emailService = require('./emailService');
  await emailService.sendEmail({
    to: email,
    subject: 'Demu账号 邮箱绑定验证码',
    html: '<div style="max-width:480px;margin:0 auto;font-family:sans-serif;color:#1e293b;">' +
      '<h2 style="color:#2563eb;">邮箱绑定验证</h2>' +
      '<p>你的验证码是：</p>' +
      '<div style="text-align:center;margin:24px 0;">' +
        '<span style="display:inline-block;padding:12px 32px;background:#f1f5f9;' +
        'border-radius:8px;font-size:28px;font-weight:700;letter-spacing:6px;color:#2563eb;">' +
        code + '</span>' +
      '</div>' +
      '<p style="font-size:13px;color:#64748b;">验证码 10 分钟内有效，请勿泄露给他人。</p>' +
      '<hr style="margin:24px 0;border:none;border-top:1px solid #e2e8f0;" />' +
      '<p style="font-size:12px;color:#94a3b8;">一个Demu尽享翰德姆全部服务</p>' +
    '</div>'
  });

  return { success: true, message: '验证码已发送' };
}

/**
 * 绑定手机号
 */
async function bindPhone(userId, phone, code) {
  const [codes] = await pool.execute(
    `SELECT id FROM sms_codes
     WHERE phone = ? AND code = ? AND purpose = 'bind'
       AND used = 0 AND expires_at > NOW()
     ORDER BY created_at DESC LIMIT 1`,
    [phone, code]
  );
  if (codes.length === 0) {
    return { success: false, message: '验证码无效或已过期' };
  }

  await pool.execute('UPDATE sms_codes SET used = 1 WHERE id = ?', [codes[0].id]);

  const [existing] = await pool.execute(
    'SELECT id FROM users WHERE phone = ? AND id != ?', [phone, userId]
  );
  if (existing.length > 0) {
    return { success: false, message: '该手机号已被其他账号绑定' };
  }

  await pool.execute('UPDATE users SET phone = ? WHERE id = ?', [phone, userId]);
  return { success: true, message: '手机号绑定成功' };
}

/**
 * 获取登录记录
 */
async function getLoginLogs(userId, page, pageSize, status) {
  page = Math.max(1, parseInt(page) || 1);
  pageSize = Math.min(50, Math.max(5, parseInt(pageSize) || 10));
  const offset = (page - 1) * pageSize;

  let where = 'WHERE user_id = ?';
  const params = [userId];

  if (status === 'success' || status === 'failed') {
    where += ' AND status = ?';
    params.push(status);
  }

  const [total] = await pool.execute(
    'SELECT COUNT(*) AS cnt FROM login_logs ' + where, params
  );

  const queryParams = [...params, pageSize, offset];
  const [logs] = await pool.execute(
    'SELECT id, ip, device, browser, os, region, status, fail_reason, created_at ' +
    'FROM login_logs ' + where + ' ORDER BY created_at DESC LIMIT ? OFFSET ?',
    queryParams
  );

  return {
    success: true,
    data: {
      list: logs,
      total: total[0].cnt,
      page: page,
      pageSize: pageSize
    }
  };
}

/**
 * 获取活跃会话
 */
async function getActiveSessions(userId) {
  const [sessions] = await pool.execute(
    `SELECT id, ip, device, browser, os, last_active_at, created_at
     FROM user_sessions
     WHERE user_id = ? AND expires_at > NOW()
     ORDER BY last_active_at DESC`,
    [userId]
  );
  return { success: true, data: sessions };
}

/**
 * 踢出会话（需验证密码）
 */
async function removeSession(userId, sessionId, password) {
  const [users] = await pool.execute(
    'SELECT password_hash FROM users WHERE id = ?', [userId]
  );
  if (users.length === 0) {
    return { success: false, message: '用户不存在' };
  }

  const valid = await bcrypt.compare(password, users[0].password_hash);
  if (!valid) {
    return { success: false, message: '密码验证失败' };
  }

  const [result] = await pool.execute(
    'DELETE FROM user_sessions WHERE id = ? AND user_id = ?',
    [sessionId, userId]
  );

  if (result.affectedRows === 0) {
    return { success: false, message: '会话不存在或已失效' };
  }

  return { success: true, message: '已下线该设备' };
}

/**
 * 注销账号
 */
async function deleteAccount(userId, password) {
  const [users] = await pool.execute(
    'SELECT password_hash FROM users WHERE id = ?', [userId]
  );
  if (users.length === 0) {
    return { success: false, message: '用户不存在' };
  }

  const valid = await bcrypt.compare(password, users[0].password_hash);
  if (!valid) {
    return { success: false, message: '密码不正确' };
  }

  await pool.execute(
    "UPDATE users SET status = 'deleted', email = NULL, phone = NULL WHERE id = ?",
    [userId]
  );
  await pool.execute('DELETE FROM user_sessions WHERE user_id = ?', [userId]);

  return { success: true, message: '账号已注销' };
}

/**
 * 导出用户数据
 */
async function exportData(userId) {
  const [users] = await pool.execute(
    `SELECT u.id, u.username, u.email, u.phone, u.nickname, u.status, u.created_at,
            p.avatar_url, p.bio
     FROM users u
     LEFT JOIN user_profiles p ON p.user_id = u.id
     WHERE u.id = ?`,
    [userId]
  );

  const [logs] = await pool.execute(
    `SELECT ip, device, browser, os, status, created_at
     FROM login_logs WHERE user_id = ?
     ORDER BY created_at DESC LIMIT 100`,
    [userId]
  );

  return {
    success: true,
    data: {
      exportTime: new Date().toISOString(),
      profile: users[0] || {},
      loginLogs: logs
    }
  };
}

/**
 * 记录登录日志
 */
async function recordLoginLog(userId, ip, userAgent, status, failReason) {
  const ua = parseUA(userAgent);
  await pool.execute(
    `INSERT INTO login_logs (user_id, ip, user_agent, device, browser, os, status, fail_reason)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [userId, ip || '', userAgent || '', ua.device, ua.browser, ua.os, status, failReason || '']
  );

  if (status === 'success') {
    await pool.execute(
      'UPDATE users SET last_login_at = NOW(), last_login_ip = ? WHERE id = ?',
      [ip || '', userId]
    );
  }
}

module.exports = {
  getUserInfo,
  updateProfile,
  updateAvatar,
  changePassword,
  bindEmail,
  sendBindEmailCode,
  bindPhone,
  getLoginLogs,
  getActiveSessions,
  removeSession,
  deleteAccount,
  exportData,
  recordLoginLog,
  parseUA
};
