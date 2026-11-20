# 第三方资源

运行时准备脚本保留下载来源和 SHA-256。部署或分发时请连同各项目的许可证、模型许可和必要声明保存。

| 资源 | 本项目用途 | 官方来源与许可入口 |
|---|---|---|
| FastAPI、Uvicorn、Pydantic | 本地 API 和输入验证 | https://github.com/fastapi/fastapi ，https://github.com/encode/uvicorn ，https://github.com/pydantic/pydantic |
| faster-whisper、CTranslate2、PyAV | 本地普通话转写和音频解码 | https://github.com/SYSTRAN/faster-whisper ，https://github.com/OpenNMT/CTranslate2 ，https://github.com/PyAV-Org/PyAV |
| faster-whisper-small | 本地语音模型 | https://huggingface.co/Systran/faster-whisper-small |
| llama.cpp b12549 CPU 运行文件 | 本机语义模型服务 | https://github.com/ggml-org/llama.cpp ，MIT |
| Qwen2.5-1.5B-Instruct-GGUF | 本地议题与意见关系分析 | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF ，Apache-2.0 |
| 多语言 MiniLM L12 v2 与 ONNX 量化版 | 本地语义向量召回 | https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 ，https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2 ，Apache-2.0 |
| MediaPipe Tasks Vision 与关键点模型 | 可选端侧视觉研究 | https://github.com/google-ai-edge/mediapipe ，https://developers.google.com/edge/mediapipe/solutions/vision ，SDK Apache-2.0，模型以相应发布页为准 |
| Playwright、pytest | 开发验证 | https://github.com/microsoft/playwright-python ，https://github.com/pytest-dev/pytest |

Windows 中文音色由所在操作系统提供。本项目不打包或再分发系统语音资源。
