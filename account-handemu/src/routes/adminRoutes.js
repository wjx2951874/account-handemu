// src/routes/adminRoutes.js
const express = require('express');
const router = express.Router();
const { requireAdmin, requireSuperAdmin } = require('../middlewares/adminAuthMiddleware');
const admin = require('../controllers/adminController');

// 管理员登录（不需要鉴权）
router.post('/login', admin.adminLoginHandler);

// 以下接口都需要管理员登录
router.use(requireAdmin);

router.post('/logout', admin.adminLogoutHandler);
router.get('/me', admin.adminMeHandler);
router.post('/change-password', admin.changePasswordHandler);

// 仪表盘
router.get('/dashboard', admin.dashboardHandler);

// 用户管理
router.get('/users', admin.userListHandler);
router.get('/users/:id', admin.userDetailHandler);
router.put('/users/:id/status', admin.updateStatusHandler);
router.put('/users/:id/username', admin.updateUsernameHandler);
router.put('/users/:id/email', admin.updateEmailHandler);
router.put('/users/:id/password', admin.resetPasswordHandler);
router.delete('/users/:id', admin.deleteUserHandler);

// 批量操作
router.post('/users/batch/status', admin.batchStatusHandler);
router.post('/users/batch/delete', admin.batchDeleteHandler);

// 日志
router.get('/logs/login', admin.loginLogsHandler);
router.get('/logs/security', admin.securityLogsHandler);

// IP 封禁管理
router.get('/ip-bans', admin.ipBansHandler);
router.delete('/ip-bans/:id', admin.removeIPBanHandler);

module.exports = router;
