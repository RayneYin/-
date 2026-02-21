/**
 * HGDoll Web Plugin - Popup 设置页面逻辑
 * 管理用户配置（服务器地址、ASR凭证等），并与 background service worker 通信
 */

document.addEventListener('DOMContentLoaded', () => {
  const serverIpInput = document.getElementById('serverIp');
  const asrAppIdInput = document.getElementById('asrAppId');
  const asrAccessTokenInput = document.getElementById('asrAccessToken');
  const screenshotIntervalInput = document.getElementById('screenshotInterval');
  const saveBtn = document.getElementById('saveBtn');
  const toggleBtn = document.getElementById('toggleBtn');
  const statusText = document.getElementById('statusText');
  const statusDot = document.getElementById('statusDot');
  const messageDiv = document.getElementById('message');

  // ========== 加载已保存的设置 ==========
  chrome.storage.local.get(
    ['serverIp', 'asrAppId', 'asrAccessToken', 'screenshotInterval', 'isRunning'],
    (result) => {
      if (result.serverIp) serverIpInput.value = result.serverIp;
      if (result.asrAppId) asrAppIdInput.value = result.asrAppId;
      if (result.asrAccessToken) asrAccessTokenInput.value = result.asrAccessToken;
      if (result.screenshotInterval) screenshotIntervalInput.value = result.screenshotInterval;
      updateUI(result.isRunning || false);
    }
  );

  // ========== 保存设置 ==========
  saveBtn.addEventListener('click', () => {
    // 清理服务器地址：去除协议前缀和尾部斜杠
    let serverIp = serverIpInput.value.trim()
      .replace(/^https?:\/\//i, '')
      .replace(/^wss?:\/\//i, '')
      .replace(/\/+$/, '');
    serverIpInput.value = serverIp;

    const config = {
      serverIp: serverIp,
      asrAppId: asrAppIdInput.value.trim(),
      asrAccessToken: asrAccessTokenInput.value.trim(),
      screenshotInterval: parseInt(screenshotIntervalInput.value) || 3,
    };

    if (!config.serverIp) {
      showMessage('请填写服务器地址', 'error');
      return;
    }

    if (!config.asrAppId || !config.asrAccessToken) {
      showMessage('请填写 ASR App ID 和 Access Token', 'error');
      return;
    }

    chrome.storage.local.set(config, () => {
      showMessage('设置已保存', 'success');
      // 通知 background 更新配置
      chrome.runtime.sendMessage({ type: 'CONFIG_UPDATED', config });
    });
  });

  // ========== 启动/停止 ==========
  toggleBtn.addEventListener('click', () => {
    chrome.storage.local.get(['isRunning', 'serverIp'], (result) => {
      if (!result.serverIp) {
        showMessage('请先填写并保存服务器地址', 'error');
        return;
      }

      const newState = !result.isRunning;
      chrome.runtime.sendMessage(
        { type: newState ? 'START' : 'STOP' },
        (response) => {
          if (response && response.success) {
            chrome.storage.local.set({ isRunning: newState });
            updateUI(newState);
            showMessage(newState ? '陪玩已启动' : '陪玩已停止', 'success');
          } else {
            showMessage(response?.error || '操作失败', 'error');
          }
        }
      );
    });
  });

  // ========== UI 更新 ==========
  function updateUI(isRunning) {
    if (isRunning) {
      toggleBtn.textContent = '停止陪玩';
      toggleBtn.classList.remove('btn-success');
      toggleBtn.classList.add('btn-danger');
      statusText.textContent = '运行中';
      statusDot.classList.remove('offline');
      statusDot.classList.add('online');
    } else {
      toggleBtn.textContent = '启动陪玩';
      toggleBtn.classList.remove('btn-danger');
      toggleBtn.classList.add('btn-success');
      statusText.textContent = '未启动';
      statusDot.classList.remove('online');
      statusDot.classList.add('offline');
    }
  }

  function showMessage(text, type) {
    messageDiv.textContent = text;
    messageDiv.className = `message ${type}`;
    messageDiv.classList.remove('hidden');
    setTimeout(() => {
      messageDiv.classList.add('hidden');
    }, 3000);
  }
});
