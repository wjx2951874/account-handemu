// src/middlewares/errorHandler.js

/**
 * 全局错误处理中间件
 */
function errorHandler(err, req, res, _next) {
  console.error('[Error]', err.stack || err.message || err);

  // CORS 错误
  if (err.message && err.message.includes('CORS')) {
    return res.status(403).json({ success: false, message: '跨域请求被拒绝' });
  }

  return res.status(500).json({
    success: false,
    message: '服务器内部错误，请稍后再试'
  });
}

module.exports = errorHandler;
