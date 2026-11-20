# 同席 · 本地协作应用

Same‑Seat: An inclusive decision‑making assistant for university group deliberations. It creates non‑blocking response windows before decisions are finalized, bridging gaps between discussion pace and individual information‑receiving rhythm.

这份代码基于《同席 V1.2 比赛版策划书》，包含主持人工作台、成员端、公共屏，以及相应的房间与状态服务。默认使用本机服务和临时 SQLite 数据库，服务重启后房间清空。

## 立即打开

在已完成环境准备的电脑上，双击 **[启动同席.cmd](./启动同席.cmd)**，然后访问 **http://127.0.0.1:8765**。当前开发电脑已完成准备。首次克隆仓库后，请先按下方“从源码建立环境”安装依赖及下载模型。首次使用摄像头或麦克风仍需浏览器授权。

首页可以创建空白讨论或体验示例。示例仅填入三句明确标注为人工编写的字幕和一个议题，真实的邀请、授权、自动留位和处理规则照常运行。

### 推荐体验顺序

1. 创建示例讨论，点击“邀请与投屏”。分别打开公共屏、两个成员入会页，成员填写各自昵称。每位参与者使用独立标签页或设备。
2. 成员在“我的授权”里打开“主动回看相关原句时替我留位”，进入“实时讨论”，对第 2 句点“回看这句”。
3. 主持人在方案 B 下点“准备定案”，核对原句后确认。公共屏显示 B 待回应，成员无需再按请求按钮。
4. 成员写“周六实验室设备借不到，建议改到周日”，可查找原议题、选择对应项或作为独立意见提交。
5. 主持人在回应工作台查看补充，修改决定并记录理由。成员端和公共屏同步显示结果。独立意见也有单独回复入口。
6. 也可以用真实本地播报代替回看测试。开启播报滞后留位授权，在播放相关原句或其仍在真实队列时准备定案。
7. 在“讨论复盘”预览后导出 JSON。结束房间会清空本场数据。

## 已实现的功能

| 能力 | 当前实现 |
|---|---|
| 房间和身份 | 限时邀请、主持／成员／公共屏独立凭证，凭证放 URL 片段并立即清除；会话仅保留在对应标签页的 sessionStorage |
| 完整前端 | 首页、概览、实时字幕、议题与决定、回应工作台、复盘、个人与设备；手机适配、键盘操作、表单校验、错误与重连提示 |
| 字幕 | 主持人记录、原句修订和版本引用、本地麦克风分段转写、主动回看与原位阅读 |
| 播报 | Windows 本机中文音色合成 WAV，成员端真实音频队列及播放事件，停止、重播、调速；音频仅在内存缓存 |
| 议题提取 | 本地 Qwen + llama.cpp，按稳定字幕变化自动提出候选，可暂停；JSON schema 约束与服务端证据复核；主持人核对后建立议题 |
| 自动留位 | 按成员授权、定案轮次、有效原句版本、队列或主动回看状态联合判断；公共卡按议题聚合 |
| 手动请求 | 等待、澄清、接话；区分已送达、已看到、已处理；本人可以略过或撤回 |
| 意见处理 | 本地中文词组检索推荐原议题，本人改选；主持人修改、维持、推迟、重开、回复独立意见 |
| 视觉研究 | 本机 MediaPipe 面部与姿态模型，校准相对肩宽变化和口部开合；私人显示、关闭后停止轨道并清理缓存 |
| 同步与一致性 | SQLite 写事务、命令幂等、决定版本冲突检查、WebSocket 按角色发送最新快照、心跳与过期证据处理 |
| 隐私和导出 | 公共投影字段白名单；原始录音和视频默认不落盘；导出前核对版本、排除个人进度与凭证 |

摄像头实现遵循 V1.2 的 P1 范围。肩宽变化是机位相关的视觉特征，尚需目标用户试验才能验证其与发言意图的关系。当前视觉结果只用于私人研究和提示。

## 从源码建立环境

需要 Windows、CPython 3.12 或更新版本。避免误用 MSYS2 的 Python。在项目根目录执行，以下示例中的 Python 路径可换成实际 CPython 路径。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ai.txt
.\.venv\Scripts\python.exe scripts/setup_models.py
.\.venv\Scripts\python.exe scripts/setup_vision.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1
```

首次准备会下载约 1 GB 的模型和 CPU 运行文件到工作区内，已有完整文件会跳过。安装依赖和下载需要网络，日常运行只访问本机及当前局域网。下载失败后重新运行对应脚本即可。脚本先写临时文件，再替换成正式文件，并记录来源与 SHA-256 清单。

本次为了在现有环境直接运行，前端采用原生 ES Modules、HTML 和 CSS，无需 Node 包管理器和构建步骤。源代码均位于 tongxi/web。服务端转写使用 faster-whisper 的分段接口，视觉模块与模型均预置本地。详细部署形态与原架构中的转写和 TypeScript 候选方案略有调整。

### 本地 AI 选择

- **语义**默认自动识别 .runtime/qwen.gguf 和 .runtime/llama/llama-server.exe。第一次提取议题时启动独立 CPU 进程，监听随机回环端口，应用正常退出时一并关闭。预置模型是 Qwen2.5-0.5B-Instruct Q4_K_M，输出必须经过主持人核对。若希望用已有的 Ollama 模型，在启动前设置 TONGXI_MODEL 为实际本地模型名，服务将改用 127.0.0.1:11434。
- **转写**优先使用 .runtime/whisper-small，也可将 TONGXI_ASR_MODEL 设置为另一个已经下载的 faster-whisper 模型目录。麦克风每约六秒上传一个完整录音容器，由本机解码转写。转写延迟包括采集窗和推理时间，仍需实测抢话、噪声、时间和否定词。
- **中文播报**使用 Windows 的中文 System.Speech 音色。本机已确认有 Microsoft Huihui Desktop。其他 Windows 主机如缺失中文音色，需先通过系统设置安装。成员设备只需能够播放 WAV。
- **视觉**依赖 assets/vendor 的本地 WASM 与 task 文件。浏览器拒绝摄像头时仍能使用全部 P0 功能。

## 局域网体验

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -Lan
```

主持人仍在主机的 127.0.0.1 地址创建房间，然后在邀请弹窗中将分享地址改为主机实际的局域网地址。防火墙应只允许预期的私有网络访问此端口。默认启动仅监听回环地址。

手机使用局域网 HTTP 可进行字幕、意见和公共屏协作；浏览器通常要求 HTTPS 安全上下文才能访问摄像头和麦克风。主机的 localhost 体验可直接授权。需要跨设备使用这些硬件能力时，使用受信任证书，例如通过 uvicorn 的 --ssl-keyfile 和 --ssl-certfile 参数启动，并在客户端验证证书信任。当前启动脚本默认采用本机 HTTP。

## 代码结构

```text
tongxi/
  main.py          HTTP、WebSocket、静态页面与角色鉴权
  models.py        输入字段和状态枚举
  store.py         内存 SQLite、事务与幂等结果、连接代次
  domain.py        授权、原句修订、留位、请求与决定状态机
  adapters.py      本地语音、转写、语义和词组检索
  web/
    index.html     本地页面入口
    app.js         三端页面、自动候选、播放器与交互
    style.css      桌面与手机界面
    vision.js      端侧视觉生命周期及特征显示
    vendor/        下载的本地视觉资源与来源清单
scripts/
  start.ps1        启动本地／局域网服务
  setup_models.py  准备 Windows CPU 模型资源
  setup_vision.py  准备浏览器视觉资源
  check_browser.py 四端交互、手机与媒体模块浏览器检查
  check_models.py  合成中文音频和真实本地推理检查
tests/
  test_rooms.py    核心状态、修订、权限与一致性回归测试
```

接口文档位于 **http://127.0.0.1:8765/docs**。写请求通过 Idempotency-Key 去重，房间私有接口使用 Bearer 会话。WebSocket 接收的第一条消息传会话，后续按连接维持心跳。当前实现使用角色过滤后的完整快照同步，重连重新取得一致状态，尚未实现设计稿中的增量事件重放协议。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_models.py
# 启动服务后运行，默认使用本机 Chrome 的独立无痕上下文
.\.venv\Scripts\python.exe scripts/check_browser.py
```

结果、界面截图写到 artifacts，未加入版本管理。浏览器检查使用合成摄像头，避免访问真实用户摄像头。模型检查使用本机合成语音，保留真实转写和候选输出。这些结果说明工程链路已接通，参与体验仍需按项目书的 A/B/C 条件开展真实用户试用。

原型的本地小模型会误识别方案字母、时间及其他词语，语义模型也可能提出宽泛或错误的议题。界面因此保留修订原句、手动建项、改选关联与人工确认。首版尚未实现复杂说话人分离、跨房间持久账户和主机重启后的会话恢复。用户理解程度也需本人表达。
