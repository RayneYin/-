"""Test streaming chat and ASR WebSocket proxy"""
import requests
import json
import asyncio

def test_streaming_chat():
    print("=== Test 1: Streaming chat ===")
    payload = {
        "model": "bot-20241114164326-xlcc91",
        "stream": True,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "你好"}]}],
    }
    headers = {
        "Content-Type": "application/json",
        "X-Context-Id": "test-stream-002",
    }
    try:
        r = requests.post(
            "http://localhost:8888/api/v3/bots/chat/completions",
            json=payload, headers=headers, timeout=30, stream=True
        )
        print(f"Status: {r.status_code}")
        ct = r.headers.get("content-type", "")
        print(f"Content-Type: {ct}")

        reply_text = ""
        audio_chunks = 0
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            json_str = line[5:].strip()
            if json_str == "[DONE]":
                continue
            try:
                chunk = json.loads(json_str)
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        reply_text += delta["content"]
                    audio = delta.get("audio")
                    if audio:
                        if audio.get("transcript"):
                            reply_text += audio["transcript"]
                        if audio.get("data"):
                            audio_chunks += 1
            except json.JSONDecodeError:
                pass

        print(f"Reply text: {reply_text[:100]}")
        print(f"Audio chunks: {audio_chunks}")
        print("PASS" if reply_text else "FAIL: no text")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")


def test_asr_websocket():
    print("\n=== Test 2: ASR WebSocket proxy ===")
    try:
        import websockets

        async def _test():
            url = "ws://localhost:8888/ws/asr?app_id=YOUR_APP_ID&access_token=YOUR_ACCESS_TOKEN"
            async with websockets.connect(url, open_timeout=10) as ws:
                print("WebSocket connected: PASS")
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                    print(f"Got response: {msg[:100] if isinstance(msg, str) else 'binary'}")
                except asyncio.TimeoutError:
                    print("No response in 3s (normal - waiting for audio): PASS")

        asyncio.run(_test())
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")


def test_debug_status():
    print("\n=== Test 3: Debug status ===")
    try:
        r = requests.get("http://localhost:8888/debug/status", timeout=5)
        data = r.json()
        print(f"Active contexts: {data['active_contexts']}")
        for k, v in data["contexts"].items():
            print(f"  {k}: {v['history_length']} messages")
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")


if __name__ == "__main__":
    test_streaming_chat()
    test_asr_websocket()
    test_debug_status()
