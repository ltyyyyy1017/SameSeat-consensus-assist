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
| 字幕 | 主持人记录、原句修订和版本引用、FunASR Runtime 连续转写、临时字幕与句末稳定字幕、主动回看与原位阅读 |
| 播报 | Windows 本机中文音色合成 WAV，成员端真实音频队列及播放事件，停止、重播、调速；音频仅在内存缓存 |
| 议题提取 | 服务端自动队列驱动本地 Qwen，显示讨论阶段、摘要、原句依据与耗时；修订时丢弃旧结果，主持人核对后建立议题 |
| 自动留位 | 按成员授权、定案轮次、有效原句版本、队列或主动回看状态联合判断；公共卡按议题聚合 |
| 手动请求 | 等待、澄清、接话；区分已送达、已看到、已处理；本人可以略过或撤回 |
| 意见处理 | 本地 ONNX 语义向量召回，加 Qwen 逐议题判断补充关系，展示引用原句及可能影响的前提；成员确认或改选 |
| 表达信号 | 本机摄像头经 MediaPipe 提取面部与上身特征，对照个人坐姿和动作样例，显示可能准备发言、希望补充、需要等待或状态不确定。摄像头授权与提醒授权分开。信号只形成不写姓名的私密提醒；主持人确认后，才为发出信号的成员打开当前议题的回应入口 |
| 同步与一致性 | SQLite 写事务、命令幂等、决定版本冲突检查、WebSocket 按角色发送最新快照、心跳与过期证据处理 |
| 隐私和导出 | 公共投影字段白名单；原始录音和视频默认不落盘；导出前核对版本、排除个人进度与凭证 |

当前统一说明见《同席_V1.4_参赛交付版项目书.md》。V1.2 文档保留为历史方案。摄像头特征可以在本机显示为表达信号；它还不是经过目标用户试验验证的发言意愿识别。画面不确定时系统保持不确定，不自动公开介入。

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

本次为了在现有环境直接运行，前端采用原生 ES Modules、HTML 和 CSS，无需 Node 包管理器和构建步骤。源代码均位于 tongxi/web。讨论转写的现行路径是本机 FunASR Runtime 2-pass。faster-whisper 仍留在代码中供既有模型检查脚本调用，会议页面不再使用它。视觉模型预置在本地。

### 本地 AI 选择

- **语义**优先识别 .runtime/qwen-1.5b.gguf，兼容已有的 qwen.gguf。第一次分析时启动 .runtime/llama/llama-server.exe 独立 CPU 进程，监听随机回环端口，应用正常退出时关闭。预置模型为 Qwen2.5-1.5B-Instruct Q4_K_M。若使用已有 Ollama，可设置 TONGXI_MODEL 为实际本地模型名，服务改用 127.0.0.1:11434。两种入口均绕过代理，避免本机请求意外送到代理服务器。
- **关联检索**使用 .runtime/embedding 中的多语言 MiniLM ONNX 模型，按语义召回候选，再由本地语言模型逐一判断。无法运行语义服务时明确标记词组检索降级。模型判断仍可能出错，提交前保留改选和独立意见入口。
- **转写**的会议页使用 FunASR Runtime。浏览器把 16 kHz PCM 连续送到本服务的 `/asr`，再转发到 `127.0.0.1:10095` 的官方 2-pass WebSocket。`2pass-online` 只更新同一条临时字幕，不进入 Qwen。`2pass-offline` 在原句上替换为稳定字幕，版本号保持为 1，然后才进入原有稳定字幕分析。主持人的“修订”才会增加版本。启动脚本发现 10095 未监听时，会尝试使用仓库上一级的 `03_语音评测\ASR接入测试`。该目录不在本仓库内。也可以用 `TONGXI_FUNASR_ROOT`、`TONGXI_FUNASR_PYTHON`、`TONGXI_FUNASR_SERVER`、`TONGXI_FUNASR_CACHE` 指定本机路径。仓库同时保留 SenseVoice 字幕流：`tongxi/asr.py`、`scripts/setup_asr.py` 和 `/caption-stream`。运行 `scripts/setup_asr.py` 后，该接口可以按句写入原句；会议页目前不走这条路径。
- **中文播报**使用 Windows 的中文 System.Speech 音色。本机已确认有 Microsoft Huihui Desktop。其他 Windows 主机如缺失中文音色，需先通过系统设置安装。成员设备只需能够播放 WAV。
- **视觉**依赖 assets/vendor 的本地 WASM 与 task 文件。参与者单独授权后，画面只在浏览器内处理。个人端显示相对自然坐姿的嘴部、前倾、俯仰和转向变化。达到持续条件后，主持人只看到一句不写姓名的提醒，确认或忽略后才进入回应。公共屏只显示有没有待回应需求，不显示身份和画面。浏览器拒绝摄像头时，房间、字幕、手动请求和人工结项仍可使用。

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
  adapters.py      FunASR 健康检查、本地语音、语义和词组检索
  asr.py           保留的 SenseVoice 与 Silero VAD 字幕组件
  asr_bridge.py    2pass-online 与 2pass-offline 的字幕组装
  web/
    index.html     本地页面入口
    app.js         三端页面、表达信号、播放器与交互
    style.css      桌面与手机界面
    meeting-theme.css 会议页补充样式
    vision.js      端侧摄像头、个人基准、表达信号
    vision-policy.js 个人动作样例与弃判
    microphone.js  会议页使用的 FunASR 连续 PCM
    caption-microphone.js 保留的 SenseVoice 收音模块
    response-flow.js 回应窗口与意见卡片
    intervention-policy.js 自动留位的页面策略
    pcm-worklet.js 浏览器音频采集处理器
    vendor/        下载的本地视觉资源与来源清单
scripts/
  start.ps1        启动本地／局域网服务
  setup_models.py  准备 Windows CPU 模型资源
  setup_asr.py     准备保留的 SenseVoice 字幕组件
  setup_vision.py  准备浏览器视觉资源
  check_browser.py 四端交互、手机与媒体模块浏览器检查
  check_models.py  合成中文音频和真实本地推理检查
docs/
  字幕底座选型与接入.md  SenseVoice 选型记录，保留在仓库中
tests/
  test_rooms.py    核心状态、修订、权限与一致性回归测试
  test_response_flow.py 回应窗口、意见答复与导出范围测试
  test_captions.py SenseVoice 字幕流的权限与原句测试
  test_sprint.py   临时字幕、回应入口与 FunASR 示例句测试
  test_notices.py  主持人确认、忽略与匿名提醒测试
  test_phase101.py 当前议题、本人回应与连接失效测试
```

接口文档位于 **http://127.0.0.1:8765/docs**。写请求通过 Idempotency-Key 去重，房间私有接口使用 Bearer 会话。WebSocket 接收的第一条消息传会话，后续按连接维持心跳。当前实现使用角色过滤后的完整快照同步，重连重新取得一致状态，尚未实现设计稿中的增量事件重放协议。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_models.py
.\.venv\Scripts\python.exe scripts/check_semantic_ai.py
node scripts/check_client_ai.mjs
# 若本机没有安装在 Program Files 下的 Chrome，先安装配套的无头 Chromium
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
# 启动服务后运行，脚本在独立浏览器上下文内验证
.\.venv\Scripts\python.exe scripts/check_browser.py
```

结果和界面截图写到 `artifacts/`，未加入版本管理。`scripts/check_browser.py` 使用合成摄像头，只检查页面能否打开设备，不作为表达信号识别证据。参与体验仍需要目标用户在真实讨论中验证。

原型的本地小模型会误识别方案字母、时间及其他词语，语义模型也可能提出宽泛或错误的议题。界面因此保留修订原句、手动建项、改选关联与人工确认。首版尚未实现复杂说话人分离、跨房间持久账户和主机重启后的会话恢复。用户理解程度也需本人表达。

若开启转写后没有字幕，先确认本机 10095 端口的 FunASR 已启动，并核对浏览器的输入设备。说完一句话后，临时字幕会先出现，句末由离线结果替换。只有替换后的稳定字幕进入议题分析。核对否定词、方案名和日期，必要时在“实时讨论”点“修订”保存原话，再准备定案。

## 体验新增 AI 能力

主持人加入后，在概览或实时讨论查看“本地 AI 候选议题”。输入明确的讨论前提，再输入一句准备定案的原话，服务端会自动分析，无需让主持人页面持续发分析请求。可以暂停自动提议，也可主动分析一次。AI 结论采用原句引用，引用版本变化后须重新计算。

成员提交补充前，点击“根据内容查找原议题”。系统先在本机计算语义向量，再比较补充与各项议题，展示候选关系及原句。引用无效、结构不合规或解释与关联标记冲突的结果会被舍弃。语言模型没有确认、但向量召回较接近时，明确标为“可能回应的上下文”，交给本人核对。当前模型的关系标签和解释仍可能错误，含糊指代也可能没有结果。构造案例验证仅覆盖小规模归属回归，仍需积累真实讨论中的误判与漏判。

表达信号在“个人与设备”中开启。先单独勾选摄像头分析，再校准自然坐姿，并记录自然聆听、准备发言和希望等待。样例未齐时，只有持续的嘴部变化会形成发言候选，单独前倾不会触发。信号只生成一条不写姓名的私密提醒，主持人确认后，才给发出信号的成员打开当前议题的回应入口。主持人也可以忽略。没有明确的当前议题时不产生提醒。这个原型还没有经过目标用户试验，不能把关键点提取写成已经准确识别发言意愿。
