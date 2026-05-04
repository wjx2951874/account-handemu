const jwt = require('jsonwebtoken');

function authMiddleware(req, res, next) {
  let token = null;

  if (req.cookies && req.cookies.token) {
    token = req.cookies.token;
  } else if (req.headers.authorization) {
    const parts = req.headers.authorization.split(' ');
    if (parts.length === 2 && parts[0] === 'Bearer') {
      token = parts[1];
    }
  }

  if (!token) {
    return res.status(401).json({
      success: false,
      message: '请先登录',
      code: 'UNAUTHORIZED'
    });
  }

  try {
    const secret = process.env.JWT_SECRET || 'handemu_jwt_secret_2024';
    const decoded = jwt.verify(token, secret);
    req.user = {
      id: decoded.userId || decoded.id,
      username: decoded.username
    };
    next();
  } catch (err) {
    return res.status(401).json({
      success: false,
      message: '登录已过期，请重新登录',
      code: 'TOKEN_EXPIRED'
    });
  }
}

module.exports = authMiddleware;
