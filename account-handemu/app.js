// app.js
require('dotenv').config();
const express = require('express');
const cookieParser = require('cookie-parser');
const path = require('path');
const errorHandler = require('./src/middlewares/errorHandler');

const app = express();
const PORT = process.env.PORT || 3000;

app.set('trust proxy', true);
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(cookieParser());

// 静态文件
app.use(express.static(path.join(__dirname, 'public')));

// ---- 路由 ----
const authRoutes = require('./src/routes/authRoutes');
const verifyRoutes = require('./src/routes/verifyRoutes');
const adminRoutes = require('./src/routes/adminRoutes');
const userRoutes = require('./src/routes/userRoutes');

app.use('/api/auth', authRoutes);
app.use('/verify', verifyRoutes);
app.use('/api/user', userRoutes);
app.use('/api/admin', adminRoutes);

// 全局错误处理
app.use(errorHandler);

app.listen(PORT, () => {
  console.log(`[Handemu Auth] 服务已启动 → http://127.0.0.1:${PORT}`);
});
