# 公共科普素材检索与下载

## 来源优先级

先用主题的中英文关键词检索，优先级如下：

1. 科学机构原始素材：NASA、NOAA、USGS、NIH/NIAID、CDC Public Health Image Library、ESA 等。逐项查看使用条款；政府机构标识、第三方署名素材和人物肖像不自动等同于公有领域。
2. Wikimedia Commons：优先 Public Domain、CC0、CC BY；使用 CC BY-SA 时保留相同许可要求和完整署名。
3. Openverse：只把它当搜索入口，回到原始页面核对作者、许可和原文件。
4. Pexels、Pixabay、Mixkit 等免版税素材站：读取当前站点许可，避免商标、可识别人物敏感用途及禁止再分发的用法。
5. Internet Archive 或博物馆开放馆藏：仅使用条目标记为 Public Domain、CC0 或许可明确允许当前用途的文件。

不要默认抓取普通搜索引擎图片、社交媒体或 YouTube 视频。只有来源页明确给出可下载文件及兼容许可时才使用。不得绕过登录、付费墙、DRM、验证码、访问限制或站点下载限制。

## 每段检索方法

- 从 `visual_intent` 提取主体、行为、尺度、场景和镜头类型，分别生成 2-4 个中文和英文短语。
- 优先检索能证明旁白内容的镜头，而不是只有氛围的镜头。例如“深度睡眠清除代谢物”优先脑部/睡眠实验和类淋巴图解，而不是夜空或空床。
- 下载原始分辨率文件；视频优先 1080p 或更高、横屏、无水印、稳定帧率。保留原文件，不覆盖转码版本。
- 一个来源失败后换下一个来源，不对同一 URL 无限重试。单素材最多重试一次。

## 许可清单

`assets/manifest.jsonl` 每行至少包含：

```json
{"id":"s01-a","segment_id":"s01","kind":"video","source_url":"https://...","download_url":"https://...","creator":"机构或作者","license":"CC BY 4.0","license_url":"https://...","retrieved_at":"2026-08-31T12:00:00+08:00","local_source":"assets/source/s01-a.mp4","local_render":"assets/stock-render/s01-a.mp4","sha256":"...","used":true}
```

没有明确许可或来源页无法访问时设置 `used:false`，并使用备用素材或原创图解。需要署名的素材同时生成 `CREDITS.md`，不得把署名只留在内部清单中。

## 下载和转码

所有文件下载到项目目录，使用直接下载 URL、官方 API 或站点允许的下载方式。校验非零文件、MIME/扩展名、时长、分辨率和 SHA-256。HTML 错误页、缩略图、水印预览和损坏文件立即淘汰。

视频转为 30fps、固定 GOP 的本地渲染版本。RTX 5070 可优先使用 `h264_nvenc`；失败时回退 `libx264`。转码文件只写 `assets/stock-render/`，原文件留在 `assets/source/` 供追溯。
