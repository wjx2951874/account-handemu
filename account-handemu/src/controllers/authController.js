// src/controllers/authController.js
const authService = require('../services/authService');
const { validateUsername, validateEmail, validatePassword } = require('../utils/validator');

/**
 * POST /api/auth/register
 */
async function registerHandler(req, res, next) {
  try {
    const { username, email, password, confirm_password } = req.body;

    // 参数校验
    const errors = [];
    const ue = validateUsername(username);
    if (ue) errors.push(ue);
    const ee = validateEmail(email);
    if (ee) errors.push(ee);
    const pe = validatePassword(password);
    if (pe) errors.push(pe);
    if (password !== confirm_password) {
      errors.push('两次输入的密码不一致');
    }

    if (errors.length > 0) {
      return res.status(400).json({ success: false, message: errors.join('；') });
    }

    const result = await authService.register({ username, email, password });

    if (!result.success) {
      return res.status(409).json(result);
    }

    return res.status(201).json(result);
  } catch (err) {
    next(err);
  }
}

/**
 * POST /api/auth/login
 */
async function loginHandler(req, res, next) {
  try {
    const { account, password } = req.body;
    const ip = req.headers['x-forwarded-for'] || req.connection.remoteAddress || '';
    const userAgent = req.headers['user-agent'] || '';

    const result = await authService.login(account, password, ip, userAgent);

    if (result.success && result.token) {
      // 设置 cookie
      const isHTTPS = req.secure || req.headers['x-forwarded-proto'] === 'https';
      res.cookie('token', result.token, {
        httpOnly: true,
        secure: isHTTPS,
        sameSite: 'lax',
        domain: process.env.COOKIE_DOMAIN || undefined,
        maxAge: 7 * 24 * 60 * 60 * 1000 // 7天
      });
    }

    return res.json(result);
  } catch (err) {
    next(err);
  }
}

/**
 * POST /api/auth/logout
 */
async function logoutHandler(req, res) {
  res.clearCookie('token', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    domain: process.env.COOKIE_DOMAIN || undefined,
    path: '/'
  });
  return res.json({ success: true, message: '已退出登录' });
}

async function forgotPasswordHandler(req, res, next) {
  try {
    const { method, account } = req.body;

    if (!method || !account) {
      return res.status(400).json({ success: false, message: '请填写完整' });
    }

    const allowedMethods = ['email', 'username', 'phone'];
    if (!allowedMethods.includes(method)) {
      return res.status(400).json({ success: false, message: '不支持的找回方式' });
    }

    // IP 级别限流：1小时内最多请求 10 次
    const ip = req.ip || req.headers['x-forwarded-for'] || 'unknown';
    const [ipCount] = await require('../config/database').execute(
      "SELECT COUNT(*) AS cnt FROM email_verification_tokens WHERE token LIKE 'reset\\_%' AND created_at > DATE_SUB(NOW(), INTERVAL 1 HOUR)"
    );
    // 简单全局限流，生产环境建议用 redis 按 IP 限流
    if (ipCount[0].cnt > 50) {
      return res.status(429).json({ success: false, message: '系统繁忙，请稍后再试' });
    }

    const result = await authService.forgotPassword(method, account);
    return res.json(result);
  } catch (err) {
    next(err);
  }
}

async function resetPasswordHandler(req, res, next) {
  try {
    const { token, password } = req.body;
    if (!token || !password) {
      return res.status(400).json({ success: false, message: '参数不完整' });
    }
    const { validatePassword } = require('../utils/validator');
    const pe = validatePassword(password);
    if (pe) return res.status(400).json({ success: false, message: pe });

    const result = await authService.resetPassword(token, password);
    return res.json(result);
  } catch (err) { next(err); }
}

module.exports = { registerHandler, loginHandler, logoutHandler, forgotPasswordHandler, resetPasswordHandler };
