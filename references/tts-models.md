# TTS 模型自主选择与下载

## 默认与触发条件

默认复用 `<WORKSPACE_ROOT>/.ai/` 中已安装且通过验证的中文 TTS；存在 Qwen3-TTS 1.7B `Uncle_Fu` 时可作为普通话男性声线候选。只有以下情况才寻找新模型：现有权重缺失或持续失败、用户要求另一音色/语言、普通话质量不达标、许可证不允许目标用途。

显存受限时可评估官方 `Qwen3-TTS-12Hz-0.6B-CustomVoice`，质量和语气控制优先时使用 1.7B 版本。0.6B 与 1.7B 的 CustomVoice 接口兼容且更轻，但 0.6B 不支持自然语言 `instruct` 控制。参数量更小不保证实际延迟更低；切换前必须用同一段代表性文案比较生成耗时、成品语速、峰值显存和实际试听，按实时系数而不是模型大小做决定。

需要比 Qwen 更低的实时系数时，可评估官方 CosyVoice2 0.5B 等支持中文的流式模型；其安装和模型权重必须遵循本文件的来源、许可证、工作目录与冒烟测试要求。Kokoro、MeloTTS、Piper 等更轻方案适合预览或低资源设备，但不能在未试听的情况下替代最终普通话旁白。

可信来源按顺序为：模型项目官方 Hugging Face 组织、ModelScope 官方组织、项目官方 GitHub Release。优先考虑 Qwen3-TTS、CosyVoice、Fish Speech、GPT-SoVITS 等维护活跃且支持中文的项目，但最终选择必须基于当时的官方模型卡、许可证和当前硬件实测，不把此列表视为自动批准。

## 选择门槛

- 支持普通话，能稳定输出长文本或可靠分段拼接。
- 模型及推理所需显存适合当前设备；不明确时先选择较小版本、量化版本或 CPU 可运行方案。
- 许可证清晰，并允许用户预期的发布/商业场景；许可证不清晰则不下载。
- 官方权重优先 `safetensors`。涉及 `trust_remote_code=True` 时先阅读仓库相关 Python 文件，不直接执行未知安装脚本。
- 记录模型 ID、版本或 commit、下载 URL、许可证、文件大小和 SHA-256。

## 下载位置与缓存

- 模型：`<WORKSPACE_ROOT>/.ai/tts/models/<provider>/<model-id>`
- 虚拟环境：`<WORKSPACE_ROOT>/.ai/tts/envs/<provider>`
- Hugging Face：`<WORKSPACE_ROOT>/.cache/huggingface`
- ModelScope：`<WORKSPACE_ROOT>/.cache/modelscope`
- Torch：`<WORKSPACE_ROOT>/.cache/torch`
- pip：`<WORKSPACE_ROOT>/.cache/pip`

在同一进程中设置环境变量后再运行官方 CLI 或 Python SDK。不要使用 `WORKSPACE_ROOT` 以外的默认缓存，不执行需要系统权限的全局安装，不把 API key 或访问 token 写入项目文件。

## 下载后验收

1. 检查文件完整性、模型版本和许可证并写入 `models/manifest.jsonl`。
2. 使用 2-3 句标准普通话做冒烟测试，包含数字、英文缩写和长句停顿。
3. 检查是否爆音、吞字、方言化、情绪漂移或段间音色变化，并记录实时系数与显存峰值。
4. 只有通过测试才切换生产声音；失败时保留日志，删除本任务产生的残缺下载，不删除共享的已验证模型。

模型搜索与下载可以和素材下载同时进行，但首次模型冒烟测试完成前不能批量生成整篇旁白。生产过程中模型固定，不在段落之间混用不同模型或声线。
