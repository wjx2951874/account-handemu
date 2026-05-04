const userService = require('../services/userService');
const path = require('path');
const fs = require('fs');

async function getInfo(req, res, next) {
  try {
    const result = await userService.getUserInfo(req.user.id);
    return res.json(result);
  } catch (err) { next(err); }
}

async function updateProfile(req, res, next) {
  try {
    const result = await userService.updateProfile(req.user.id, req.body);
    return res.json(result);
  } catch (err) { next(err); }
}

async function uploadAvatar(req, res, next) {
  try {
    if (!req.file) {
      return res.status(400).json({ success: false, message: '请选择图片' });
    }

    // 文件大小限制 2MB
    if (req.file.size > 2 * 1024 * 1024) {
      // 删除已上传的文件
      fs.unlinkSync(req.file.path);
      return res.status(400).json({ success: false, message: '图片不能超过 2MB' });
    }

    // 只允许图片
    const allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    if (!allowedTypes.includes(req.file.mimetype)) {
      fs.unlinkSync(req.file.path);
      return res.status(400).json({ success: false, message: '只支持 JPG/PNG/GIF/WebP 格式' });
    }

    const avatarUrl = '/uploads/avatars/' + req.file.filename;
    const result = await userService.updateAvatar(req.user.id, avatarUrl);
    return res.json(result);
  } catch (err) { next(err); }
}

async function changePassword(req, res, next) {
  try {
    const { oldPassword, newPassword } = req.body;
    if (!oldPassword || !newPassword) {
      return res.status(400).json({ success: false, message: '请填写完整' });
    }
    const result = await userService.changePassword(req.user.id, oldPassword, newPassword);
    return res.json(result);
  } catch (err) { next(err); }
}

async function sendBindEmailCode(req, res, next) {
  try {
    const { email } = req.body;
    if (!email) {
      return res.status(400).json({ success: false, message: '请输入邮箱' });
    }
    const result = await userService.sendBindEmailCode(req.user.id, email);
    return res.json(result);
  } catch (err) { next(err); }
}

async function bindEmail(req, res, next) {
  try {
    const { email, code } = req.body;
    if (!email || !code) {
      return res.status(400).json({ success: false, message: '请填写完整' });
    }
    const result = await userService.bindEmail(req.user.id, email, code);
    return res.json(result);
  } catch (err) { next(err); }
}

async function bindPhone(req, res, next) {
  try {
    const { phone, code } = req.body;
    if (!phone || !code) {
      return res.status(400).json({ success: false, message: '请填写完整' });
    }
    const result = await userService.bindPhone(req.user.id, phone, code);
    return res.json(result);
  } catch (err) { next(err); }
}

async function getLoginLogs(req, res, next) {
  try {
    const { page, pageSize, status } = req.query;
    const result = await userService.getLoginLogs(req.user.id, page, pageSize, status);
    return res.json(result);
  } catch (err) { next(err); }
}

async function getActiveSessions(req, res, next) {
  try {
    const result = await userService.getActiveSessions(req.user.id);
    return res.json(result);
  } catch (err) { next(err); }
}

async function removeSession(req, res, next) {
  try {
    const { sessionId } = req.params;
    const { password } = req.body;
    if (!password) {
      return res.status(400).json({ success: false, message: '请输入密码验证' });
    }
    const result = await userService.removeSession(req.user.id, sessionId, password);
    return res.json(result);
  } catch (err) { next(err); }
}

async function deleteAccount(req, res, next) {
  try {
    const { password } = req.body;
    if (!password) {
      return res.status(400).json({ success: false, message: '请输入密码确认' });
    }
    const result = await userService.deleteAccount(req.user.id, password);
    if (result.success) {
      res.clearCookie('token');
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function exportData(req, res, next) {
  try {
    const result = await userService.exportData(req.user.id);
    if (result.success) {
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Content-Disposition',
        'attachment; filename="demu_data_' + req.user.id + '.json"');
      return res.send(JSON.stringify(result.data, null, 2));
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function logout(req, res, next) {
  try {
    // 删除当前会话
    if (req.cookies && req.cookies.token) {
      const jwt = require('jsonwebtoken');
      try {
        const secret = process.env.JWT_SECRET || 'handemu_jwt_secret_2024';
        const decoded = jwt.verify(req.cookies.token, secret);
        if (decoded.sessionId) {
          await require('../config/database').execute(
            'DELETE FROM user_sessions WHERE session_token = ?',
            [decoded.sessionId]
          );
        }
      } catch (e) { /* token 无效也没关系 */ }
    }
    res.clearCookie('token');
    return res.json({ success: true, message: '已退出登录' });
  } catch (err) { next(err); }
}

module.exports = {
  getInfo,
  updateProfile,
  uploadAvatar,
  changePassword,
  sendBindEmailCode,
  bindEmail,
  bindPhone,
  getLoginLogs,
  getActiveSessions,
  removeSession,
  deleteAccount,
  exportData,
  logout
};
