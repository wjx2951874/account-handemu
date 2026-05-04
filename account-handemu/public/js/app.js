// public/js/app.js

/**
 * 显示消息提示
 */
function showMessage(text, type) {
  const el = document.getElementById('message');
  if (!el) return;
  el.textContent = text;
  el.className = 'message show ' + (type || 'error');
}

/**
 * 清除消息提示
 */
function clearMessage() {
  const el = document.getElementById('message');
  if (!el) return;
  el.textContent = '';
  el.className = 'message';
}
