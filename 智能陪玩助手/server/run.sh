#!/bin/bash
# HGDoll Server 启动脚本
# 支持 Android 客户端 和 Web 插件（已内置 CORS + WebSocket ASR 代理）

# 请设置以下环境变量（或在 .env 文件中配置）
# export ARK_API_KEY=your-ark-api-key
# export VLM_ENDPOINT=your-vlm-endpoint-id
# export LLM_ENDPOINT=your-llm-endpoint-id
# export TTS_APP_ID=your-tts-app-id
# export TTS_ACCESS_TOKEN=your-tts-access-token
# export ASR_APP_ID=your-asr-app-id
# export ASR_ACCESS_TOKEN=your-asr-access-token

if [ -z "$ARK_API_KEY" ]; then
  echo "Error: ARK_API_KEY is not set. Please export it before running."
  exit 1
fi

python src/main.py