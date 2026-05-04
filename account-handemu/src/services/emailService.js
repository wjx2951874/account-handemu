// src/services/emailService.js
const nodemailer = require('nodemailer');
const { pool } = require('../models/db');
const { generateEmailToken } = require('../utils/token');

// ---- 创建 SMTP 传输器 ----
const transporter = nodemailer.createTransport({
  host: process.env.SMTP_HOST,
  port: parseInt(process.env.SMTP_PORT, 10) || 465,
  secure: process.env.SMTP_SECURE === 'true',
  auth: {
    user: process.env.SMTP_USER,
    pass: process.env.SMTP_PASS
  },
  connectionTimeout: 10000,
  greetingTimeout: 10000
});

// 启动时验证 SMTP 连接
transporter.verify()
  .then(() => console.log('[邮件] SMTP 连接成功'))
  .catch((err) => console.error('[邮件] SMTP 连接失败:', err.message));

/**
 * 生成邮件通用 HTML 模板
 */
function buildEmailHTML({ username, bodyContent }) {
  return `
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8" />
      <style>
        body {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
            'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
          background: #f5f7fa;
          padding: 40px 0;
          margin: 0;
        }
        .container {
          max-width: 480px;
          margin: 0 auto;
          background: #fff;
          border-radius: 16px;
          padding: 48px 40px;
          box-shadow: 0 4px 24px rgba(0,0,0,0.06);
        }
        .logo {
          text-align: center;
          font-size: 24px;
          font-weight: 700;
          color: #1e293b;
          margin-bottom: 8px;
        }
        .logo span { color: #2563eb; }
        .slogan {
          text-align: center;
          font-size: 13px;
          color: #64748b;
          margin-bottom: 32px;
        }
        .greeting {
          font-size: 15px;
          color: #1e293b;
          margin-bottom: 16px;
        }
        .text {
          font-size: 14px;
          color: #475569;
          line-height: 1.7;
          margin-bottom: 24px;
        }
        .btn-wrap { text-align: center; margin-bottom: 24px; }
        .btn {
          display: inline-block;
          padding: 12px 36px;
          background: #2563eb;
          color: #fff !important;
          text-decoration: none;
          border-radius: 10px;
          font-size: 15px;
          font-weight: 600;
        }
        .link-text {
          font-size: 12px;
          color: #94a3b8;
          word-break: break-all;
          line-height: 1.6;
        }
        .noreply-notice {
          margin-top: 28px;
          padding: 14px 16px;
          background: #f8fafc;
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          font-size: 12px;
          color: #94a3b8;
          line-height: 1.7;
        }
        .noreply-notice strong {
          color: #64748b;
        }
        .footer {
          margin-top: 28px;
          padding-top: 20px;
          border-top: 1px solid #e2e8f0;
          font-size: 12px;
          color: #94a3b8;
          text-align: center;
          line-height: 1.7;
        }
        .footer a {
          color: #2563eb;
          text-decoration: none;
        }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="logo"><span>Demu</span> 账号</div>
        <div class="slogan">一个 Demu 账号，畅享全部服务</div>

        <div class="greeting">你好，${username}：</div>

        ${bodyContent}

        <div class="noreply-notice">
          <strong>请勿直接回复此邮件。</strong>本邮件由系统自动发送，回复将不会被处理。<br />
          如需帮助或有任何疑问，请访问 <a href="https://www.handemu.com" style="color:#2563eb; text-decoration:none;">www.handemu.com</a> 查找联系方式。
        </div>

        <div class="footer">
          此邮件由 Handemu 系统自动发送，请勿回复。<br />
          &copy; ${new Date().getFullYear()} Handemu. All rights reserved.<br />
          <a href="https://www.handemu.com">www.handemu.com</a>
        </div>
      </div>
    </body>
    </html>
  `;
}

/**
 * 发送验证邮件
 */
async function sendVerificationEmail({ to, username, verifyLink }) {
  const fromName = process.env.MAIL_FROM_NAME || 'Demu 账号';
  const fromAddress = process.env.MAIL_FROM_ADDRESS || process.env.SMTP_USER;

  const bodyContent = `
    <div class="text">
      感谢注册 Demu 账号。请点击下方按钮验证您的邮箱地址，完成账号激活。<br />
      此链接将在 <strong>15 分钟</strong>后失效。
    </div>

    <div class="btn-wrap">
      <a href="${verifyLink}" class="btn">验证邮箱</a>
    </div>

    <div class="text">如果按钮无法点击，请复制以下链接到浏览器打开：</div>
    <div class="link-text">${verifyLink}</div>
  `;

  const html = buildEmailHTML({ username, bodyContent });

  const mailOptions = {
    from: `"${fromName}" <${fromAddress}>`,
    to,
    subject: '【Demu】请验证您的邮箱地址',
    html,
    // 设置 Reply-To 为 noreply 强化不要回复的语义
    replyTo: `"请勿回复" <${fromAddress}>`,
    headers: {
      'X-Auto-Response-Suppress': 'All',
      'Auto-Submitted': 'auto-generated'
    }
  };

  const info = await transporter.sendMail(mailOptions);
  console.log('[邮件] 发送成功, messageId:', info.messageId);
  return info;
}

/**
 * 创建邮箱验证 token 并发送验证邮件
 */
async function createVerificationToken(userId) {
  const token = generateEmailToken();
  const expiresSeconds = parseInt(process.env.EMAIL_TOKEN_EXPIRES, 10) || 900;
  const expiresAt = new Date(Date.now() + expiresSeconds * 1000);

  await pool.execute(
    `INSERT INTO email_verification_tokens (user_id, token, expires_at) VALUES (?, ?, ?)`,
    [userId, token, expiresAt]
  );

  const siteUrl = process.env.SITE_URL || 'http://127.0.0.1:3000';
  const verifyLink = `${siteUrl}/verify?token=${token}`;

  const [rows] = await pool.execute(
    'SELECT username, email FROM users WHERE id = ?',
    [userId]
  );

  if (rows.length > 0) {
    const user = rows[0];

    if (process.env.SMTP_HOST && process.env.SMTP_USER && process.env.SMTP_PASS) {
      try {
        await sendVerificationEmail({
          to: user.email,
          username: user.username,
          verifyLink
        });
      } catch (err) {
        console.error('[邮件] 发送失败:', err.message);
        console.log('[邮件回退] 验证链接:', verifyLink);
      }
    } else {
      console.log('========================================');
      console.log('[邮件模拟] 未配置 SMTP，验证链接:');
      console.log(verifyLink);
      console.log('========================================');
    }
  }

  return { token, verifyLink };
}

module.exports = { createVerificationToken, sendVerificationEmail, buildEmailHTML };
