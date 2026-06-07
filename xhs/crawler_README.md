# 小红书美甲趋势采集 MVP

使用 Python + Playwright 打开浏览器，搜索关键词 `美甲`，低频采集公开可见内容。新版会：

- 最近 90 天的帖子进入趋势分析池。
- 最近 30 天内的帖子才下载图片并进入展示图库。
- 每次采集创建一个时间命名的批次目录。
- 图片先下载到临时目录，再调用豆包视觉 API 筛选。
- 只保存“完整展示美甲且只有一套美甲”的图片。
- 过滤组图、拼图、合集、多款式对比、无美甲或严重裁剪图片。

## 输出位置

每次运行会生成：

```text
D:\NailMindAI\xhs\runs\YYYYMMDD_HHMMSS
├── hot_nails.json
├── summary.md
├── filtered_out.json
├── rejected_images
│   └── 01_filtered_xxx.webp
└── images
    └── 01_xxx.webp
```

浏览器登录状态单独保存在：

```text
D:\NailMindAI\xhs\data\xhs_storage_state.json
```

这样每次采集数据不会混在一起，但登录状态可以复用。

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

## 配置豆包视觉 API

不要把 API key 写进代码。运行前设置环境变量：

```powershell
$env:ARK_API_KEY="你的 ark key"
```

你提供的 curl 对应配置已写在 `config.py`：

- `ARK_API_URL`
- `ARK_MODEL`
- `ARK_API_KEY_ENV`

## 运行采集

```bash
python main.py
```

运行流程：

1. 打开 Chromium 浏览器。
2. 如果需要登录，在浏览器里手动完成登录即可，脚本会自动检测登录状态并继续，不需要回到终端按 Enter。
3. 脚本抓取更多候选内容。
4. 进入详情页解析发布时间，最近 90 天进入分析池，最近 30 天下载图片。
5. 下载候选图片到临时目录。
6. 调用豆包视觉 API 判断是否为“完整单套美甲图”。
7. 合格图片保存到本次批次 `images/`。
8. 不合格图片保存到本次批次 `rejected_images/`，方便人工检查。
8. 输出 `hot_nails.json`、`summary.md`、`filtered_out.json`。

## 生成网站

```bash
python build_site.py
```

网站会自动读取最新的 `runs/时间戳/hot_nails.json`，输出到：

```text
D:\NailMindAI\xhs\site\index.html
```

## 主要配置

在 `config.py` 中：

- `LIMIT = 20`: 最终入选数量。
- `ANALYSIS_DAYS = 90`: 最近 90 天进入趋势分析。
- `RECENT_DAYS = 30`: 最近 30 天下载图片并进入展示图库。
- `CANDIDATE_LIMIT = 80`: 候选池大小，图片筛选严格时可以调大。
- `INCLUDE_UNKNOWN_DATE = False`: 发布时间未知时默认跳过。
- `FILTER_IMAGES_WITH_VISION = True`: 是否启用视觉筛选。
- `MAX_SCROLL_ROUNDS = 35`: 最多滚动轮数。

## 输出字段

`hot_nails.json`：

```json
{
  "keyword": "美甲",
  "run_id": "20260603_101500",
  "recent_days": 30,
  "analysis_days": 90,
  "count": 20,
  "items": [
    {
      "title": "标题",
      "image_url": "原始图片 URL",
      "image_path": "D:\\NailMindAI\\xhs\\runs\\...\\images\\01_xxx.webp",
      "likes": 0,
      "collects": 0,
      "comments": 0,
      "url": "小红书原帖链接",
      "published_at": "2026-06-01T00:00:00",
      "days_old": 2,
      "vision_keep": true,
      "vision_reason": "清晰展示完整单套美甲",
      "vision_category": "完整单套美甲"
    }
  ]
}
```

`filtered_out.json` 会记录被过滤掉的候选及原因，方便复盘。
如果候选已经下载并经过视觉筛选但未通过，会额外记录 `rejected_image_path`。
