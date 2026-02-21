/**
 * HGDoll Web Plugin - Background Service Worker
 * 
 * 核心功能（对应 Android 端）：
 * 1. 定时截图 → 对应 ScreenshotService（使用 chrome.tabs.captureVisibleTab）
 * 2. 语音识别 → 对应 AsrService（通过 offscreen document 录音 + Doubao ASR WebSocket）
 * 3. 与后端通信 → 发送截图/文字到 server，接收 AI 回复和 TTS 音频
 */

// ========== 全局状态 ==========
let config = {
  serverIp: '',
  asrAppId: '',
  asrAccessToken: '',
  screenshotInterval: 3,
};
let contextId = '';
let isRunning = false;
let screenshotTimer = null;
let isProcessingScreenshot = false;
let isProcessingChat = false;
let chatProcessingStartTime = 0; // 记录开始处理的时间，用于超时保护
const CHAT_TIMEOUT_MS = 30000; // 聊天请求超时时间 30 秒
const CHAT_STUCK_TIMEOUT_MS = 60000; // isProcessingChat 卡住超时保护 60 秒
let screenshotCount = 0;
const PROACTIVE_CHAT_INTERVAL = 5; // 每 5 次截图后主动发起对话
let pendingUserMessage = null; // 用户消息队列，避免 isProcessingChat 时丢弃用户输入

// ========== ASR 相关常量（同 Android AsrService） ==========
const ASR_URL = 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel';
const ASR_RESOURCE_ID = 'volc.bigasr.sauc.duration';
const SAMPLE_RATE = 16000;
const PROTOCOL_VERSION = 0b0001;
const DEFAULT_HEADER_SIZE = 0b0001;
const FULL_CLIENT_REQUEST = 0b0001;
const AUDIO_ONLY_REQUEST = 0b0010;
const JSON_SERIAL = 0b0001;
const GZIP_COMPRESS = 0b0001;
const POS_SEQUENCE = 0b0001;

let asrWebSocket = null;
let asrSequence = 0;
let isMicActive = false;

// ========== 初始化 ==========
chrome.runtime.onInstalled.addListener(() => {
  console.log('HGDoll Web Plugin installed');
  loadConfig();
});

chrome.runtime.onStartup.addListener(() => {
  loadConfig();
});

// Manifest V3 Service Worker 保活机制
// Service Worker 在 30s 无活动后可能被终止，导致状态丢失
let keepAliveTimer = null;
function startKeepAlive() {
  if (keepAliveTimer) return;
  keepAliveTimer = setInterval(() => {
    // 简单的自我 ping 保持活跃
    if (isRunning) {
      chrome.runtime.getPlatformInfo(() => {});
    }
  }, 25000); // 每 25 秒 ping 一次
}
function stopKeepAlive() {
  if (keepAliveTimer) {
    clearInterval(keepAliveTimer);
    keepAliveTimer = null;
  }
}

function loadConfig() {
  chrome.storage.local.get(
    ['serverIp', 'asrAppId', 'asrAccessToken', 'screenshotInterval', 'isRunning', 'contextId'],
    (result) => {
      // 清理服务器地址：去除协议前缀和尾部斜杠
      if (result.serverIp) {
        config.serverIp = result.serverIp
          .replace(/^https?:\/\//i, '')
          .replace(/^wss?:\/\//i, '')
          .replace(/\/+$/, '');
      }
      if (result.asrAppId) config.asrAppId = result.asrAppId.trim();
      if (result.asrAccessToken) config.asrAccessToken = result.asrAccessToken.trim();
      if (result.screenshotInterval) config.screenshotInterval = result.screenshotInterval;
      // 恢复 contextId（Service Worker 重启后保持上下文一致性）
      if (result.contextId) contextId = result.contextId;
      if (result.isRunning) {
        // 恢复运行状态
        startService();
      }
    }
  );
}

// ========== 消息处理 ==========
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'START':
      loadConfig();
      startService()
        .then(() => sendResponse({ success: true }))
        .catch((err) => sendResponse({ success: false, error: err.message }));
      return true; // 异步响应

    case 'STOP':
      stopService();
      sendResponse({ success: true });
      break;

    case 'CONFIG_UPDATED':
      Object.assign(config, message.config);
      break;

    case 'MIC_START':
      startMicrophone();
      break;

    case 'MIC_STOP':
      stopMicrophone();
      break;

    case 'SEND_TEXT':
      if (message.text && message.text.trim()) {
        const userText = message.text.trim();
        if (isProcessingChat) {
          // 排队等待，不丢弃用户输入
          pendingUserMessage = userText;
          console.log('HGDoll: 当前正在处理中，用户消息已排队:', userText);
          broadcastToTabs({ type: 'STATUS_UPDATE', text: '消息已排队，稍后发送...' });
        } else {
          sendChatMessage(userText);
        }
      }
      break;
  }
});

// ========== 服务启动/停止 ==========
async function startService() {
  if (isRunning) return;

  config = await getConfig();
  if (!config.serverIp) {
    throw new Error('请先配置服务器地址');
  }

  // 如果没有已保存的 contextId，生成新的
  if (!contextId) {
    contextId = generateUUID();
  }
  isRunning = true;
  screenshotCount = 0;
  
  // 持久化 contextId，防止 Service Worker 重启后丢失
  chrome.storage.local.set({ contextId, isRunning: true });
  
  // 启动保活机制
  startKeepAlive();
  
  console.log(`HGDoll: 服务已启动, contextId=${contextId}`);

  // 通知 content script 显示面板
  broadcastToTabs({ type: 'SHOW_PANEL' });

  // 延迟发送初始化消息，确保 content script 面板准备就绪
  setTimeout(async () => {
    await sendChatMessage('应用初始化');
  }, 1000);

  // 开始定时截图
  startScreenshotLoop();
}

function stopService() {
  isRunning = false;
  contextId = '';

  // 清除持久化的运行状态
  chrome.storage.local.set({ isRunning: false, contextId: '' });

  // 停止保活
  stopKeepAlive();

  // 停止截图
  if (screenshotTimer) {
    clearInterval(screenshotTimer);
    screenshotTimer = null;
  }

  // 停止 ASR
  stopMicrophone();

  // 重置聊天状态
  isProcessingChat = false;
  chatProcessingStartTime = 0;
  pendingUserMessage = null;

  // 通知 content script
  broadcastToTabs({ type: 'STATUS_UPDATE', text: '已停止' });

  console.log('HGDoll: 服务已停止');
}

// ========== 截图模块（对应 Android ScreenshotService） ==========
function startScreenshotLoop() {
  if (screenshotTimer) {
    clearInterval(screenshotTimer);
  }

  const intervalMs = (config.screenshotInterval || 3) * 1000;

  screenshotTimer = setInterval(async () => {
    if (!isRunning || isProcessingScreenshot) return;
    await captureAndUploadScreenshot();
  }, intervalMs);

  console.log(`HGDoll: 截图循环已启动, 间隔=${intervalMs}ms`);
}

async function captureAndUploadScreenshot() {
  isProcessingScreenshot = true;
  try {
    // 获取当前活动标签页
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      console.warn('HGDoll: 无法获取活动标签页');
      return;
    }

    // 使用 chrome.tabs.captureVisibleTab 截图（对应 Android 的 MediaProjection）
    const dataUrl = await chrome.tabs.captureVisibleTab(null, {
      format: 'jpeg',
      quality: 80,
    });

    // 提取 base64 数据
    const base64Image = dataUrl.replace(/^data:image\/jpeg;base64,/, '');
    console.log(`HGDoll: 截图完成, 大小=${base64Image.length} 字符`);

    // 上传到服务器（对应 Android ScreenshotService.uploadScreenshot）
    await uploadScreenshot(base64Image);
  } catch (err) {
    console.error('HGDoll: 截图失败', err);
  } finally {
    isProcessingScreenshot = false;
  }
}

async function uploadScreenshot(base64Image) {
  const url = `http://${config.serverIp}/api/v3/bots/chat/completions`;

  // 构建请求体（格式与 Android 端一致）
  const body = {
    model: 'bot-20241114164326-xlcc91',
    stream: false,
    messages: [
      {
        role: 'user',
        content: [
          { type: 'text', text: '' },
          {
            type: 'image_url',
            image_url: { url: `data:image/jpeg;base64,${base64Image}` },
          },
        ],
      },
    ],
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Context-Id': contextId,
      },
      body: JSON.stringify(body),
    });

    if (response.ok) {
      console.log('HGDoll: 截图上传成功');
      // 每 N 次截图后主动触发 AI 对话，让 AI 基于已分析的画面主动说话
      screenshotCount++;
      // 超时保护：如果 isProcessingChat 卡住超过 60 秒，强制重置
      if (isProcessingChat && chatProcessingStartTime > 0 &&
          Date.now() - chatProcessingStartTime > CHAT_STUCK_TIMEOUT_MS) {
        console.warn('HGDoll: isProcessingChat 超时，强制重置');
        isProcessingChat = false;
        chatProcessingStartTime = 0;
      }
      if (screenshotCount >= PROACTIVE_CHAT_INTERVAL && !isProcessingChat) {
        screenshotCount = 0;
        console.log('HGDoll: 触发主动对话 (screenshotCount reached interval)');
        sendChatMessage('根据你刚才看到的画面，和我聊聊吧');
      }
    } else {
      console.error('HGDoll: 截图上传失败', response.status);
    }
  } catch (err) {
    console.error('HGDoll: 截图上传网络错误', err);
  }
}

// ========== 聊天/语音消息模块（对应 Android AsrService 的 sendToServer） ==========
async function sendChatMessage(text) {
  if (!config.serverIp || !contextId) {
    console.warn('HGDoll: sendChatMessage 跳过 - serverIp 或 contextId 为空');
    broadcastToTabs({ type: 'STATUS_UPDATE', text: '服务未启动，请先点击"启动陪玩"' });
    return;
  }
  if (isProcessingChat) {
    console.log('HGDoll: sendChatMessage 跳过 - 正在处理中, 已耗时:',
      chatProcessingStartTime > 0 ? `${Date.now() - chatProcessingStartTime}ms` : 'unknown');
    return;
  }

  isProcessingChat = true;
  chatProcessingStartTime = Date.now();
  const url = `http://${config.serverIp}/api/v3/bots/chat/completions`;
  console.log(`HGDoll: 发送聊天消息 "${text.substring(0, 50)}" -> ${url}`);

  // 立即给用户反馈，避免等待时无任何显示
  broadcastToTabs({ type: 'STATUS_UPDATE', text: 'AI 正在思考...' });

  // 使用流式请求以获取更快的首字响应和可靠的音频数据
  const body = {
    model: 'bot-20241114164326-xlcc91',
    stream: true,
    messages: [
      {
        role: 'user',
        content: [{ type: 'text', text: text }],
      },
    ],
  };

  // 使用 AbortController 实现超时保护
  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    controller.abort();
    console.error(`HGDoll: 聊天请求超时 (${CHAT_TIMEOUT_MS}ms)`);
  }, CHAT_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Context-Id': contextId,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    console.log('HGDoll: 服务器响应状态:', response.status);

    if (response.ok) {
      const contentType = response.headers.get('content-type') || '';

      if (contentType.includes('text/event-stream')) {
        // 流式 SSE 响应解析
        await handleStreamResponse(response);
      } else {
        // 非流式 JSON 响应解析（fallback）
        await handleJsonResponse(response);
      }
    } else {
      const errBody = await response.text().catch(() => '');
      console.error('HGDoll: 聊天请求失败', response.status, errBody.substring(0, 200));
      broadcastToTabs({ type: 'AI_RESPONSE', text: `[请求失败] HTTP ${response.status}，请检查服务器是否运行` });
      broadcastToTabs({ type: 'STATUS_UPDATE', text: `请求失败: HTTP ${response.status}` });
    }
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      console.error('HGDoll: 聊天请求超时被中止');
      broadcastToTabs({ type: 'STATUS_UPDATE', text: '请求超时，请检查服务器状态' });
    } else {
      console.error('HGDoll: 聊天请求网络错误', err.message || err);
      broadcastToTabs({ type: 'AI_RESPONSE', text: `[连接失败] ${err.message || '网络错误'}，请确认服务器地址和端口` });
      broadcastToTabs({ type: 'STATUS_UPDATE', text: `网络错误: ${err.message || '未知'}` });
    }
  } finally {
    isProcessingChat = false;
    chatProcessingStartTime = 0;
    console.log('HGDoll: 聊天处理完成, isProcessingChat 已重置为 false');
    broadcastToTabs({ type: 'STATUS_UPDATE', text: '' });
    // 处理排队中的用户消息
    if (pendingUserMessage) {
      const nextMsg = pendingUserMessage;
      pendingUserMessage = null;
      console.log('HGDoll: 处理排队消息:', nextMsg);
      sendChatMessage(nextMsg);
    }
  }
}

/**
 * 处理流式 SSE 响应 - 提取 transcript 和音频数据
 * 
 * 使用 response.text() 而非 response.body.getReader() 以确保
 * 在 Chrome MV3 Service Worker 中的可靠性（ReadableStream 在 SW 中有兼容问题）
 */
async function handleStreamResponse(response) {
  let replyText = '';
  let audioChunks = [];
  let chunkCount = 0;

  try {
    // 优先尝试 ReadableStream（实时性更好），失败则回退到 text()
    if (response.body && typeof response.body.getReader === 'function') {
      try {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            const trimmedLine = line.trim();
            if (!trimmedLine.startsWith('data:')) continue;
            const jsonStr = trimmedLine.slice(5).trim();
            if (jsonStr === '[DONE]') continue;

            try {
              const chunk = JSON.parse(jsonStr);
              chunkCount++;
              if (chunk.choices && chunk.choices.length > 0) {
                const delta = chunk.choices[0].delta;
                if (delta) {
                  if (delta.content) replyText += delta.content;
                  if (delta.audio) {
                    if (delta.audio.transcript) replyText += delta.audio.transcript;
                    if (delta.audio.data) audioChunks.push(delta.audio.data);
                  }
                }
              }
            } catch (parseErr) {
              // 忽略无法解析的行
            }
          }
        }
        console.log(`HGDoll: ReadableStream 解析完成, ${chunkCount} chunks`);
      } catch (streamErr) {
        console.warn('HGDoll: ReadableStream 读取失败，回退到 text():', streamErr.message || streamErr);
        // ReadableStream 失败后无法复用 response，需要重新标记
        // 但此时 body 可能已部分消费，只能使用已收集到的 partial data
        if (chunkCount === 0) {
          console.error('HGDoll: ReadableStream 0 chunks，响应未被读取');
          broadcastToTabs({ type: 'STATUS_UPDATE', text: '读取响应失败，请重试' });
          return;
        }
      }
    } else {
      // Service Worker 不支持 ReadableStream，使用 text() 回退
      console.warn('HGDoll: response.body 不可用，使用 text() 回退');
      const fullText = await response.text();
      console.log(`HGDoll: text() 读取完成, 长度=${fullText.length}`);
      const lines = fullText.split('\n');

      for (const line of lines) {
        const trimmedLine = line.trim();
        if (!trimmedLine.startsWith('data:')) continue;
        const jsonStr = trimmedLine.slice(5).trim();
        if (jsonStr === '[DONE]') continue;

        try {
          const chunk = JSON.parse(jsonStr);
          chunkCount++;
          if (chunk.choices && chunk.choices.length > 0) {
            const delta = chunk.choices[0].delta;
            if (delta) {
              if (delta.content) replyText += delta.content;
              if (delta.audio) {
                if (delta.audio.transcript) replyText += delta.audio.transcript;
                if (delta.audio.data) audioChunks.push(delta.audio.data);
              }
            }
          }
        } catch (parseErr) {
          // 忽略无法解析的行
        }
      }
      console.log(`HGDoll: text() 解析完成, ${chunkCount} chunks`);
    }
  } catch (readErr) {
    console.error('HGDoll: 读取响应异常:', readErr);
    broadcastToTabs({ type: 'STATUS_UPDATE', text: `读取响应异常: ${readErr.message || '未知错误'}` });
  }

  console.log('HGDoll: 流式响应完成 - 文本:', replyText ? replyText.substring(0, 80) + '...' : '(空)');
  console.log('HGDoll: 流式响应完成 - 音频块数:', audioChunks.length);

  // 发送回复到 content script
  if (replyText) {
    broadcastToTabs({ type: 'AI_RESPONSE', text: replyText });
    broadcastToTabs({ type: 'STATUS_UPDATE', text: '' });
  }
  if (audioChunks.length > 0) {
    const fullAudio = audioChunks.join('');
    broadcastToTabs({ type: 'PLAY_AUDIO', audioData: fullAudio });
  }

  if (!replyText && audioChunks.length === 0) {
    console.warn('HGDoll: 服务器返回成功但无有效回复内容 (chunks parsed:', chunkCount, ')');
    broadcastToTabs({ type: 'STATUS_UPDATE', text: '服务器返回了空回复，请重试' });
  }
}

/**
 * 处理非流式 JSON 响应（fallback）
 */
async function handleJsonResponse(response) {
  const data = await response.json();
  console.log('HGDoll: JSON 响应 choices 数量:', data.choices?.length || 0);

  let replyText = '';
  let audioData = null;

  if (data.choices && data.choices.length > 0) {
    const choice = data.choices[0];
    if (choice.message) {
      // 优先从 audio.transcript 提取文字（TTS 模式下 content 为 null）
      if (choice.message.audio && choice.message.audio.transcript) {
        replyText = choice.message.audio.transcript;
      } else if (choice.message.content) {
        replyText = choice.message.content;
      }
      // 提取音频
      if (choice.message.audio && choice.message.audio.data) {
        audioData = choice.message.audio.data;
      }
    }
    // 流式 chunk 格式兼容
    if (!replyText && choice.delta) {
      if (choice.delta.audio && choice.delta.audio.transcript) {
        replyText = choice.delta.audio.transcript;
      } else if (choice.delta.content) {
        replyText = choice.delta.content;
      }
    }
  }

  console.log('HGDoll: 提取到回复文本:', replyText ? replyText.substring(0, 80) + '...' : '(空)');
  console.log('HGDoll: 提取到音频数据:', audioData ? `${audioData.length} 字符` : '无');

  if (replyText) {
    broadcastToTabs({ type: 'AI_RESPONSE', text: replyText });
  }
  if (audioData) {
    broadcastToTabs({ type: 'PLAY_AUDIO', audioData: audioData });
  }

  if (!replyText && !audioData) {
    console.warn('HGDoll: 服务器返回成功但无有效回复内容');
    broadcastToTabs({ type: 'STATUS_UPDATE', text: '服务器返回了空回复' });
  }
}

// ========== 麦克风/ASR 模块（对应 Android AsrService） ==========

/**
 * 麦克风录音通过 offscreen document 实现（Service Worker 无法直接访问 MediaRecorder）
 * 录音数据通过 WebSocket 发送到 Doubao 流式ASR，识别结果发送到后端
 * 
 * 简化方案：在 content script 中录音，发送 PCM 数据到 background，
 * background 通过 WebSocket 发送到 ASR 服务
 */
async function startMicrophone() {
  if (isMicActive) return;
  isMicActive = true;

  // 通知当前标签页开始录音
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.id) {
    chrome.tabs.sendMessage(tab.id, { type: 'START_RECORDING' });
  }

  // 连接 ASR WebSocket
  connectAsrWebSocket();

  broadcastToTabs({ type: 'STATUS_UPDATE', text: '正在录音...' });
}

function stopMicrophone() {
  isMicActive = false;

  // 通知停止录音
  chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
    if (tab && tab.id) {
      chrome.tabs.sendMessage(tab.id, { type: 'STOP_RECORDING' });
    }
  });

  // 关闭 ASR WebSocket
  if (asrWebSocket) {
    asrWebSocket.close(1000, 'User stopped');
    asrWebSocket = null;
  }

  broadcastToTabs({ type: 'STATUS_UPDATE', text: '录音已停止' });
}

function connectAsrWebSocket() {
  if (!config.asrAppId || !config.asrAccessToken) {
    console.warn('HGDoll: ASR 凭证未配置');
    broadcastToTabs({ type: 'STATUS_UPDATE', text: 'ASR 凭证未配置，请在设置中填写 App ID 和 Access Token' });
    return;
  }

  // 浏览器 WebSocket API 不支持自定义 Header，Doubao ASR 要求认证 Header
  // 因此直接使用服务端 WebSocket 代理（服务端会在代理中携带认证 Header）
  if (!config.serverIp) {
    console.warn('HGDoll: 服务器地址未配置，无法连接 ASR');
    broadcastToTabs({ type: 'STATUS_UPDATE', text: '服务器地址未配置，请在设置中填写' });
    return;
  }

  console.log(`HGDoll: ASR 连接参数 - serverIp=${config.serverIp}, appId=${config.asrAppId}, token=${config.asrAccessToken.substring(0, 4)}***`);
  connectAsrViaServer();
}

/**
 * 通过服务端 WebSocket 代理进行 ASR（备选方案）
 */
function connectAsrViaServer() {
  if (!config.serverIp) return;

  // 如果已有连接且状态正常，不重复创建
  if (asrWebSocket && (asrWebSocket.readyState === WebSocket.CONNECTING || asrWebSocket.readyState === WebSocket.OPEN)) {
    console.log('HGDoll: ASR WebSocket 已存在且状态正常，跳过重连');
    return;
  }

  try {
    const wsUrl = `ws://${config.serverIp}/ws/asr?app_id=${encodeURIComponent(config.asrAppId)}&access_token=${encodeURIComponent(config.asrAccessToken)}`;
    console.log('HGDoll: 连接 ASR 代理:', wsUrl);
    asrWebSocket = new WebSocket(wsUrl);
    asrSequence = 0;

    asrWebSocket.onopen = () => {
      console.log('HGDoll: ASR 代理 WebSocket 已连接');
      broadcastToTabs({ type: 'STATUS_UPDATE', text: '语音识别已就绪' });
    };

    asrWebSocket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.error) {
          console.error('HGDoll: ASR 服务端错误:', data.error);
          broadcastToTabs({ type: 'STATUS_UPDATE', text: `ASR 错误: ${data.error}` });
          return;
        }
        if (data.type === 'ack') {
          console.log('HGDoll: ASR 服务端确认');
          return;
        }
        if (data.text) {
          if (data.is_final) {
            // 最终识别结果，发送到服务器
            console.log('HGDoll: ASR 最终结果:', data.text);
            broadcastToTabs({ type: 'USER_SPEECH', text: data.text });
            sendChatMessage(data.text);
          } else {
            // 中间结果
            broadcastToTabs({ type: 'ASR_PARTIAL', text: data.text });
          }
        }
      } catch (e) {
        console.warn('HGDoll: ASR 代理响应解析失败', e);
      }
    };

    asrWebSocket.onerror = (err) => {
      console.error('HGDoll: ASR 代理连接失败', err);
      broadcastToTabs({ type: 'STATUS_UPDATE', text: '语音识别连接失败，请检查服务器地址' });
    };

    asrWebSocket.onclose = (event) => {
      console.log(`HGDoll: ASR 代理 WebSocket 已关闭 (code=${event.code}, reason=${event.reason})`);
      asrWebSocket = null;
      if (isMicActive) {
        // 录音仍在进行但连接断了，尝试重连
        console.log('HGDoll: ASR 连接断开，3秒后尝试重连...');
        broadcastToTabs({ type: 'STATUS_UPDATE', text: '语音连接断开，正在重连...' });
        setTimeout(() => {
          if (isMicActive) connectAsrViaServer();
        }, 3000);
      }
    };
  } catch (err) {
    console.error('HGDoll: ASR 代理创建失败', err);
    broadcastToTabs({ type: 'STATUS_UPDATE', text: `ASR 连接创建失败: ${err.message}` });
  }
}

/**
 * 发送 ASR 初始化消息（同 Android AsrService.connectWebSocket 中的 onOpen）
 */
function sendAsrInitMessage() {
  const payload = {
    user: { uid: 'HGDOLL_WEB_PLUGIN' },
    audio: {
      format: 'pcm',
      sample_rate: SAMPLE_RATE,
      bits: 16,
      channel: 1,
    },
    request: {
      model_name: 'bigmodel',
      result_type: 'single',
      show_utterances: true,
      end_window_size: 600,
      force_to_speech_time: 1500,
    },
  };

  const payloadBytes = new TextEncoder().encode(JSON.stringify(payload));
  // 简化：不做 gzip 压缩，直接发 JSON（服务端代理模式下）
  if (asrWebSocket && asrWebSocket.readyState === WebSocket.OPEN) {
    asrWebSocket.send(JSON.stringify(payload));
  }
}

/**
 * 处理 ASR 响应
 */
function handleAsrResponse(data) {
  try {
    let text = '';
    let isFinal = false;

    if (typeof data === 'string') {
      const parsed = JSON.parse(data);
      text = parsed.text || parsed.result || '';
      isFinal = parsed.is_final || parsed.definite || false;
    } else if (data instanceof ArrayBuffer) {
      // 二进制协议解析（同 Android 端 parseResponse）
      const view = new DataView(data);
      if (data.byteLength < 4) return;

      const headerByte1 = view.getUint8(1);
      const messageType = (headerByte1 >> 4) & 0x0F;

      if (messageType === 0b1001) {
        // FULL_SERVER_RESPONSE: 解析 payload
        // 跳过 header(4) + sequence(4) 得到 payload_size(4) + payload
        if (data.byteLength > 12) {
          const payloadSize = view.getInt32(8);
          const payloadBytes = new Uint8Array(data, 12, payloadSize);
          // 尝试 gzip 解压或直接解析 JSON
          try {
            const payloadStr = new TextDecoder().decode(payloadBytes);
            const payload = JSON.parse(payloadStr);
            text = payload.result?.[0]?.text || payload.text || '';
            isFinal = payload.result?.[0]?.definite || false;
          } catch {
            // gzip 压缩的数据需要解压，在浏览器中使用 DecompressionStream
            decompressGzip(payloadBytes).then((decompressed) => {
              try {
                const payload = JSON.parse(decompressed);
                text = payload.result?.[0]?.text || payload.text || '';
                isFinal = payload.result?.[0]?.definite || false;
                handleAsrText(text, isFinal);
              } catch (e) {
                console.warn('HGDoll: ASR 解压后解析失败', e);
              }
            });
            return;
          }
        }
      } else if (messageType === 0b1011) {
        // SERVER_ACK
        console.log('HGDoll: ASR 服务端确认');
        return;
      }
    }

    if (text) {
      handleAsrText(text, isFinal);
    }
  } catch (e) {
    console.warn('HGDoll: ASR 响应解析异常', e);
  }
}

function handleAsrText(text, isFinal) {
  if (isFinal && text.trim()) {
    broadcastToTabs({ type: 'USER_SPEECH', text: text });
    sendChatMessage(text);
  } else if (text.trim()) {
    broadcastToTabs({ type: 'ASR_PARTIAL', text: text });
  }
}

/**
 * 使用浏览器原生 DecompressionStream 解压 gzip
 */
async function decompressGzip(compressedData) {
  const ds = new DecompressionStream('gzip');
  const writer = ds.writable.getWriter();
  writer.write(compressedData);
  writer.close();

  const reader = ds.readable.getReader();
  const chunks = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
  }

  const totalLength = chunks.reduce((acc, c) => acc + c.length, 0);
  const result = new Uint8Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return new TextDecoder().decode(result);
}

// 接收来自 content script 的音频数据
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'AUDIO_DATA') {
    // 将 PCM 音频数据发送到 ASR
    if (asrWebSocket && asrWebSocket.readyState === WebSocket.OPEN) {
      // 通过服务端代理模式：直接发送 base64 编码的 PCM 数据
      asrWebSocket.send(JSON.stringify({
        audio_data: message.data,
        sequence: ++asrSequence,
      }));
    } else {
      // WebSocket 未就绪，可能还在连接中
      console.warn('HGDoll: ASR WebSocket 未就绪，音频数据被丢弃, readyState=',
        asrWebSocket ? asrWebSocket.readyState : 'null');
    }
  }
});

// ========== 工具函数 ==========

function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

async function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(
      ['serverIp', 'asrAppId', 'asrAccessToken', 'screenshotInterval'],
      (result) => {
        resolve({
          serverIp: result.serverIp || '',
          asrAppId: result.asrAppId || '',
          asrAccessToken: result.asrAccessToken || '',
          screenshotInterval: result.screenshotInterval || 3,
        });
      }
    );
  });
}

function broadcastToTabs(message) {
  chrome.tabs.query({}, (tabs) => {
    let delivered = 0;
    let failed = 0;
    tabs.forEach((tab) => {
      if (tab.id && tab.url && !tab.url.startsWith('chrome://') && !tab.url.startsWith('chrome-extension://')) {
        chrome.tabs.sendMessage(tab.id, message)
          .then(() => { delivered++; })
          .catch(() => { failed++; });
      }
    });
    // 仅对关键消息类型记录日志
    if (message.type === 'AI_RESPONSE' || message.type === 'PLAY_AUDIO') {
      setTimeout(() => {
        console.log(`HGDoll: broadcastToTabs ${message.type} -> ${delivered} delivered, ${failed} failed, ${tabs.length} total tabs`);
        if (delivered === 0 && tabs.length > 0) {
          console.warn('HGDoll: ⚠ 没有标签页成功接收消息！请确认 content script 已加载（刷新目标页面）');
        }
      }, 500);
    }
  });
}
