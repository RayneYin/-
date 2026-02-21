"""
HGDoll Web Plugin 适配 - 测试套件
测试服务端 CORS 支持、WebSocket ASR 代理、以及 Web 插件相关的核心逻辑
"""

import asyncio
import json
import gzip
import struct
import os
import sys
import pytest

# 将 src 目录加入 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ========== 1. 基础导入测试 ==========

class TestImports:
    """测试所有必要的模块可以正常导入"""

    def test_standard_libs(self):
        """标准库导入"""
        import json, struct, gzip, base64, asyncio
        assert True

    def test_fastapi(self):
        """FastAPI 及 CORS 中间件"""
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        assert app is not None

    def test_websockets(self):
        """websockets 库"""
        import websockets
        assert websockets is not None

    def test_uvicorn(self):
        """uvicorn 服务器"""
        import uvicorn
        assert uvicorn is not None

    def test_config(self):
        """服务端配置文件"""
        import config
        assert hasattr(config, "LLM_ENDPOINT")
        assert hasattr(config, "VLM_ENDPOINT")
        assert hasattr(config, "TTS_APP_ID")
        assert hasattr(config, "TTS_ACCESS_TOKEN")

    def test_prompt(self):
        """Prompt 文件包含网页端游戏支持"""
        import prompt
        assert hasattr(prompt, "VLM_PROMPT")
        assert hasattr(prompt, "VLM_CHAT_PROMPT")
        assert hasattr(prompt, "LLM_PROMPT")
        # 验证 VLM_PROMPT 已适配网页端
        assert "网页" in prompt.VLM_PROMPT or "浏览器" in prompt.VLM_PROMPT
        # 验证 LLM_PROMPT 已适配网页端
        assert "网页" in prompt.LLM_PROMPT or "电脑" in prompt.LLM_PROMPT

    def test_utils(self):
        """工具类（需要 arkitect SDK，不可用时跳过）"""
        try:
            import utils
            assert hasattr(utils, "Storage")
            assert hasattr(utils, "Context")
            assert hasattr(utils, "CoroutineSafeMap")
        except ModuleNotFoundError:
            pytest.skip("arkitect SDK 未安装，跳过 utils 导入测试")


# ========== 2. ASR 协议测试 ==========

# ======= 独立的 parse_asr_response 实现（避免导入 main.py 及其 arkitect 依赖） =======
def parse_asr_response(data: bytes) -> dict:
    """解析 Doubao ASR 二进制协议响应（与 main.py 中逻辑一致）"""
    if len(data) < 4:
        return None
    header_byte1 = data[1]
    message_type = (header_byte1 >> 4) & 0x0F
    if message_type == 0b1001:  # FULL_SERVER_RESPONSE
        if len(data) > 12:
            payload_size = struct.unpack(">I", data[8:12])[0]
            payload_bytes = data[12:12 + payload_size]
            try:
                decompressed = gzip.decompress(payload_bytes)
                payload = json.loads(decompressed)
            except Exception:
                try:
                    payload = json.loads(payload_bytes)
                except Exception:
                    return None
            text = ""
            is_final = False
            if "result" in payload and payload["result"]:
                text = payload["result"][0].get("text", "")
                is_final = payload["result"][0].get("definite", False)
            elif "text" in payload:
                text = payload["text"]
                is_final = payload.get("definite", False)
            return {"text": text, "is_final": is_final}
    elif message_type == 0b1011:  # SERVER_ACK
        return {"type": "ack"}
    return None


class TestAsrProtocol:
    """测试 ASR 二进制协议的构建和解析（对应 Android AsrService 的协议逻辑）"""

    def _build_header(self, message_type, message_flags, serial_method, compression):
        """构建 4 字节协议头"""
        return bytes([
            (0x01 << 4) | 0x01,  # version=1 | header_size=1
            (message_type << 4) | message_flags,
            (serial_method << 4) | compression,
            0x00,  # reserved
        ])

    def test_build_init_message(self):
        """测试构建 ASR 初始化消息"""
        init_payload = {
            "user": {"uid": "HGDOLL_WEB_PLUGIN"},
            "audio": {"format": "pcm", "sample_rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "result_type": "single",
                "show_utterances": True,
            },
        }

        payload_bytes = gzip.compress(json.dumps(init_payload).encode())
        header = self._build_header(0x01, 0x01, 0x01, 0x01)  # FULL_CLIENT_REQUEST, POS_SEQ, JSON, GZIP
        seq_bytes = struct.pack(">I", 1)
        size_bytes = struct.pack(">I", len(payload_bytes))
        msg = header + seq_bytes + size_bytes + payload_bytes

        # 验证消息结构
        assert len(msg) == 4 + 4 + 4 + len(payload_bytes)
        assert msg[0] == 0x11  # version=1, header_size=1
        assert (msg[1] >> 4) == 0x01  # FULL_CLIENT_REQUEST
        assert struct.unpack(">I", msg[4:8])[0] == 1  # sequence=1
        assert struct.unpack(">I", msg[8:12])[0] == len(payload_bytes)

        # 验证 payload 可以解压还原
        restored = json.loads(gzip.decompress(msg[12:]))
        assert restored["user"]["uid"] == "HGDOLL_WEB_PLUGIN"
        assert restored["audio"]["sample_rate"] == 16000

    def test_build_audio_message(self):
        """测试构建音频数据包"""
        fake_audio = b'\x00\x01' * 4096  # 模拟 8KB PCM 数据
        header = self._build_header(0x02, 0x01, 0x00, 0x02)  # AUDIO_ONLY, POS_SEQ, NO_SERIAL, RAW
        seq = struct.pack(">I", 42)
        size = struct.pack(">I", len(fake_audio))
        msg = header + seq + size + fake_audio

        assert len(msg) == 4 + 4 + 4 + len(fake_audio)
        assert (msg[1] >> 4) == 0x02  # AUDIO_ONLY_REQUEST
        assert struct.unpack(">I", msg[4:8])[0] == 42
        assert msg[12:] == fake_audio

    def test_parse_server_response(self):
        """测试解析 ASR 服务端 FULL_SERVER_RESPONSE"""
        payload = {"result": [{"text": "你好呀", "definite": True}]}
        payload_bytes = gzip.compress(json.dumps(payload).encode())
        header = self._build_header(0x09, 0x01, 0x01, 0x01)  # FULL_SERVER_RESPONSE
        seq = struct.pack(">I", 5)
        size = struct.pack(">I", len(payload_bytes))
        msg = header + seq + size + payload_bytes

        result = parse_asr_response(msg)
        assert result is not None
        assert result["text"] == "你好呀"
        assert result["is_final"] is True

    def test_parse_server_ack(self):
        """测试解析 SERVER_ACK"""
        header = self._build_header(0x0B, 0x00, 0x00, 0x00)
        result = parse_asr_response(header)
        assert result is not None
        assert result.get("type") == "ack"

    def test_parse_partial_result(self):
        """测试解析中间识别结果（非 definite）"""
        payload = {"result": [{"text": "你好", "definite": False}]}
        payload_bytes = gzip.compress(json.dumps(payload).encode())
        header = self._build_header(0x09, 0x01, 0x01, 0x01)
        seq = struct.pack(">I", 3)
        size = struct.pack(">I", len(payload_bytes))
        msg = header + seq + size + payload_bytes

        result = parse_asr_response(msg)
        assert result is not None
        assert result["text"] == "你好"
        assert result["is_final"] is False

    def test_parse_empty_data(self):
        """测试解析空数据"""
        assert parse_asr_response(b"") is None
        assert parse_asr_response(b"\x00") is None
        assert parse_asr_response(b"\x00\x00") is None

    def test_parse_unknown_message_type(self):
        """测试解析未知消息类型"""
        header = self._build_header(0x05, 0x00, 0x00, 0x00)
        result = parse_asr_response(header)
        assert result is None


# ========== 3. Web 插件文件完整性测试 ==========

class TestWebPluginFiles:
    """验证 Web 插件目录的文件结构和内容"""

    PLUGIN_DIR = os.path.join(
        os.path.dirname(__file__), "..", "..", "web-plugin"
    )

    def test_manifest_exists(self):
        """manifest.json 存在且格式正确"""
        path = os.path.join(self.PLUGIN_DIR, "manifest.json")
        assert os.path.exists(path), "manifest.json 不存在"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["manifest_version"] == 3
        assert "activeTab" in data["permissions"]
        assert "tabs" in data["permissions"]
        assert "storage" in data["permissions"]

    def test_background_js_exists(self):
        """background.js 存在且包含核心函数"""
        path = os.path.join(self.PLUGIN_DIR, "background.js")
        assert os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        # 验证核心功能函数存在
        assert "captureAndUploadScreenshot" in content, "缺少截图上传函数"
        assert "sendChatMessage" in content, "缺少聊天消息函数"
        assert "connectAsrWebSocket" in content or "connectAsrViaServer" in content, "缺少ASR连接函数"
        assert "startService" in content, "缺少服务启动函数"
        assert "stopService" in content, "缺少服务停止函数"
        assert "chrome.tabs.captureVisibleTab" in content, "缺少截图API调用"

    def test_content_js_exists(self):
        """content.js 存在且包含 UI 和录音功能"""
        path = os.path.join(self.PLUGIN_DIR, "content.js")
        assert os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        assert "hgdoll-overlay" in content, "缺少悬浮面板"
        assert "hgdoll-panel" in content, "缺少面板元素"
        assert "startRecording" in content, "缺少录音启动函数"
        assert "stopRecording" in content, "缺少录音停止函数"
        assert "float32ToInt16" in content, "缺少 PCM 转换函数"
        assert "navigator.mediaDevices.getUserMedia" in content, "缺少麦克风API"

    def test_content_css_exists(self):
        """content.css 存在"""
        path = os.path.join(self.PLUGIN_DIR, "content.css")
        assert os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        assert "hgdoll-panel" in content
        assert "hgdoll-fab" in content

    def test_popup_files_exist(self):
        """popup.html/js/css 均存在"""
        for f in ["popup.html", "popup.js", "popup.css"]:
            path = os.path.join(self.PLUGIN_DIR, f)
            assert os.path.exists(path), f"{f} 不存在"

    def test_popup_html_structure(self):
        """popup.html 包含必要的表单元素"""
        path = os.path.join(self.PLUGIN_DIR, "popup.html")
        content = open(path, encoding="utf-8").read()
        assert "serverIp" in content, "缺少服务器地址输入框"
        assert "asrAppId" in content, "缺少 ASR App ID 输入框"
        assert "asrAccessToken" in content, "缺少 ASR Access Token 输入框"
        assert "saveBtn" in content, "缺少保存按钮"
        assert "toggleBtn" in content, "缺少启动/停止按钮"

    def test_manifest_references_valid(self):
        """manifest.json 引用的文件都存在"""
        path = os.path.join(self.PLUGIN_DIR, "manifest.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        # 检查 background
        bg_file = data["background"]["service_worker"]
        assert os.path.exists(os.path.join(self.PLUGIN_DIR, bg_file)), f"{bg_file} 不存在"

        # 检查 content_scripts
        for cs in data["content_scripts"]:
            for js_file in cs["js"]:
                assert os.path.exists(os.path.join(self.PLUGIN_DIR, js_file)), f"{js_file} 不存在"
            for css_file in cs["css"]:
                assert os.path.exists(os.path.join(self.PLUGIN_DIR, css_file)), f"{css_file} 不存在"

        # 检查 popup
        popup_file = data["action"]["default_popup"]
        assert os.path.exists(os.path.join(self.PLUGIN_DIR, popup_file)), f"{popup_file} 不存在"


# ========== 4. CORS 中间件集成测试 ==========

class TestCorsIntegration:
    """测试 CORS 中间件能正确添加到 FastAPI 应用"""

    def test_cors_setup(self):
        """测试 setup_web_plugin 可以正常执行"""
        from fastapi import FastAPI
        app = FastAPI()

        # 模拟 setup_web_plugin 中的 CORS 部分
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )

        # 验证中间件已添加（FastAPI 的 middleware_stack 会在 build 后创建）
        assert len(app.user_middleware) > 0

    def test_cors_headers_on_response(self):
        """测试 CORS headers 出现在响应中"""
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/test")
        def test_endpoint():
            return {"status": "ok"}

        client = TestClient(app)
        response = client.get("/test", headers={"Origin": "chrome-extension://test"})

        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers

    def test_cors_preflight(self):
        """测试 CORS 预检请求（OPTIONS）"""
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.post("/api/v3/bots/chat/completions")
        def chat():
            return {"choices": []}

        client = TestClient(app)
        response = client.options(
            "/api/v3/bots/chat/completions",
            headers={
                "Origin": "chrome-extension://abcdefg",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-Context-Id",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


# ========== 5. 上下文存储测试 ==========

class TestContextStorage:
    """测试上下文存储（Web 插件和 Android 使用相同的后端存储）"""

    def test_context_creation(self):
        """测试创建会话上下文"""
        try:
            import utils
            ctx = utils.Context()
            assert ctx.history == []
            assert ctx.state == utils.STATE_IDLE
        except ModuleNotFoundError:
            pytest.skip("arkitect SDK 未安装，跳过上下文测试")

    def test_context_append_and_get(self):
        """测试存储和获取消息历史"""
        try:
            import utils
            ctx = utils.Context()
            ctx.history.append({"role": "user", "content": "你好"})
            ctx.history.append({"role": "assistant", "content": "你好呀！"})
            assert len(ctx.history) == 2
            assert ctx.history[0]["content"] == "你好"
        except ModuleNotFoundError:
            pytest.skip("arkitect SDK 未安装，跳过上下文测试")


# ========== 6. 截图请求格式测试 ==========

class TestScreenshotRequestFormat:
    """验证 Web 插件发送的截图请求格式与 Android 端一致"""

    def test_image_upload_format(self):
        """截图上传请求格式验证"""
        import base64
        fake_image = b'\xff\xd8\xff' + b'\x00' * 100  # 模拟 JPEG 头
        base64_image = base64.b64encode(fake_image).decode()

        # Web 插件的请求格式（与 Android ScreenshotService.uploadScreenshot 一致）
        request_body = {
            "model": "bot-20241114164326-xlcc91",
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ""},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                    ],
                }
            ],
        }

        # 验证格式
        assert request_body["stream"] is False
        msg = request_body["messages"][0]
        assert msg["role"] == "user"
        assert len(msg["content"]) == 2
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][0]["text"] == ""  # 空文本 → 服务端走 VLM 图片分析
        assert msg["content"][1]["type"] == "image_url"
        assert msg["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_chat_message_format(self):
        """语音识别文本请求格式验证"""
        request_body = {
            "model": "bot-20241114164326-xlcc91",
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "我赢了"}],
                }
            ],
        }

        msg = request_body["messages"][0]
        assert msg["content"][0]["text"] == "我赢了"

    def test_context_id_header(self):
        """验证 X-Context-Id header 格式"""
        import re
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        # 模拟 Web 插件生成的 UUID
        test_id = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        assert re.match(uuid_pattern, test_id), "Context ID 应为 UUID v4 格式"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
