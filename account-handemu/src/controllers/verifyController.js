// src/controllers/verifyController.js
const { pool } = require('../models/db');

/**
 * GET /verify?token=xxx
 */
async function verifyEmailHandler(req, res, next) {
  try {
    const { token } = req.query;

    if (!token) {
      return res.redirect('/verify-fail.html?reason=missing_token');
    }

    // 查找 token
    const [rows] = await pool.execute(
      `SELECT evt.id AS token_id, evt.user_id, evt.expires_at, u.status
       FROM email_verification_tokens evt
       JOIN users u ON u.id = evt.user_id
       WHERE evt.token = ?
       LIMIT 1`,
      [token]
    );

    if (rows.length === 0) {
      return res.redirect('/verify-fail.html?reason=invalid_token');
    }

    const record = rows[0];

    // 检查是否已激活
    if (record.status === 'active') {
      return res.redirect('/verify-success.html?already=1');
    }

    // 检查是否过期
    if (new Date(record.expires_at) < new Date()) {
      return res.redirect('/verify-fail.html?reason=expired');
    }

    // 激活账号
    await pool.execute(
      `UPDATE users SET status = 'active' WHERE id = ?`,
      [record.user_id]
    );

    // 删除已使用的 token
    await pool.execute(
      `DELETE FROM email_verification_tokens WHERE id = ?`,
      [record.token_id]
    );

    return res.redirect('/verify-success.html');
  } catch (err) {
    next(err);
  }
}

module.exports = { verifyEmailHandler };
