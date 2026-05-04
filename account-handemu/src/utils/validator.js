// src/utils/validator.js

const USERNAME_RE = /^[a-zA-Z0-9_]{3,32}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PASSWORD_MIN = 8;
const PASSWORD_MAX = 64;

function validateUsername(val) {
  if (!val || typeof val !== 'string') return '用户名不能为空';
  if (!USERNAME_RE.test(val)) return '用户名只能包含字母、数字和下划线，长度 3~32';
  return null;
}

function validateEmail(val) {
  if (!val || typeof val !== 'string') return '邮箱不能为空';
  if (!EMAIL_RE.test(val)) return '邮箱格式不正确';
  if (val.length > 128) return '邮箱长度不能超过 128 个字符';
  return null;
}

function validatePassword(val) {
  if (!val || typeof val !== 'string') return '密码不能为空';
  if (val.length < PASSWORD_MIN) return `密码长度不能少于 ${PASSWORD_MIN} 位`;
  if (val.length > PASSWORD_MAX) return `密码长度不能超过 ${PASSWORD_MAX} 位`;
  return null;
}

module.exports = { validateUsername, validateEmail, validatePassword };
