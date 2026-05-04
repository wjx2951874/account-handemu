// src/middlewares/adminAuthMiddleware.js
const jwt = require('jsonwebtoken');

/**
 * 管理员鉴权中间件
 * 从 Cookie 中读取 admin_token
 */
function requireAdmin(req, res, next) {
  const token = req.cookies.admin_token;

  if (!token) {
    return res.status(401).json({ success: false, message: '未登录' });
  }

  try {
    const decoded = jwt.verify(token, process.env.JWT_SECRET);
    if (!decoded.isAdmin) {
      return res.status(403).json({ success: false, message: '无权限' });
    }
    req.admin = decoded;
    next();
  } catch (err) {
    return res.status(401).json({ success: false, message: '登录已过期' });
  }
}

/**
 * 超级管理员权限检查
 */
function requireSuperAdmin(req, res, next) {
  if (!req.admin || req.admin.role !== 'super_admin') {
    return res.status(403).json({ success: false, message: '需要超级管理员权限' });
  }
  next();
}

module.exports = { requireAdmin, requireSuperAdmin };
