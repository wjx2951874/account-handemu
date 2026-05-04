// src/config/database.js
// 桥接文件：将 config/database 指向 models/db
// 项目中多处 require('../config/database')，保持兼容
const { pool } = require('../models/db');
module.exports = pool;
