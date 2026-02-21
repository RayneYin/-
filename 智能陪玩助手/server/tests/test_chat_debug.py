"""Quick integration test for chat endpoint and context saving."""
import requests
import json
import time
import sys


BASE = "http://localhost:8888"
CTX_ID = f"test-debug-{int(time.time())}"


def test_chat():
    print("=== Test 1: Chat Request ===")
    body = {
        "model": "bot-20241114164326-xlcc91",
        "stream": False,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "你好呀，我是测试用户"}]}
        ],
    }
    t0 = time.time()
    r = requests.post(
        f"{BASE}/api/v3/bots/chat/completions",
        json=body,
        headers={"X-Context-Id": CTX_ID},
        timeout=30,
    )
    elapsed = time.time() - t0
    print(f"  Status: {r.status_code} ({elapsed:.1f}s)")

    data = r.json()
    if data.get("choices"):
        msg = data["choices"][0].get("message", {})
        audio = msg.get("audio") or {}
        transcript = audio.get("transcript", "")
        has_audio = bool(audio.get("data"))
        content = msg.get("content")
        print(f"  transcript: {transcript[:150]}")
        print(f"  has_audio: {has_audio}")
        print(f"  content: {content}")
        if not transcript and not content:
            print("  WARNING: No text reply!")
            return False
        return True
    else:
        print(f"  ERROR: {json.dumps(data, ensure_ascii=False)[:300]}")
        return False


def test_context_saved():
    print("\n=== Test 2: Context Saved ===")
    time.sleep(2)  # Wait for async context save
    r = requests.get(f"{BASE}/debug/status", timeout=5)
    data = r.json()
    print(f"  Active contexts: {data.get('active_contexts')}")
    ctx = data.get("contexts", {}).get(CTX_ID, {})
    history_len = ctx.get("history_length", 0)
    print(f"  Context {CTX_ID}: history_length={history_len}")
    for m in ctx.get("last_messages", []):
        print(f"    {m['role']}: {repr(m['content'])[:100]}")
    if history_len >= 2:
        print("  OK: Context properly saved (user + assistant)")
        return True
    else:
        print("  WARNING: Context not saved properly!")
        return False


def test_multi_turn():
    print("\n=== Test 3: Multi-turn Chat ===")
    body = {
        "model": "bot-20241114164326-xlcc91",
        "stream": False,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "你还记得我刚才说了什么吗？"}]}
        ],
    }
    t0 = time.time()
    r = requests.post(
        f"{BASE}/api/v3/bots/chat/completions",
        json=body,
        headers={"X-Context-Id": CTX_ID},
        timeout=30,
    )
    elapsed = time.time() - t0
    print(f"  Status: {r.status_code} ({elapsed:.1f}s)")

    data = r.json()
    if data.get("choices"):
        msg = data["choices"][0].get("message", {})
        audio = msg.get("audio") or {}
        transcript = audio.get("transcript", "")
        content = msg.get("content")
        reply = transcript or content or ""
        print(f"  Reply: {reply[:200]}")
        return True
    else:
        print(f"  ERROR: {json.dumps(data, ensure_ascii=False)[:300]}")
        return False


def test_cors():
    print("\n=== Test 4: CORS Headers ===")
    r = requests.options(
        f"{BASE}/api/v3/bots/chat/completions",
        headers={
            "Origin": "chrome-extension://test-id",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-context-id",
        },
        timeout=5,
    )
    print(f"  OPTIONS Status: {r.status_code}")
    allow_origin = r.headers.get("access-control-allow-origin", "not set")
    allow_headers = r.headers.get("access-control-allow-headers", "not set")
    print(f"  Allow-Origin: {allow_origin}")
    print(f"  Allow-Headers: {allow_headers}")
    return r.status_code == 200


if __name__ == "__main__":
    # Check server is running
    try:
        requests.get(f"{BASE}/v1/ping", timeout=3)
    except Exception as e:
        print(f"Server not running at {BASE}: {e}")
        sys.exit(1)

    results = []
    results.append(("Chat", test_chat()))
    results.append(("Context", test_context_saved()))
    results.append(("Multi-turn", test_multi_turn()))
    results.append(("CORS", test_cors()))

    print("\n" + "=" * 40)
    print("RESULTS:")
    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    sys.exit(0 if all_pass else 1)
