// src/utils/token.js
const jwt = require('jsonwebtoken');
const { v4: uuidv4 } = require('uuid');

/**
 * 生成 JWT
 */
function signJWT(payload) {
  return jwt.sign(payload, process.env.JWT_SECRET, {
    expiresIn: process.env.JWT_EXPIRES_IN || '7d'
  });
}

/**
 * 验证 JWT
 */
function verifyJWT(token) {
  return jwt.verify(token, process.env.JWT_SECRET);
}

/**
 * 生成邮箱验证 token（UUID）
 */
function generateEmailToken() {
  return uuidv4();
}

module.exports = { signJWT, verifyJWT, generateEmailToken };
