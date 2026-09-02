# TTS 模型自主选择与下载

## 默认与触发条件

默认使用已安装的 Qwen3-TTS 1.7B `Uncle_Fu`。只有以下情况才寻找新模型：现有权重缺失或持续失败、用户要求另一音色/语言、普通话质量不达标、许可证不允许目标用途。

可信来源按顺序为：模型项目官方 Hugging Face 组织、ModelScope 官方组织、项目官方 GitHub Release。优先考虑 Qwen3-TTS、CosyVoice、Fish Speech、GPT-SoVITS 等维护活跃且支持中文的项目，但最终选择必须基于当时的官方模型卡、许可证和 RTX 5070 12GB 实测，不把此列表视为自动批准。

## 选择门槛

- 支持普通话，能稳定输出长文本或可靠分段拼接。
- 模型及推理所需显存适合 12GB；不明确时先选择较小版本或量化版本。
- 许可证清晰，并允许用户预期的发布/商业场景；许可证不清晰则不下载。
- 官方权重优先 `safetensors`。涉及 `trust_remote_code=True` 时先阅读仓库相关 Python 文件，不直接执行未知安装脚本。
- 记录模型 ID、版本或 commit、下载 URL、许可证、文件大小和 SHA-256。

## 下载位置与缓存

- 模型：`H:\短剧\.ai\tts\models\<provider>\<model-id>`
- 虚拟环境：`H:\短剧\.ai\tts\envs\<provider>`
- Hugging Face：`H:\短剧\.cache\huggingface`
- ModelScope：`H:\短剧\.cache\modelscope`
- Torch：`H:\短剧\.cache\torch`
- pip：`H:\短剧\.cache\pip`

在同一 PowerShell 进程中设置环境变量后再运行官方 CLI 或 Python SDK。不要使用默认缓存位置，不执行写入 C 盘的全局安装，不把 API key 或访问 token 写入项目文件。

## 下载后验收

1. 检查文件完整性、模型版本和许可证并写入 `models/manifest.jsonl`。
2. 使用 2-3 句标准普通话做冒烟测试，包含数字、英文缩写和长句停顿。
3. 检查是否爆音、吞字、方言化、情绪漂移或段间音色变化，并记录实时系数与显存峰值。
4. 只有通过测试才切换生产声音；失败时保留日志，删除本任务产生的残缺下载，不删除共享的已验证模型。

模型搜索与下载可以和素材下载同时进行，但首次模型冒烟测试完成前不能批量生成整篇旁白。生产过程中模型固定，不在段落之间混用不同模型或声线。
