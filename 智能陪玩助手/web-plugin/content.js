/**
 * HGDoll Web Plugin - Content Script
 * 注入到游戏网页中，创建悬浮面板用于显示 AI 陪玩助手回复和语音控制
 */

(function () {
  'use strict';

  // 防止重复注入
  if (document.getElementById('hgdoll-overlay')) return;

  // ========== 创建悬浮面板 ==========
  const overlay = document.createElement('div');
  overlay.id = 'hgdoll-overlay';
  overlay.innerHTML = `
    <div id="hgdoll-panel" class="hgdoll-panel hgdoll-collapsed">
      <div id="hgdoll-header" class="hgdoll-header">
        <span class="hgdoll-logo">HGDoll</span>
        <div class="hgdoll-header-btns">
          <button id="hgdoll-minimize" class="hgdoll-icon-btn" title="最小化">−</button>
          <button id="hgdoll-close" class="hgdoll-icon-btn" title="关闭">×</button>
        </div>
      </div>
      <div id="hgdoll-body" class="hgdoll-body">
        <div id="hgdoll-messages" class="hgdoll-messages">
          <div class="hgdoll-msg hgdoll-msg-bot">
            <span>欢迎你，接下来让 HG Doll 陪你一起玩耍吧！</span>
          </div>
        </div>
        <div class="hgdoll-input-area">
          <input type="text" id="hgdoll-text-input" class="hgdoll-text-input" placeholder="输入消息..." />
          <button id="hgdoll-send-btn" class="hgdoll-send-btn" title="发送">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
            </svg>
          </button>
          <button id="hgdoll-mic-btn" class="hgdoll-mic-btn" title="按住说话">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5z"/>
              <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
            </svg>
          </button>
        </div>
        <div class="hgdoll-controls">
          <span id="hgdoll-mic-status" class="hgdoll-mic-status">输入文字或点击麦克风说话</span>
        </div>
      </div>
    </div>
    <button id="hgdoll-fab" class="hgdoll-fab" title="HGDoll 陪玩助手">
      <span>🎮</span>
    </button>
  `;

  document.body.appendChild(overlay);

  // ========== DOM 引用 ==========
  const panel = document.getElementById('hgdoll-panel');
  const body = document.getElementById('hgdoll-body');
  const header = document.getElementById('hgdoll-header');
  const minimizeBtn = document.getElementById('hgdoll-minimize');
  const closeBtn = document.getElementById('hgdoll-close');
  const fab = document.getElementById('hgdoll-fab');
  const messagesDiv = document.getElementById('hgdoll-messages');
  const micBtn = document.getElementById('hgdoll-mic-btn');
  const micStatus = document.getElementById('hgdoll-mic-status');
  const textInput = document.getElementById('hgdoll-text-input');
  const sendBtn = document.getElementById('hgdoll-send-btn');

  let isExpanded = false;
  let isMicActive = false;

  // ========== 面板展开/折叠 ==========
  fab.addEventListener('click', () => {
    isExpanded = true;
    panel.classList.remove('hgdoll-collapsed');
    panel.classList.add('hgdoll-expanded');
    fab.style.display = 'none';
  });

  minimizeBtn.addEventListener('click', () => {
    isExpanded = false;
    panel.classList.remove('hgdoll-expanded');
    panel.classList.add('hgdoll-collapsed');
    fab.style.display = 'flex';
  });

  closeBtn.addEventListener('click', () => {
    overlay.style.display = 'none';
  });

  // ========== 拖拽功能 ==========
  let isDragging = false;
  let dragOffsetX = 0;
  let dragOffsetY = 0;

  header.addEventListener('mousedown', (e) => {
    isDragging = true;
    const rect = panel.getBoundingClientRect();
    dragOffsetX = e.clientX - rect.left;
    dragOffsetY = e.clientY - rect.top;
    panel.style.transition = 'none';
  });

  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    const x = e.clientX - dragOffsetX;
    const y = e.clientY - dragOffsetY;
    panel.style.left = `${Math.max(0, Math.min(x, window.innerWidth - 320))}px`;
    panel.style.top = `${Math.max(0, Math.min(y, window.innerHeight - 100))}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
  });

  document.addEventListener('mouseup', () => {
    isDragging = false;
    panel.style.transition = '';
  });

  // ========== 文字输入发送 ==========
  function sendTextMessage() {
    const text = textInput.value.trim();
    if (!text) return;
    addMessage(text, 'user');
    textInput.value = '';
    chrome.runtime.sendMessage({ type: 'SEND_TEXT', text: text });
  }

  sendBtn.addEventListener('click', sendTextMessage);
  textInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendTextMessage();
    }
  });

  // ========== 麦克风控制 ==========
  micBtn.addEventListener('click', () => {
    isMicActive = !isMicActive;
    if (isMicActive) {
      micBtn.classList.add('hgdoll-mic-active');
      micStatus.textContent = '正在录音...';
      chrome.runtime.sendMessage({ type: 'MIC_START' });
    } else {
      micBtn.classList.remove('hgdoll-mic-active');
      micStatus.textContent = '点击麦克风开始说话';
      chrome.runtime.sendMessage({ type: 'MIC_STOP' });
    }
  });

  // ========== 接收消息 ==========
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log('HGDoll content: 收到消息', message.type,
      message.type === 'AI_RESPONSE' ? message.text?.substring(0, 50) : '',
      message.type === 'PLAY_AUDIO' ? `audio=${message.audioData?.length} chars` : '');
    switch (message.type) {
      case 'AI_RESPONSE':
        addMessage(message.text, 'bot');
        // 收到 AI 回复后清除状态文字
        micStatus.textContent = '输入文字或点击麦克风说话';
        break;
      case 'USER_SPEECH':
        addMessage(message.text, 'user');
        break;
      case 'ASR_PARTIAL':
        micStatus.textContent = `识别中: ${message.text}`;
        break;
      case 'STATUS_UPDATE':
        if (message.text) {
          micStatus.textContent = message.text;
        }
        break;
      case 'PLAY_AUDIO':
        playAudio(message.audioData);
        break;
      case 'SHOW_PANEL':
        overlay.style.display = 'block';
        fab.click();
        break;
      case 'HIDE_PANEL':
        overlay.style.display = 'none';
        break;
    }
    // 告知 background 消息已送达
    sendResponse({ received: true });
    return true;
  });

  // ========== 添加消息到面板 ==========
  function addMessage(text, role) {
    if (!text || text.trim() === '') return;
    const msgDiv = document.createElement('div');
    msgDiv.className = `hgdoll-msg hgdoll-msg-${role}`;
    msgDiv.innerHTML = `<span>${escapeHtml(text)}</span>`;
    messagesDiv.appendChild(msgDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    // 最多保留 50 条消息
    while (messagesDiv.children.length > 50) {
      messagesDiv.removeChild(messagesDiv.firstChild);
    }
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ========== 音频播放 ==========
  function playAudio(base64Audio) {
    try {
      const audioData = atob(base64Audio);
      const arrayBuffer = new Uint8Array(audioData.length);
      for (let i = 0; i < audioData.length; i++) {
        arrayBuffer[i] = audioData.charCodeAt(i);
      }
      const blob = new Blob([arrayBuffer], { type: 'audio/mp3' });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.play().catch(err => console.warn('HGDoll: 音频播放失败', err));
      audio.onended = () => URL.revokeObjectURL(url);
    } catch (e) {
      console.warn('HGDoll: 音频解码失败', e);
    }
  }

  // ========== 麦克风录音模块（对应 Android AsrService 的录音部分） ==========
  let mediaStream = null;
  let audioContext = null;
  let scriptProcessor = null;
  let isRecording = false;

  // 接收来自 background 的录音控制指令
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'START_RECORDING') {
      startRecording();
    } else if (message.type === 'STOP_RECORDING') {
      stopRecording();
    }
  });

  async function startRecording() {
    if (isRecording) return;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });

      audioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: 16000,
      });

      const source = audioContext.createMediaStreamSource(mediaStream);

      // 使用 ScriptProcessorNode 获取 PCM 数据（兼容性好）
      // bufferSize=4096 ~ 约 256ms 的数据
      scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);

      scriptProcessor.onaudioprocess = (e) => {
        if (!isRecording) return;
        const inputData = e.inputBuffer.getChannelData(0);
        // 将 Float32 转换为 Int16 PCM（与 Android 端 PCM_16BIT 一致）
        const pcm16 = float32ToInt16(inputData);
        // 转为 base64 发送到 background
        const base64 = arrayBufferToBase64(pcm16.buffer);
        chrome.runtime.sendMessage({
          type: 'AUDIO_DATA',
          data: base64,
        });
      };

      source.connect(scriptProcessor);
      scriptProcessor.connect(audioContext.destination);
      isRecording = true;
      console.log('HGDoll: 录音已开始');
    } catch (err) {
      console.error('HGDoll: 无法获取麦克风权限', err);
      chrome.runtime.sendMessage({
        type: 'STATUS_UPDATE',
        text: '麦克风权限被拒绝',
      });
    }
  }

  function stopRecording() {
    isRecording = false;
    if (scriptProcessor) {
      scriptProcessor.disconnect();
      scriptProcessor = null;
    }
    if (audioContext) {
      audioContext.close();
      audioContext = null;
    }
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
    console.log('HGDoll: 录音已停止');
  }

  function float32ToInt16(float32Array) {
    const int16Array = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return int16Array;
  }

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }
})();
