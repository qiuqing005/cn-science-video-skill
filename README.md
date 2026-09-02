# cn-science-video

一个用于制作中文科普视频的 Codex Skill。用户提交完整剧本后，Skill 会并行生成普通话解说、检索并下载高关联公共素材，再通过 HyperFrames 完成字幕、动态图解、渲染和成片验收。

适合人类、生物、医学、天文和自然科学等科普内容。不用于产品宣传、网站录制或仅为已有视频添加字幕。

## 主要能力

- 将中文剧本拆分为可执行的镜头和素材任务。
- 并行运行 TTS 与公共素材检索，减少串行等待时间。
- Qwen3-TTS 支持多段批处理，并按显存自动降级批大小。
- 默认使用沉稳、自然、语气统一的普通话男性配音。
- 优先复用用户工作区中已有的合格 TTS；必要时从可信官方来源寻找并验证其他中文模型。
- 自动生成中英文素材关键词，检索科学机构和许可明确的公共素材库。
- 内置 Wikimedia 素材解析器会并发检索并优先下载适合 1080p 成片的缩略版本。
- 记录素材来源、作者、许可证、本地路径和文件哈希。
- 缺少合适实拍素材时，使用原创动态图解或数据可视化补位。
- 使用 HyperFrames 进行字幕、动画、渲染和画面检查。
- 使用两遍响度测量归一化，避免单遍处理偏离目标响度。
- 支持 NVIDIA NVENC，并保留 CPU 编码回退方案。
- 按 CPU/内存自动选择渲染 worker，并用实际编码冒烟测试决定是否启用 NVENC。
- 对实拍友好主题强制动态视频覆盖，并检查场景构图签名、跨场素材重复和许可完整性。
- 保留 30fps 默认档，并提供经过 A/B 验证的 24fps `--fast-render` 快速档。
- 素材、字体、音频或 vendor 文件变化会准确使下游缓存失效。
- 下载、素材清单、渲染结果和性能报告采用临时文件成功后原子替换。
- 严格组合检查与最终媒体检查使用成功标记和 MP4 哈希安全复用。
- 快速模式会前置 1.2 倍速、子集化中文字体并复用时间线与验收框架，同时保留每个主题独立的场景构图。

## 安装

```powershell
npx skills add qiuqing005/cn-science-video-skill
```

安装后可显式调用：

```text
使用 $cn-science-video，把下面的剧本制作成中文科普视频：

《标题》
这里粘贴完整剧本……
```

也可以直接提出中文科普视频制作需求，Codex 会根据 Skill 描述自动匹配。

## 默认制作参数

| 项目 | 默认值 |
| --- | --- |
| 画幅 | 1920x1080 横屏 |
| 帧率 | 30fps |
| 配音 | 普通话成年男性 |
| 语气 | 沉稳、自然、克制、段落一致 |
| 默认声线 | 工作区内质量合格的普通话男性声线 |
| 成片速度 | 1.2 倍速，保持音高 |
| 音频响度 | 约 -16 LUFS |
| 视频编码 | 优先 H.264 NVENC |

用户明确指定的画幅、声音、语速和视觉风格会覆盖这些默认值。

## 并行工作流

剧本完成分段后，Skill 会同时启动三条工作流：

1. `voice`：按段生成 TTS、响度归一化、词级时间戳和音频元数据。
2. `assets`：扩展中英文关键词、检索公共素材、核查许可、下载并转码。
3. 主流程：搭建 storyboard、原创图解和 HyperFrames 工程。

合流时以真实音频时长更新镜头时间线，并把已经下载的本地素材绑定到对应段落。素材缺失时自动切换到预先设计的原创图解方案。

## 公共素材来源

Skill 优先寻找来源和许可明确的内容，例如：

- NASA、NOAA、USGS、NIH/NIAID 等科学机构素材
- Wikimedia Commons
- Openverse 收录的原始来源
- Pexels、Pixabay、Mixkit 等免版税素材站
- Internet Archive 和开放馆藏

每项素材仍需单独核对使用条款。普通搜索引擎图片、社交媒体和许可不明的视频不会被直接用于成片。

## 文件结构

```text
cn-science-video-skill/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── qwen_batch_tts.py
│   ├── align_captions.py
│   ├── download_commons_assets.py
│   ├── run_fast_pipeline.py
│   └── subset_font.py
├── tests/
│   └── test_workflow.py
└── references/
    ├── workflow.md
    ├── fast-pipeline.md
    ├── performance.md
    ├── public-media.md
    └── tts-models.md
```

详细执行规范见：

- [标准制作流程](references/workflow.md)
- [一键快速工作流](references/fast-pipeline.md)
- [快速模式与性能策略](references/performance.md)
- [公共素材检索与许可记录](references/public-media.md)
- [TTS 模型选择与下载](references/tts-models.md)

快速模式提供 `scripts/qwen_batch_tts.py`、`scripts/align_captions.py` 与 `scripts/subset_font.py`，分别用于批量生成音频、按真实语音时间对齐原始字幕和缩小中文字体嵌入体积。

项目输入准备完成后，`scripts/run_fast_pipeline.py` 可一键执行并行媒体处理、字幕对齐、HyperFrames 检查、渲染和最终验收，并自动复用仍然有效的阶段输出。
