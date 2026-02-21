import os

# Doubao-1.5-vision-pro-32k ENDPOINT_ID
VLM_ENDPOINT = os.environ.get("VLM_ENDPOINT", "your-vlm-endpoint-id")
# Doubao-1.5-pro-32k ENDPOINT_ID
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "your-llm-endpoint-id")

TTS_APP_ID = os.environ.get("TTS_APP_ID", "your-tts-app-id")
TTS_ACCESS_TOKEN = os.environ.get("TTS_ACCESS_TOKEN", "your-tts-access-token")

# ASR (流式语音识别) 凭证 —— 与 TTS 同一个应用时可共用
ASR_APP_ID = os.environ.get("ASR_APP_ID", "your-asr-app-id")
ASR_ACCESS_TOKEN = os.environ.get("ASR_ACCESS_TOKEN", "your-asr-access-token")
