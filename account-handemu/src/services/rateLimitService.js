// src/services/rateLimitService.js
const { redis } = require('../models/db');

const IP_FAIL_PREFIX = 'ratelimit:ip_fail:';
const ACCOUNT_FAIL_PREFIX = 'ratelimit:acct_fail:';
const IP_BAN_PREFIX = 'ratelimit:ip_ban:';

const IP_FAIL_WINDOW = 300;       // 5 分钟窗口
const IP_FAIL_THRESHOLD = 5;      // 5 次失败
const IP_BAN_DURATION = 600;      // 封禁 10 分钟
const ACCOUNT_FAIL_THRESHOLD = 5; // 账号连续失败 5 次

/**
 * 检查 IP 是否被封禁
 */
async function isIPBanned(ip) {
  const banned = await redis.get(`${IP_BAN_PREFIX}${ip}`);
  return !!banned;
}

/**
 * 记录 IP 登录失败
 */
async function recordIPFail(ip) {
  const key = `${IP_FAIL_PREFIX}${ip}`;
  const count = await redis.incr(key);
  if (count === 1) {
    await redis.expire(key, IP_FAIL_WINDOW);
  }
  if (count >= IP_FAIL_THRESHOLD) {
    await redis.set(`${IP_BAN_PREFIX}${ip}`, '1', 'EX', IP_BAN_DURATION);
    return { banned: true, count };
  }
  return { banned: false, count };
}

/**
 * 检查账号是否被锁定
 */
async function isAccountLocked(identifier) {
  const key = `${ACCOUNT_FAIL_PREFIX}${identifier}`;
  const count = await redis.get(key);
  return parseInt(count, 10) >= ACCOUNT_FAIL_THRESHOLD;
}

/**
 * 记录账号登录失败
 */
async function recordAccountFail(identifier) {
  const key = `${ACCOUNT_FAIL_PREFIX}${identifier}`;
  const count = await redis.incr(key);
  if (count === 1) {
    await redis.expire(key, IP_FAIL_WINDOW);
  }
  return parseInt(count, 10);
}

/**
 * 登录成功后清除账号失败计数
 */
async function clearAccountFail(identifier) {
  await redis.del(`${ACCOUNT_FAIL_PREFIX}${identifier}`);
}

/**
 * 登录成功后清除 IP 失败计数
 */
async function clearIPFail(ip) {
  await redis.del(`${IP_FAIL_PREFIX}${ip}`);
}

module.exports = {
  isIPBanned,
  recordIPFail,
  isAccountLocked,
  recordAccountFail,
  clearAccountFail,
  clearIPFail,
  IP_BAN_DURATION
};
