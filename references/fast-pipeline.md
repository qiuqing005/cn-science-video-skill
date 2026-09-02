# 一键快速工作流

`scripts/run_fast_pipeline.py` 将已确定的剧本与场景配置自动推进到成片。它负责并行 TTS、素材、字体处理，随后完成字幕对齐、组装、检查、快照、渲染、响度处理和验收。

## 项目输入契约

调用前项目至少包含：

- `audio_request.json`：`{"lines":[{"id":"01","text":"..."}]}`。
- `segments.json`：与音频行 ID 和顺序对应的场景数据。
- `SCRIPT_SOURCE.md`：原始剧本，供字体子集使用。
- `assemble.mjs`：读取 `segments.json`、`audio_meta.json`、`captions.json` 并生成 `index.html`、`timeline.json`。
- `hf-cli.mjs`：项目本地 HyperFrames 包装器；路径也可由 `--hf-cli` 指定。
- 可选 `scripts/download_assets.py`：项目定制素材解析器。不存在时自动使用 Skill 内置的 `download_commons_assets.py`，并发按 `search_en` / `search_zh` 为每个场景寻找一张许可兼容的 Wikimedia Commons 图片，优先冻结 1920px 版本并写 `assets/manifest.jsonl`。素材失败不会终止流水线，组装器必须支持原创图解 fallback。

`timeline.json` 必须包含 `scenes: [{start, duration}]`，工作流由此计算快照中点。

## 调用

```powershell
python <SKILL_DIR>\scripts\run_fast_pipeline.py `
  --project <PROJECT_DIR> `
  --tts-python <WORKSPACE_ROOT>\.ai\tts\envs\qwen\Scripts\python.exe `
  --model <WORKSPACE_ROOT>\.ai\tts\models\qwen\Qwen3-TTS-12Hz-1.7B-CustomVoice `
  --whisper-cache <WORKSPACE_ROOT>\.cache\whisper `
  --font <WORKSPACE_ROOT>\.ai\fonts\NotoSansSC-VF.ttf
```

如果项目 wrapper 不在默认位置，增加 `--hf-cli <absolute-path>`。调试期间可用 `--skip-render` 只执行到快照；需要完整重建时使用 `--force`。

## 阶段顺序

1. 并行：`voice`、`assets`、`font`。
2. `captions`：Whisper 时间边界映射回原始剧本。
3. `assemble`。
4. `lint`。
5. 并行：`validate`、`inspect`。
6. `snapshot`。
7. `render`。
8. `normalize_analyze` / `finalize`：两遍测量归一化音频，并直接复制视频流。
9. `ffprobe`、`blackdetect`、`loudness`，并断言音视频轨、时长差、尺寸、黑帧和响度均合格。

每个阶段的输出写入 `work/<stage>.log`，阶段耗时、总墙钟时间和跳过原因写入 `work/performance.json`。

## 断点续跑

默认比较输出与输入的修改时间：

- `audio_meta.json` 新于 `audio_request.json` 时跳过 TTS，并核验其引用的音频文件存在。
- `assets/manifest.jsonl` 新于素材脚本和 `segments.json` 时跳过下载。
- 字体子集新于字体、剧本和场景数据时跳过。
- `captions.json` 新于音频时跳过对齐。
- `index.html` 与 `timeline.json` 新于场景、音频、字幕和组装器时跳过组装。
- contact sheet、渲染文件和最终文件均按依赖时间复用。

局部修改只使下游相关阶段失效。例如修改 `assemble.mjs` 会重跑组装、检查和渲染，但不会重新生成 TTS 或下载素材。

## 成功门槛

命令返回 0，`lint`、`validate`、`inspect` 全部通过，`renders/final.mp4` 存在且 `work/performance.json` 完整。素材阶段允许失败降级，但失败详情必须保留在日志中，组装器不得引用不存在的素材。
