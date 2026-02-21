# 配置指南：需要用户自行补充的内容

在运行项目之前，你需要准备好以下凭证并在相应位置填写。API Key 的申请方法请参考 [key.md](key.md)。

---

## 1. 后端服务（server）

### 环境变量

启动服务前需要设置以下环境变量。可以直接 `export`，也可以在 `server/` 目录下创建 `.env` 文件。

| 环境变量 | 说明 | 获取方式 |
|----------|------|----------|
| `ARK_API_KEY` | 火山方舟 API Key | [参考文档](https://www.volcengine.com/docs/82379/1298459#api-key-%E7%AD%BE%E5%90%8D%E9%89%B4%E6%9D%83) |
| `VLM_ENDPOINT` | Doubao-1.5-Vision-Pro-32K 的 Endpoint ID | [创建推理接入点](https://www.volcengine.com/docs/82379/1099522#594199f1) |
| `LLM_ENDPOINT` | Doubao-1.5-Pro-32K 的 Endpoint ID | [创建推理接入点](https://www.volcengine.com/docs/82379/1099522#594199f1) |
| `TTS_APP_ID` | 语音合成应用的 App ID | 见下方"语音技术凭证" |
| `TTS_ACCESS_TOKEN` | 语音合成应用的 Access Token | 见下方"语音技术凭证" |
| `ASR_APP_ID` | 流式语音识别应用的 App ID（与 TTS 同应用时可共用） | 见下方"语音技术凭证" |
| `ASR_ACCESS_TOKEN` | 流式语音识别应用的 Access Token（与 TTS 同应用时可共用） | 见下方"语音技术凭证" |

### 涉及文件

| 文件 | 说明 |
|------|------|
| `server/src/config.py` | 所有凭证通过 `os.environ.get()` 读取，默认值为占位符 |
| `server/run.sh` | 启动脚本，需在运行前设置环境变量 |

### 示例

```bash
# 方式一：直接 export
export ARK_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export VLM_ENDPOINT=ep-xxxxxxxxxxxx-xxxxx
export LLM_ENDPOINT=ep-xxxxxxxxxxxx-xxxxx
export TTS_APP_ID=xxxxxxxxxx
export TTS_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export ASR_APP_ID=xxxxxxxxxx
export ASR_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 方式二：创建 server/.env 文件（已被 .gitignore 忽略）
# ARK_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# VLM_ENDPOINT=ep-xxxxxxxxxxxx-xxxxx
# ...
```

---

## 2. Android 客户端

在 App 启动后的配置界面中填写：

| 配置项 | 说明 |
|--------|------|
| Server IP | 后端服务的地址（如 `http://192.168.1.100:8888`） |
| ASR App ID | 语音识别 App ID |
| ASR Access Token | 语音识别 Access Token |

### 涉及文件

| 文件 | 说明 |
|------|------|
| `android/local.properties` | Android SDK 路径，需改为你本机的 SDK 目录 |

---

## 3. Web 浏览器插件

在插件弹出窗口（popup）中填写：

| 配置项 | 说明 |
|--------|------|
| Server IP | 后端服务的地址 |
| ASR App ID | 语音识别 App ID |
| ASR Access Token | 语音识别 Access Token |
| 截图间隔 | 截屏频率（秒） |

---

## 语音技术凭证获取步骤

1. [完成企业认证](https://console.volcengine.com/user/authentication/detail/)
2. [开通语音技术产品](https://console.volcengine.com/speech/app)
3. [创建应用](https://console.volcengine.com/speech/app)，同时勾选 **大模型语音合成** 和 **流式语音识别大模型**
4. 在应用详情页获取 **App ID** 和 **Access Token**

详细步骤及截图请参考 [key.md](key.md)。
