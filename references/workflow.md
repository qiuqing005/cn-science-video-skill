# 中文科普视频标准工作流

## 0. 快速预检

确定 `WORKSPACE_ROOT`：优先使用用户明确分配的目录，未指定时使用当前任务的工作区根目录。解析为绝对路径并做实际写入/删除探针；目录不可写时不要尝试管理员权限，改用用户分配的其他可写目录。然后在其中创建主题化项目目录，例如 `<WORKSPACE_ROOT>/sleep-science-explainer`。检查：

- `<WORKSPACE_ROOT>/.ai/` 中是否已有可复用的 TTS 运行环境和模型；不存在时按需下载，不假定固定产品或路径。
- `ffmpeg`、`ffprobe`、Node.js 和 HyperFrames CLI 是否可用。
- 检测当前 GPU、可用显存及编码器；只有支持时才使用 `h264_nvenc`。
- `TEMP`、pip/npm 缓存、浏览器缓存和媒体缓存是否位于 `<WORKSPACE_ROOT>/.cache/`；命令行使用绝对路径并安全传递空格或非 ASCII 目录。

Windows PowerShell 可按以下方式为当前进程设置工作目录与缓存；其他平台使用等价的当前进程环境变量，不修改系统全局配置：

```powershell
$WorkspaceRoot = (Resolve-Path -LiteralPath '<用户分配目录>').Path
$CacheRoot = Join-Path $WorkspaceRoot '.cache'
$AiRoot = Join-Path $WorkspaceRoot '.ai'
@($CacheRoot, $AiRoot, (Join-Path $CacheRoot 'temp')) | ForEach-Object {
  New-Item -ItemType Directory -Force -Path $_ | Out-Null
}
$Probe = Join-Path $CacheRoot '.write-test'
[IO.File]::WriteAllText($Probe, 'ok')
Remove-Item -LiteralPath $Probe
$env:HF_HOME = Join-Path $CacheRoot 'huggingface'
$env:HUGGINGFACE_HUB_CACHE = Join-Path $env:HF_HOME 'hub'
$env:MODELSCOPE_CACHE = Join-Path $CacheRoot 'modelscope'
$env:TORCH_HOME = Join-Path $CacheRoot 'torch'
$env:PIP_CACHE_DIR = Join-Path $CacheRoot 'pip'
$env:npm_config_cache = Join-Path $CacheRoot 'npm'
$env:TEMP = Join-Path $CacheRoot 'temp'
$env:TMP = $env:TEMP
```

创建这些目录并通过写入探针后再执行下载或安装。子进程必须继承这些环境变量。

默认锁定：`1920x1080`、`30fps`、质量合格的普通话男性声线、成片 `1.2x`、AAC 音频、约 `-16 LUFS`。用户明确指定的格式覆盖默认值。

## 1. 剧本入库与分段

把用户原始内容原样保存到 `SCRIPT_SOURCE.md`，生成 `segments.json`。每段至少包含：

```json
{
  "id": "s01",
  "narration": "本段完整旁白",
  "visual_intent": "画面要准确表达的概念",
  "search_zh": ["中文检索词"],
  "search_en": ["English search terms"],
  "estimated_seconds": 12,
  "fallback_visual": "找不到实拍素材时的原创图解"
}
```

把文案压成 5-8 个教学段落：钩子、核心问题、机制、证据、生活影响、行动建议、总结。每段只讲一个可视化概念。旁白目标约 150-180 汉字/分钟，先根据目标时长压缩文案，再生成 TTS。

## 2. 强制并行启动

`segments.json` 写入完成后，立即在同一轮并行工具调用中启动：

- **voice 工作流**：按段调用本地 TTS，输出 `audio/segments/sNN.wav`，再拼接、响度归一化并生成 `audio_meta.json` 与词级时间戳。
- **assets 工作流**：按中英文关键词检索公共素材库，核查许可并下载到 `assets/source/`，写入 `assets/manifest.jsonl`，随后转码到 `assets/stock-render/`。
- **主工作流**：并行期间创建 `STORYBOARD.md`、`frame.md`、图解方案和 HyperFrames 骨架，不占用上述两个写入范围。

优先使用一次并行工具调用；需要独立进程时把 PID 和日志记录在 `work/parallel-jobs.json`。在最终合流前必须等待两条任务结束，不能遗留后台进程。

合流规则：音频真实时长覆盖估算值；素材清单中的本地路径覆盖候选 URL；缺少素材的段落自动使用 `fallback_visual`。某个段落的 TTS 或素材下载失败时只重试该段一次，随后降级，不重启整个流水线。

## 3. 事实与医学边界

把文案压成 5-8 个教学段落：钩子、核心问题、机制、证据、生活影响、行动建议、总结。每段只讲一个可视化概念。旁白目标约 150-180 汉字/分钟，先根据目标时长压缩文案，再生成 TTS。

对医学和生命科学内容：区分“相关性”和“因果关系”，保留研究限制，不把动物实验直接写成人类结论，不把单项研究写成医学定论。文案内的百分比、年份和机构名称需要快速核验；无法核验时改为“研究显示”“与……相关”。

## 4. 素材和画面

按段建立素材表：`segment_id`、旁白关键词、素材来源、许可/公共站点说明、入点、出点、替代方案。优先高关联镜头：真实器官/实验/显微镜/天文观测/食物或人物行为；每段至少准备一个备用素材。

自主寻找和下载素材时读取 [public-media.md](public-media.md)。可以使用 `media-use` 管理已有素材和清单，但公共站点下载仍必须记录原始来源与许可。

长视频必须先转码为适合逐帧 seek 的本地中间文件，推荐 30fps、GOP 1、H.264 NVENC，放在项目 `assets\stock-render`。不要直接让 HyperFrames 反复 seek 原始高码率文件。素材不能加载时，用 CSS/Canvas/SVG 之外的原创位图或 HTML 图解补位；动态图解必须可暂停、可 seek、不能依赖实时随机数。

## 5. TTS、模型与时间线

使用单一声音和单一全局提示词，推荐：

`标准普通话，成年男性，沉稳自然，纪录片式科普讲解，语速清晰，情绪克制，段落之间自然停顿，整段语气保持一致，不夸张，不播音腔，不带方言口音。`

默认直接复用 `WORKSPACE_ROOT/.ai/` 中质量合格的本地 TTS，不联网寻找替代模型。只有现有模型缺失、运行失败、语言/音色不匹配或用户明确要求更换时，才读取 [tts-models.md](tts-models.md) 并自主选择、下载和冒烟测试新模型。下载不得阻塞素材工作流。

生成后检查每个段落的音频波形和时长；用真实时长更新 storyboard，字幕以词级或短句级时间戳为准。先做响度归一化，再在最终阶段统一 1.2 倍速并保持音高。若加速后旁白过密，缩短字幕行而不是把画面再单独加速。

## 6. HyperFrames 实现

先读 `hyperframes`、`hyperframes-core`、`hyperframes-media` 和需要的 `hyperframes-animation`。每个场景使用可 seek 的单一时间线、`data-*` 时间属性和 `class="clip"`；媒体播放交给框架。镜头 HTML 只承担本镜头，不让多个 worker 同时写同一文件。

推荐顺序：

```powershell
node hf-cli.mjs lint
node hf-cli.mjs validate
node hf-cli.mjs inspect
node hf-cli.mjs snapshot --at <各段中点>
node hf-cli.mjs render --quality high --output renders\final-rendered-raw.mp4
node normalize-audio.mjs
ffmpeg -i normal.mp4 -filter_complex "[0:v]setpts=PTS/1.2[v];[0:a]atempo=1.2[a]" -map "[v]" -map "[a]" -c:v h264_nvenc -c:a aac renders\final.mp4
```

实际脚本名称以项目已有模板为准。HyperFrames 检查失败时只修复导致失败的文件后重跑该检查，不连续堆叠无关命令。

## 7. 最终验收

使用 `ffprobe` 确认最终文件存在，分辨率为目标值、帧率约 30、音视频时长一致且音频轨存在。抽查片头、机制解释、统计数据、结尾四个位置：不能空白、冻结、素材与旁白无关、字幕越过安全区或遮挡主体。报告最终绝对路径、时长、画幅、配音声线和验证结果；任何未执行的检查必须明确写出。

同时验收 `assets/manifest.jsonl`：所有实际使用的外部素材都有来源、作者/机构、许可和本地路径；所有模型记录模型 ID、版本/commit、许可证、来源和本地目录。

## 资源发现顺序

1. 当前项目已冻结的媒体、脚本和模型配置。
2. `<WORKSPACE_ROOT>/.ai/` 中已验证的 TTS 环境与模型。
3. `<WORKSPACE_ROOT>/.cache/` 中可复用的模型、浏览器和媒体缓存。
4. 可信公共素材库与官方模型仓库。

发现旧项目时只复用明确兼容且来源可追溯的资源，不依赖特定机器上的固定项目名称。
