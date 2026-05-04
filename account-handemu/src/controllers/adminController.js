// src/controllers/adminController.js
const adminService = require('../services/adminService');
const userManageService = require('../services/userManageService');
const { validateUsername, validateEmail, validatePassword } = require('../utils/validator');

// ---- 管理员认证 ----

async function adminLoginHandler(req, res, next) {
  try {
    const { username, password } = req.body;
    if (!username || !password) {
      return res.status(400).json({ success: false, message: '请输入用户名和密码' });
    }
    const ip = req.ip || req.connection.remoteAddress;
    const result = await adminService.adminLogin({ username, password, ip });
    if (!result.success) return res.status(401).json(result);

    res.cookie('admin_token', result.data.token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 12 * 60 * 60 * 1000,
      path: '/'
    });
    return res.json({ success: true, data: result.data.admin });
  } catch (err) { next(err); }
}

async function adminLogoutHandler(req, res) {
  res.clearCookie('admin_token', { httpOnly: true, path: '/' });
  return res.json({ success: true, message: '已退出' });
}

async function adminMeHandler(req, res) {
  return res.json({ success: true, data: req.admin });
}

async function changePasswordHandler(req, res, next) {
  try {
    const { oldPassword, newPassword } = req.body;
    if (!oldPassword || !newPassword) {
      return res.status(400).json({ success: false, message: '请填写完整' });
    }
    const pe = validatePassword(newPassword);
    if (pe) return res.status(400).json({ success: false, message: pe });
    const result = await adminService.changeAdminPassword({
      adminId: req.admin.adminId, oldPassword, newPassword
    });
    return res.json(result);
  } catch (err) { next(err); }
}

// ---- 仪表盘 ----

async function dashboardHandler(req, res, next) {
  try {
    const stats = await userManageService.getDashboardStats();
    return res.json({ success: true, data: stats });
  } catch (err) { next(err); }
}

// ---- 用户管理 ----

async function userListHandler(req, res, next) {
  try {
    const result = await userManageService.getUserList(req.query);
    return res.json({ success: true, data: result });
  } catch (err) { next(err); }
}

async function userDetailHandler(req, res, next) {
  try {
    const detail = await userManageService.getUserDetail(req.params.id);
    if (!detail) return res.status(404).json({ success: false, message: '用户不存在' });
    return res.json({ success: true, data: detail });
  } catch (err) { next(err); }
}

async function updateStatusHandler(req, res, next) {
  try {
    const result = await userManageService.updateUserStatus(req.params.id, req.body.status);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'update_user_status',
        targetType: 'user', targetId: req.params.id,
        detail: `状态变更为 ${req.body.status}`, ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function updateUsernameHandler(req, res, next) {
  try {
    const ue = validateUsername(req.body.username);
    if (ue) return res.status(400).json({ success: false, message: ue });
    const result = await userManageService.updateUsername(req.params.id, req.body.username);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'update_username',
        targetType: 'user', targetId: req.params.id,
        detail: `用户名修改为 ${req.body.username}`, ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function updateEmailHandler(req, res, next) {
  try {
    const ee = validateEmail(req.body.email);
    if (ee) return res.status(400).json({ success: false, message: ee });
    const result = await userManageService.updateUserEmail(req.params.id, req.body.email);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'update_email',
        targetType: 'user', targetId: req.params.id,
        detail: `邮箱修改为 ${req.body.email}`, ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function resetPasswordHandler(req, res, next) {
  try {
    const pe = validatePassword(req.body.password);
    if (pe) return res.status(400).json({ success: false, message: pe });
    const result = await userManageService.resetUserPassword(req.params.id, req.body.password);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'reset_password',
        targetType: 'user', targetId: req.params.id,
        detail: '密码已重置', ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function deleteUserHandler(req, res, next) {
  try {
    const detail = await userManageService.getUserDetail(req.params.id);
    const result = await userManageService.deleteUser(req.params.id);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'delete_user',
        targetType: 'user', targetId: req.params.id,
        detail: `删除用户 ${detail ? detail.user.username : req.params.id}`, ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function batchStatusHandler(req, res, next) {
  try {
    const { userIds, status } = req.body;
    const result = await userManageService.batchUpdateStatus(userIds, status);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'batch_update_status',
        targetType: 'user', detail: `批量更新 ${userIds.length} 个用户状态为 ${status}`, ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

async function batchDeleteHandler(req, res, next) {
  try {
    const { userIds } = req.body;
    const result = await userManageService.batchDeleteUsers(userIds);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'batch_delete',
        targetType: 'user', detail: `批量删除 ${userIds.length} 个用户`, ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

// ---- 日志查询 ----

async function loginLogsHandler(req, res, next) {
  try {
    const result = await userManageService.getLoginLogs(req.query);
    return res.json({ success: true, data: result });
  } catch (err) { next(err); }
}

async function securityLogsHandler(req, res, next) {
  try {
    const result = await userManageService.getSecurityLogs(req.query);
    return res.json({ success: true, data: result });
  } catch (err) { next(err); }
}

async function ipBansHandler(req, res, next) {
  try {
    const result = await userManageService.getIPBans(req.query);
    return res.json({ success: true, data: result });
  } catch (err) { next(err); }
}

async function removeIPBanHandler(req, res, next) {
  try {
    const result = await userManageService.removeIPBan(req.params.id);
    if (result.success) {
      await adminService.writeAdminLog({
        adminId: req.admin.adminId, action: 'remove_ip_ban',
        targetType: 'ip_ban', targetId: req.params.id,
        detail: '解除 IP 封禁', ip: req.ip
      });
    }
    return res.json(result);
  } catch (err) { next(err); }
}

module.exports = {
  adminLoginHandler, adminLogoutHandler, adminMeHandler, changePasswordHandler,
  dashboardHandler,
  userListHandler, userDetailHandler, updateStatusHandler,
  updateUsernameHandler, updateEmailHandler, resetPasswordHandler, deleteUserHandler,
  batchStatusHandler, batchDeleteHandler,
  loginLogsHandler, securityLogsHandler, ipBansHandler, removeIPBanHandler
};
