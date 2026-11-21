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
| 字幕 | 主持人记录、原句修订和版本引用、sherpa-onnx + SenseVoice 中文转写、Silero VAD 自动端点及标点分句、主动回看与原位阅读 |
| 播报 | Windows 本机中文音色合成 WAV，成员端真实音频队列及播放事件，停止、重播、调速；音频仅在内存缓存 |
| 议题提取 | 服务端自动队列驱动本地 Qwen，显示讨论阶段、摘要、原句依据与耗时；修订时丢弃旧结果，主持人核对后建立议题 |
| 自动留位 | 按成员授权、定案轮次、有效原句版本、队列或主动回看状态联合判断；公共卡按议题聚合 |
| 手动请求 | 等待、澄清、接话；区分已送达、已看到、已处理；本人可以略过或撤回 |
| 意见处理 | 本地 ONNX 语义向量召回，加 Qwen 逐议题判断补充关系，展示引用原句及可能影响的前提；成员确认或改选 |
| 视觉研究 | 本机 MediaPipe 提取特征，记录本人的自然聆听、准备发言、希望等待三类动作示例，连续匹配后给出可忽略的私人建议；关闭时清除样例 |
| 同步与一致性 | SQLite 写事务、命令幂等、决定版本冲突检查、WebSocket 按角色发送最新快照、心跳与过期证据处理 |
| 隐私和导出 | 公共投影字段白名单；原始录音和视频默认不落盘；导出前核对版本、排除个人进度与凭证 |

### 回应流程更新（2026-11-21）

- 相关依据有自然播放结束回执，或本人明确结束回看后，开始约定的最低准备时间（默认 20 秒，本人可设 10—120 秒）。成员也可主动确认关键内容已收到、准备补充或延长时间。到时不自动关闭请求，失联不表示同意。
- 确认决定与答复意见分别记录。主持人逐条选择已采纳、部分采纳、未采纳、进一步核实或已作说明；采纳需写具体影响。成员可确认收到，或说明尚未回答的问题，要求继续回应。多窗口提交校验意见版本，保留历次答复与追问。
- 尚有请求或未答复意见时，确认决定需要先处理，或明确选择说明原因后继续。后者记录为例外，未答复意见继续保留，不再批量标记为已处理。
- 成员端实时讨论显示个人回应进度；主持人工作台优先显示待答复意见；复盘并列显示决定变更前后。公共屏不展示个人计时、设备状态或意见正文。
- 摄像头比较连续窗口中的姿态均值与变化，遮挡或大幅变化时弃判。端侧规则结合正在定案的议题、相关播放/回看、已有请求和提示冷却选择私人建议；忽略建议后延长冷却。此模块仍是规则和个人示例匹配，未训练或验证“介入增益”模型。
- 本人可评价每次自动留位是否有帮助，评价不影响回应资格，不提供给主持人或公共屏，且不进入讨论导出。原始视觉特征仍只在端侧处理。

验证命令：`python -m pytest -q`、`node scripts/check_client_ai.mjs`。启动应用后可运行 `python scripts/check_response_browser.py`（默认测试地址 8789，可用 `TONGXI_URL` 指定）检查真实浏览器的三端流程、追问、手机布局和合成摄像头生命周期；`scripts/check_browser.py` 另外验证本地语音、转写及语义模型。测试使用人工编写样例，不能代替目标用户效果研究。

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

首次准备会将语义、语音、视觉模型与 CPU 运行文件下载到工作区内，已有完整文件会跳过。安装依赖和下载需要网络，日常运行只访问本机及当前局域网。下载失败后重新运行对应脚本即可。脚本先写临时文件，再替换成正式文件，并记录来源与 SHA-256 清单。

本次为了在现有环境直接运行，前端采用原生 ES Modules、HTML 和 CSS，无需 Node 包管理器和构建步骤。源代码均位于 tongxi/web。服务端转写复用 sherpa-onnx 的 SenseVoice 与 Silero VAD，视觉模块与模型均预置本地。详细部署形态与原架构中的转写和 TypeScript 候选方案略有调整。

### 本地 AI 选择

- **语义**优先识别 .runtime/qwen-1.5b.gguf，兼容已有的 qwen.gguf。第一次分析时启动 .runtime/llama/llama-server.exe 独立 CPU 进程，监听随机回环端口，应用正常退出时关闭。预置模型为 Qwen2.5-1.5B-Instruct Q4_K_M。若使用已有 Ollama，可设置 TONGXI_MODEL 为实际本地模型名，服务改用 127.0.0.1:11434。两种入口均绕过代理，避免本机请求意外送到代理服务器。
- **关联检索**使用 .runtime/embedding 中的多语言 MiniLM ONNX 模型，按语义召回候选，再由本地语言模型逐一判断。无法运行语义服务时明确标记词组检索降级。模型判断仍可能出错，提交前保留改选和独立意见入口。
- **转写**复用 sherpa-onnx 1.13.8、SenseVoiceSmall int8 与 Silero VAD，资源位于 .runtime/asr。浏览器持续发送 16 kHz PCM，由官方 VAD 检测停顿，再输出带标点的中文，并按句号、问号、叹号建立独立原句。停止时提交尾帧并等待最后一句；不再按八秒硬切或因排队两段而自动停录。当前属于停顿后出句，不提供逐字临时字幕；快语速、含糊发音和噪声表现仍需实际使用验收。已有其他目录时，TONGXI_ASR_MODEL 须指向包含 model.int8.onnx、tokens.txt、silero_vad.onnx 的目录。详见 [字幕底座选型与接入](docs/字幕底座选型与接入.md)。
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
  analysis.py      服务端自动分析任务、状态通知与旧结果失效
  embeddings.py    本地 ONNX 句向量和候选召回
  adapters.py      本地播报、语义和词组检索
  asr.py           sherpa-onnx 识别与官方 VAD 会话，模型标点分句
  web/
    index.html     本地页面入口
    app.js         三端页面、自动候选、播放器与交互
    style.css      桌面与手机界面
    vision.js      端侧视觉生命周期及特征显示
    vision-policy.js 个人动作样例、弃判与私人提示
    caption-microphone.js  连续 PCM 传输、处理回执与尾帧提交
    pcm-worklet.js 浏览器音频采集处理器
    vendor/        下载的本地视觉资源与来源清单
scripts/
  start.ps1        启动本地／局域网服务
  setup_models.py  准备 Windows CPU 模型资源
  setup_asr.py     下载现成字幕模型、VAD 与上游许可证
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
.\.venv\Scripts\python.exe scripts/check_semantic_ai.py
node scripts/check_client_ai.mjs
# 启动服务后运行，默认使用本机 Chrome 的独立无痕上下文
.\.venv\Scripts\python.exe scripts/check_browser.py
```

结果、界面截图写到 artifacts，未加入版本管理。浏览器检查使用合成摄像头，避免访问真实用户摄像头。模型检查使用本机合成语音，保留真实转写和候选输出。这些结果说明工程链路已接通，参与体验仍需按项目书的 A/B/C 条件开展真实用户试用。

原型的本地小模型会误识别方案字母、时间及其他词语，语义模型也可能提出宽泛或错误的议题。界面因此保留修订原句、手动建项、改选关联与人工确认。首版尚未实现复杂说话人分离、跨房间持久账户和主机重启后的会话恢复。用户理解程度也需本人表达。

## 体验新增 AI 能力

主持人加入后，在概览或实时讨论查看“本地 AI 候选议题”。输入明确的讨论前提，再输入一句准备定案的原话，服务端会自动分析，无需让主持人页面持续发分析请求。可以暂停自动提议，也可主动分析一次。AI 结论采用原句引用，引用版本变化后须重新计算。

成员提交补充前，点击“根据内容查找原议题”。系统先在本机计算语义向量，再比较补充与各项议题，展示候选关系及原句。引用无效、结构不合规或解释与关联标记冲突的结果会被舍弃。语言模型没有确认、但向量召回较接近时，明确标为“可能回应的上下文”，交给本人核对。当前模型的关系标签和解释仍可能错误，含糊指代也可能没有结果。构造案例验证仅覆盖小规模归属回归，仍需积累真实讨论中的误判与漏判。

视觉功能在“个人与设备”中开启。先校准自然坐姿，再分别保持三类动作两秒完成样例记录。示例匹配会在连续几帧一致时给出私人快捷建议，用户可选择请求或忽略。该原型使用个人示例中心匹配，不具有经过真实用户实验验证的普适意图识别准确率。

## 字幕底座与原创范围

语音识别、VAD 和标点输出采用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 的 SenseVoice 与 Silero VAD 接入。本项目重点实现议题依据、授权留位、原句版本与回应处理，字幕识别不作为原创算法贡献。选型、接入方式和已检查的边界见 [说明文档](docs/字幕底座选型与接入.md)。已有环境仅更新字幕组件时，执行 `.\.venv\Scripts\python.exe scripts/setup_asr.py`。
