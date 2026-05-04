const express = require('express');
const router = express.Router();
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const userController = require('../controllers/userController');
const authMiddleware = require('../middlewares/authMiddleware');

// 头像上传目录
const avatarDir = path.join(__dirname, '../../public/uploads/avatars');
if (!fs.existsSync(avatarDir)) {
  fs.mkdirSync(avatarDir, { recursive: true });
}

// multer 配置
const storage = multer.diskStorage({
  destination: function (req, file, cb) {
    cb(null, avatarDir);
  },
  filename: function (req, file, cb) {
    const ext = path.extname(file.originalname).toLowerCase() || '.jpg';
    const name = 'avatar_' + req.user.id + '_' + Date.now() + ext;
    cb(null, name);
  }
});

const upload = multer({
  storage: storage,
  limits: { fileSize: 2 * 1024 * 1024 },
  fileFilter: function (req, file, cb) {
    const allowed = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    if (allowed.includes(file.mimetype)) {
      cb(null, true);
    } else {
      cb(new Error('只支持 JPG/PNG/GIF/WebP 格式'));
    }
  }
});

// 所有接口需要登录
router.use(authMiddleware);

// 账号信息
router.get('/info', userController.getInfo);

// 个人资料
router.put('/profile', userController.updateProfile);
router.post('/avatar', upload.single('avatar'), userController.uploadAvatar);

// 安全中心
router.post('/change-password', userController.changePassword);
router.post('/send-bind-email-code', userController.sendBindEmailCode);
router.post('/bind-email', userController.bindEmail);
router.post('/bind-phone', userController.bindPhone);

// 设备管理
router.get('/sessions', userController.getActiveSessions);
router.post('/sessions/:sessionId/remove', userController.removeSession);

// 登录记录
router.get('/login-logs', userController.getLoginLogs);

// 账号设置
router.post('/delete-account', userController.deleteAccount);
router.get('/export-data', userController.exportData);

// 退出
router.post('/logout', userController.logout);

module.exports = router;
