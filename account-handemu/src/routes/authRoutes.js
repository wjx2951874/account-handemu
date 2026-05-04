// src/routes/authRoutes.js
const express = require('express');
const router = express.Router();
const { registerHandler, loginHandler, logoutHandler, forgotPasswordHandler, resetPasswordHandler } = require('../controllers/authController');
const requireAuth = require('../middlewares/authMiddleware');
const { checkIPBan } = require('../middlewares/rateLimitMiddleware');

// 注册
router.post('/register', registerHandler);

// 登录（带 IP 封禁检查）
router.post('/login', checkIPBan, loginHandler);

// 退出登录
router.post('/logout', logoutHandler);

// 获取当前用户信息（需要登录态）
router.get('/me', requireAuth, (req, res) => {
  res.json({
    success: true,
    data: {
      uid: req.user.uid,
      username: req.user.username,
      email: req.user.email
    }
  });
});

// 忘记密码（发送重置邮件）
router.post('/forgot-password', forgotPasswordHandler);

// 重置密码（通过 token 设置新密码）
router.post('/reset-password', resetPasswordHandler);

module.exports = router;
