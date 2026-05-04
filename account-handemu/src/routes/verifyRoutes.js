// src/routes/verifyRoutes.js
const express = require('express');
const router = express.Router();
const { verifyEmailHandler } = require('../controllers/verifyController');

// GET /verify?token=xxx
router.get('/', verifyEmailHandler);

module.exports = router;
