// src/middlewares/rateLimitMiddleware.js
const { isIPBanned } = require('../services/rateLimitService');
const { writeLoginLog } = require('../services/securityService');

/**
 * 全局 IP 封禁检查中间件
 * 挂载在登录路由前
 */
async function checkIPBan(req, res, next) {
  try {
    const ip = req.ip || req.connection.remoteAddress;
    if (await isIPBanned(ip)) {
      await writeLoginLog({
        userId: null,
        ip,
        userAgent: req.get('User-Agent') || '',
        status: 'fail'
      });
      return res.status(429).json({
        success: false,
        message: '当前 IP 已被临时封禁，请 10 分钟后再试'
      });
    }
    next();
  } catch (err) {
    next(err);
  }
}

module.exports = { checkIPBan };
