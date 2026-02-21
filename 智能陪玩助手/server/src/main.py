# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# Licensed under the 【火山方舟】原型应用软件自用许可协议
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     https://www.volcengine.com/docs/82379/1433703
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Video Analyser: Realtime vision and speech analysis
"""

import asyncio
import datetime
import logging
import os
import json
import gzip
import struct
from typing import AsyncIterable, List, Optional, Tuple, Union

import prompt
import utils
from config import LLM_ENDPOINT, VLM_ENDPOINT, TTS_ACCESS_TOKEN, TTS_APP_ID, ASR_APP_ID, ASR_ACCESS_TOKEN

from arkitect.core.component.llm import BaseChatLanguageModel
from arkitect.types.llm.model import (
    ArkChatCompletionChunk,
    ArkChatParameters,
    ArkChatRequest,
    ArkChatResponse,
    ArkMessage,
    Response,
)
from volcenginesdkarkruntime.types.chat.chat_completion_content_part_text_param import (
    ChatCompletionContentPartTextParam,
)
from arkitect.core.component.tts import (
    AudioParams,
    ConnectionParams,
    AsyncTTSClient,
    create_bot_audio_responses,
)
from arkitect.launcher.local.serve import launch_serve
from arkitect.telemetry.trace import task
from arkitect.utils.context import get_headers, get_reqid

FRAME_DESCRIPTION_PREFIX = "视频帧描述："
LAST_HISTORY_MESSAGES = 180  # truncate history messages to 180


def _is_text_part(part) -> bool:
    """Check if a content part is a text part (compatible with dict or pydantic model)."""
    if isinstance(part, dict):
        return part.get("type") == "text"
    return hasattr(part, "text") and hasattr(part, "type")


def _get_text(part) -> str:
    """Get text from a content part (compatible with dict or pydantic model)."""
    if isinstance(part, dict):
        return part.get("text", "")
    return getattr(part, "text", "")

logger = logging.getLogger(__name__)


@task(watch_io=False)
async def get_request_messages_for_llm(
    contexts: utils.Storage,
    context_id: str,
    request: ArkChatRequest,
    prompt: str,
) -> List[ArkMessage]:
    request_messages = await contexts.get_history(context_id)
    if isinstance(request.messages[-1].content, list):
        assert _is_text_part(request.messages[-1].content[0])
        text = _get_text(request.messages[-1].content[0])
    else:
        text = request.messages[-1].content
    request_messages = request_messages + [ArkMessage(role="user", content=text)]
    request_messages = request_messages[-LAST_HISTORY_MESSAGES:]
    return [ArkMessage(role="system", content=prompt)] + request_messages


@task(watch_io=False)
async def chat_with_vlm(
    request: ArkChatRequest,
    parameters: ArkChatParameters,
) -> Tuple[bool, Optional[AsyncIterable[ArkChatCompletionChunk]]]:
    vlm = BaseChatLanguageModel(
        model=VLM_ENDPOINT,
        messages=[ArkMessage(role="system", content=prompt.VLM_CHAT_PROMPT)]
        + [request.messages[-1]],
        parameters=parameters,
    )

    iterator = vlm.astream()
    message = ""
    first_resp = await iterator.__anext__()
    if first_resp.choices and first_resp.choices[0].delta.content != "":
        message += first_resp.choices[0].delta.content
    second_resp = await iterator.__anext__()
    if second_resp.choices and second_resp.choices[0].delta.content != "":
        message += second_resp.choices[0].delta.content
    print("message：", message)
    if message.startswith("不知道"):
        return False, None
    async def stream_vlm_outputs():
        yield first_resp
        yield second_resp
        async for resp in iterator:
            yield resp

    return True, stream_vlm_outputs()


@task(watch_io=False)
async def llm_answer(
    contexts, context_id, request, parameters: ArkChatParameters
) -> Tuple[bool, Optional[AsyncIterable[ArkChatCompletionChunk]]]:
    request_messages = await get_request_messages_for_llm(
        contexts, context_id, request, prompt.LLM_PROMPT
    )
    llm = BaseChatLanguageModel(
        model=LLM_ENDPOINT,
        messages=request_messages,
        parameters=parameters,
    )

    iterator = llm.astream()
    first_resp = await iterator.__anext__()

    async def stream_llm_outputs():
        yield first_resp
        async for resp in iterator:
            yield resp

    return True, stream_llm_outputs()


@task(watch_io=False)
async def chat_with_llm(
    contexts: utils.Storage,
    request: ArkChatRequest,
    parameters: ArkChatParameters,
    context_id: str,
) -> Tuple[bool, Optional[AsyncIterable[ArkChatCompletionChunk]]]:
    response_task = asyncio.create_task(
        llm_answer(contexts, context_id, request, parameters)
    )
    logger.info("llm can respond")
    return await response_task


@task(watch_io=False)
async def chat_with_branches(
    contexts: utils.Storage,
    request: ArkChatRequest,
    parameters: ArkChatParameters,
    context_id: str,
) -> AsyncIterable[Union[ArkChatCompletionChunk, ArkChatResponse]]:

    llm_task = asyncio.create_task(
        chat_with_llm(contexts, request, parameters, context_id)
    )

    can_response, llm_iter = await llm_task
    # print(f"type I got from llm: {type(llm_iter)}")
    return llm_iter


@task(watch_io=False)
async def summarize_image(
    contexts: utils.Storage,
    request: ArkChatRequest,
    parameters: ArkChatParameters,
    context_id: str,
):
    """
    Summarize the image and append the summary to the context.
    """
    request_messages = [
        ArkMessage(role="system", content=prompt.VLM_PROMPT)
    ] + request.messages
    vlm = BaseChatLanguageModel(
        model=VLM_ENDPOINT,
        messages=request_messages,
        parameters=parameters,
    )
    resp = await vlm.arun()
    message = resp.choices[0].message.content
    print("图片分析结果：", message)
    message = FRAME_DESCRIPTION_PREFIX + message
    await contexts.append(context_id, ArkMessage(role="assistant", content=message))


async def _save_context(contexts, context_id, user_text, bot_message):
    """在异步任务中保存上下文历史，避免在 async generator 的 post-yield 代码中丢失"""
    try:
        print(f"[Chat] context_id={context_id} 回复内容: {bot_message[:100]}{'...' if len(bot_message) > 100 else ''}")
        await contexts.append(context_id, ArkMessage(role="user", content=user_text))
        await contexts.append(context_id, ArkMessage(role="assistant", content=bot_message))
        print(f"[Chat] context_id={context_id} 上下文已保存 (user + assistant)")
    except Exception as e:
        logger.error(f"[Chat] 保存上下文失败: {e}")


@task(watch_io=False)
async def default_model_calling(
    request: ArkChatRequest,
) -> AsyncIterable[Union[ArkChatCompletionChunk, ArkChatResponse]]:
    # local in-memory storage should be changed to other storage in production
    context_id: Optional[str] = get_headers().get("X-Context-Id", None)
    print("context_id：", context_id)
    assert context_id is not None
    contexts: utils.Storage = utils.CoroutineSafeMap.get_instance_sync()
    if not await contexts.contains(context_id):
        await contexts.set(context_id, utils.Context())

    # If a list is passed and the first text is empty
    # Use VLM to summarize the image asynchronously and return immediately
    is_image = (
        isinstance(request.messages[-1].content, list)
        and _is_text_part(request.messages[-1].content[0])
        and _get_text(request.messages[-1].content[0]) == ""
    )
    print("is_image", is_image)
    parameters = ArkChatParameters(**request.__dict__)
    if is_image:
        _ = asyncio.create_task(
            summarize_image(contexts, request, parameters, context_id)
        )
        return

    # Extract user text BEFORE the yields (post-yield code may not run in async generators)
    user_text = ""
    if isinstance(request.messages[-1].content, list) and _is_text_part(
        request.messages[-1].content[0]
    ):
        user_text = _get_text(request.messages[-1].content[0])
    elif isinstance(request.messages[-1].content, str):
        user_text = request.messages[-1].content

    # Initialize TTS connection asynchronously before launching LLM request to reduce latency
    tts_client = None
    tts_init_ok = False
    try:
        tts_client = AsyncTTSClient(
            connection_params=ConnectionParams(
                speaker="zh_female_meilinvyou_emo_v2_mars_bigtts",
                audio_params=AudioParams(
                    format="mp3",
                    sample_rate=24000,
                ),
            ),
            access_key=TTS_ACCESS_TOKEN,
            app_key=TTS_APP_ID,
            conn_id=get_reqid(),
            log_id=get_reqid(),
        )
        connection_task = asyncio.create_task(tts_client.init())
    except Exception as tts_init_err:
        logger.error(f"初始化 TTS 客户端失败: {tts_init_err}")
        connection_task = None

    # Use LLM and VLM to answer user's question
    print(f"[Chat] context_id={context_id} 开始 LLM 请求...")
    try:
        response_iter = await chat_with_branches(contexts, request, parameters, context_id)
    except Exception as llm_err:
        logger.error(f"[Chat] LLM 请求失败: {llm_err}")
        if tts_client:
            try:
                await tts_client.close()
            except Exception:
                pass
        raise

    # Wait for TTS connection
    if connection_task:
        try:
            await connection_task
            tts_init_ok = True
        except Exception as tts_conn_err:
            logger.error(f"[Chat] TTS 连接失败: {tts_conn_err}，将返回纯文本响应")

    # Use mutable list to collect message during yields
    message_parts = []

    try:
        if tts_init_ok and tts_client:
            # Normal path: TTS + audio response
            try:
                tts_stream_output = tts_client.tts(response_iter, stream=request.stream)
                async for resp in create_bot_audio_responses(tts_stream_output, request):
                    if isinstance(resp, ArkChatCompletionChunk):
                        if len(resp.choices) > 0 and hasattr(resp.choices[0].delta, "audio"):
                            message_parts.append(resp.choices[0].delta.audio.get("transcript", ""))
                    else:
                        if len(resp.choices) > 0 and resp.choices[0].message.audio:
                            message_parts.append(resp.choices[0].message.audio.transcript)
                    yield resp
            except Exception as tts_err:
                logger.error(f"[Chat] TTS 处理异常: {tts_err}，尝试回退到纯文本")
                async for resp in response_iter:
                    if isinstance(resp, ArkChatCompletionChunk):
                        if resp.choices and resp.choices[0].delta.content:
                            message_parts.append(resp.choices[0].delta.content)
                    yield resp
            finally:
                try:
                    await tts_client.close()
                except Exception:
                    pass
        else:
            # Fallback: no TTS, return text-only LLM response
            logger.warning("[Chat] TTS 不可用，返回纯文本响应")
            async for resp in response_iter:
                if isinstance(resp, ArkChatCompletionChunk):
                    if resp.choices and resp.choices[0].delta.content:
                        message_parts.append(resp.choices[0].delta.content)
                yield resp
            if tts_client:
                try:
                    await tts_client.close()
                except Exception:
                    pass
    finally:
        # CRITICAL: Use asyncio.ensure_future in finally block to reliably save context
        # This runs even when the async generator is closed via aclose() by the framework
        bot_message = "".join(message_parts)
        if bot_message or user_text:
            asyncio.ensure_future(_save_context(contexts, context_id, user_text, bot_message))


@task(watch_io=False)
async def main(request: ArkChatRequest) -> AsyncIterable[Response]:
    async for resp in default_model_calling(request):
        yield resp


# ========== Web Plugin 适配：CORS 中间件 + WebSocket ASR 代理 ==========

def setup_web_plugin(app):
    """为 FastAPI 应用添加 Web 插件支持（CORS + WebSocket ASR 代理 + 调试端点）"""
    from fastapi import WebSocket as FastAPIWebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    import websockets
    import websockets.exceptions
    import base64

    # 添加 CORS 中间件，允许浏览器插件跨域请求
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境应限制为具体域名
        allow_credentials=False,  # 修复: credentials + wildcard origin 不兼容
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    @app.get("/debug/status")
    async def debug_status():
        """调试端点：检查服务状态和上下文信息"""
        contexts = utils.CoroutineSafeMap.get_instance_sync()
        keys = await contexts.keys()
        context_info = {}
        for key in keys:
            history = await contexts.get_history(key)
            context_info[key] = {
                "history_length": len(history),
                "last_messages": [
                    {"role": m.role, "content": (m.content[:80] + '...') if isinstance(m.content, str) and len(m.content) > 80 else m.content}
                    for m in history[-3:]
                ] if history else []
            }
        return JSONResponse({
            "status": "running",
            "active_contexts": len(keys),
            "contexts": context_info,
        })

    @app.websocket("/ws/asr")
    async def asr_proxy(websocket: FastAPIWebSocket, app_id: str = "", access_token: str = ""):
        """
        WebSocket ASR 代理端点
        浏览器插件 → 本服务器 → Doubao ASR 服务
        解决浏览器无法直接携带自定义Header连接ASR WebSocket的问题
        """
        await websocket.accept()

        # 优先使用 query 参数，为空则回退到 config.py 配置
        effective_app_id = app_id or ASR_APP_ID
        effective_access_token = access_token or ASR_ACCESS_TOKEN
        logger.info(f"ASR proxy: 浏览器客户端已连接, app_id={effective_app_id}")

        if not effective_app_id or not effective_access_token:
            logger.error("ASR proxy: ASR 凭证缺失，请配置 ASR_APP_ID 和 ASR_ACCESS_TOKEN")
            await websocket.send_json({"error": "ASR 凭证未配置"})
            return

        import uuid
        ASR_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
        asr_ws = None
        forward_task = None

        try:
            # 连接到 Doubao ASR 服务（携带认证 Header）
            # X-Api-Connect-Id 必须是 UUID 格式（参考 Android 端 AsrService.kt）
            connect_id = str(uuid.uuid4())
            extra_headers = {
                "X-Api-App-Key": effective_app_id,
                "X-Api-Access-Key": effective_access_token,
                "X-Api-Resource-Id": "volc.bigasr.sauc.duration",
                "X-Api-Connect-Id": connect_id,
            }
            logger.info(f"ASR proxy: 连接 Doubao ASR, connect_id={connect_id}")
            try:
                asr_ws = await asyncio.wait_for(
                    websockets.connect(ASR_URL, additional_headers=extra_headers),
                    timeout=10,
                )
            except (asyncio.TimeoutError, Exception) as conn_err:
                logger.error(f"ASR proxy: 连接 Doubao ASR 服务失败: {conn_err}")
                await websocket.send_json({"error": "无法连接 ASR 服务", "detail": str(conn_err)})
                return
            logger.info("ASR proxy: 已连接到 Doubao ASR 服务")

            # 发送 ASR 初始化消息
            init_payload = {
                "user": {"uid": "HGDOLL_WEB_PLUGIN"},
                "audio": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "bits": 16,
                    "channel": 1,
                },
                "request": {
                    "model_name": "bigmodel",
                    "result_type": "single",
                    "show_utterances": True,
                    "end_window_size": 600,
                    "force_to_speech_time": 1500,
                },
            }

            # 构建二进制协议消息
            payload_bytes = gzip.compress(json.dumps(init_payload).encode())
            header = bytes([
                (0x01 << 4) | 0x01,  # version | header_size
                (0x01 << 4) | 0x01,  # FULL_CLIENT_REQUEST | POS_SEQUENCE
                (0x01 << 4) | 0x01,  # JSON | GZIP
                0x00,                # reserved
            ])
            seq_bytes = struct.pack(">I", 1)
            size_bytes = struct.pack(">I", len(payload_bytes))
            init_msg = header + seq_bytes + size_bytes + payload_bytes
            await asr_ws.send(init_msg)
            logger.info("ASR proxy: 初始化消息已发送")

            sequence = 1

            async def forward_asr_to_browser():
                """将 ASR 服务的响应转发给浏览器"""
                try:
                    async for msg in asr_ws:
                        if isinstance(msg, bytes):
                            # 解析二进制协议响应
                            result = parse_asr_response(msg)
                            if result:
                                await websocket.send_json(result)
                        elif isinstance(msg, str):
                            await websocket.send_text(msg)
                except (websockets.exceptions.ConnectionClosed, KeyError) as cc:
                    # KeyError can occur in websockets 16.x when ASR server uses
                    # non-standard close codes with binary protocol
                    logger.info(f"ASR proxy: ASR 上游连接已关闭 ({type(cc).__name__}: {cc})")
                except asyncio.CancelledError:
                    pass  # 正常取消，不需要日志
                except Exception as e:
                    logger.warning(f"ASR proxy: 转发响应异常: {type(e).__name__}: {e}")

            # 启动 ASR→浏览器 的转发任务
            forward_task = asyncio.create_task(forward_asr_to_browser())

            # 浏览器→ASR 的转发循环
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning(f"ASR proxy: 收到无效 JSON 消息")
                    continue

                if "audio_data" in msg:
                    # 将 base64 PCM 数据转为二进制发送到 ASR
                    audio_bytes = base64.b64decode(msg["audio_data"])
                    sequence = msg.get("sequence", sequence + 1)

                    # 构建音频数据包（AUDIO_ONLY_REQUEST）
                    audio_header = bytes([
                        (0x01 << 4) | 0x01,  # version | header_size
                        (0x02 << 4) | 0x01,  # AUDIO_ONLY_REQUEST | POS_SEQUENCE
                        (0x00 << 4) | 0x00,  # NO_SERIAL | NO_COMPRESS (0, not 2!)
                        0x00,                # reserved
                    ])
                    seq_bytes = struct.pack(">I", sequence)
                    audio_size = struct.pack(">I", len(audio_bytes))
                    audio_msg = audio_header + seq_bytes + audio_size + audio_bytes
                    try:
                        await asr_ws.send(audio_msg)
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("ASR proxy: ASR 上游已断开，无法发送音频")
                        await websocket.send_json({"error": "ASR 连接已断开"})
                        break

        except WebSocketDisconnect:
            logger.info("ASR proxy: 浏览器客户端断开")
        except Exception as e:
            logger.error(f"ASR proxy: 异常: {type(e).__name__}: {e}")
        finally:
            # 取消转发任务
            if forward_task and not forward_task.done():
                forward_task.cancel()
                try:
                    await forward_task
                except asyncio.CancelledError:
                    pass
            if asr_ws:
                try:
                    await asr_ws.close()
                except Exception:
                    pass
            logger.info("ASR proxy: 连接已清理")


def parse_asr_response(data: bytes) -> dict:
    """解析 Doubao ASR 二进制协议响应"""
    if len(data) < 4:
        return None

    header_byte1 = data[1]
    message_type = (header_byte1 >> 4) & 0x0F

    if message_type == 0b1001:  # FULL_SERVER_RESPONSE
        if len(data) > 12:
            payload_size = struct.unpack(">I", data[8:12])[0]
            payload_bytes = data[12:12 + payload_size]
            try:
                # 尝试 gzip 解压
                decompressed = gzip.decompress(payload_bytes)
                payload = json.loads(decompressed)
            except Exception:
                try:
                    payload = json.loads(payload_bytes)
                except Exception:
                    return None

            # 提取识别结果
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


if __name__ == "__main__":
    port = os.getenv("_FAAS_RUNTIME_PORT")
    run_port = int(port) if port else 8888

    # 使用 BotServer 创建 app 并添加 Web 插件支持
    from arkitect.launcher.local.serve import (
        BotServer, load_function, get_runner, get_endpoint_config,
        get_default_client_configs, setup_tracing,
        set_resource_type, set_resource_id, set_account_id,
    )

    set_resource_type(os.getenv("RESOURCE_TYPE") or "")
    set_resource_id(os.getenv("RESOURCE_ID") or "")
    set_account_id(os.getenv("ACCOUNT_ID") or "")
    setup_tracing(endpoint=os.getenv("TRACE_ENDPOINT"), trace_on=False)

    runnable_func = load_function("main", "main")
    endpoint_path = "/api/v3/bots/chat/completions"

    server = BotServer(
        runner=get_runner(runnable_func),
        health_check_path="/v1/ping",
        endpoint_config=get_endpoint_config(endpoint_path, runnable_func),
        clients=get_default_client_configs(),
    )
    setup_web_plugin(server.app)

    import uvicorn
    import socket

    def is_port_available(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return True
            except OSError:
                return False

    if not is_port_available(run_port):
        logger.warning(f"端口 {run_port} 已被占用，尝试释放...")
        # 尝试查找可用端口或提示用户
        for alt_port in range(run_port + 1, run_port + 10):
            if is_port_available(alt_port):
                logger.warning(f"使用替代端口: {alt_port}")
                run_port = alt_port
                break
        else:
            logger.error(f"端口 {run_port}-{run_port+9} 均不可用，请手动释放端口")
            import sys
            sys.exit(1)

    print(f"服务启动在 http://0.0.0.0:{run_port}")
    uvicorn.run(server.app, host="0.0.0.0", port=run_port)