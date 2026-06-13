# 前端支持抖音图集作品改造说明

本文给另一个“前端抓取页面”使用。目标是让原本只支持视频作品的页面，同时支持抖音图文/图集作品。

## 后端输入能力

`POST /aweme-jobs` 的 `aweme_id` 字段现在可以传以下任意形式：

- 纯数字作品 ID：`7646974327342695330`
- 视频长链：`https://www.douyin.com/video/7646974327342695330`
- 图文长链：`https://www.douyin.com/note/7646974327342695330?previous_page=app_code_link`
- 抖音短链分享文案：`1.07 复制打开抖音，看看【露小裕的图文作品】... https://v.douyin.com/Isaxn57al44/ ...`
- `www.iesdouyin.com/share/note/<id>` 这类短链首跳后的中间链接

前端不需要自己解析短链，也不需要区分视频和图文链接。输入框可以直接改成“作品链接或分享文案”，把用户粘贴的整段内容原样传给 `aweme_id`。

示例：

```bash
curl -X POST http://127.0.0.1:18099/aweme-jobs \
  -H 'content-type: application/json' \
  -d '{
    "account_id": 1,
    "queue_name": "public",
    "aweme_id": "1.07 复制打开抖音，看看【露小裕的图文作品】# 她真的好甜 https://v.douyin.com/Isaxn57al44/",
    "max_comments_per_video": 0,
    "include_replies": false,
    "sleep_seconds": 1
  }'
```

## 结果字段

任务完成后，前端仍然读取：

```text
GET /jobs/{job_id}/videos
```

每条作品结果里与展示相关的字段：

```json
{
  "aweme_id": "7646974327342695330",
  "aweme_url": "https://www.douyin.com/video/7646974327342695330",
  "aweme_type": 68,
  "desc": "#她真的好甜#小主人原作者@平凡的胡德禄",
  "cover_url": "https://...",
  "video_urls": ["https://..."],
  "note_image_urls": ["https://...", "https://..."]
}
```

判断规则：

- `note_image_urls.length > 0`：按图集作品展示。
- `video_urls.length > 0` 且 `note_image_urls.length === 0`：按视频作品展示。
- 两者都为空：展示为“未解析到媒体资源”，保留标题、作者、作品 ID 和原始链接，方便排查。

图集作品常见 `aweme_type` 是 `68`，但前端不要只依赖 `aweme_type`，以 `note_image_urls` 是否非空作为最终展示依据更稳。

## 前端交互建议

输入区：

- 文案从“视频 ID/链接”改成“作品链接或分享文案”。
- 不要限制只能输入数字或 `/video/` URL。
- 提交前只做非空校验即可，解析失败由后端返回 `400`。

列表/详情区：

- 图集卡片优先展示 `note_image_urls[0]`，没有时再使用 `cover_url`。
- 图集详情页使用轮播、九宫格或横向图片列表展示 `note_image_urls`。
- 视频详情页继续使用现有 `video_urls[0]` 播放逻辑。
- 如果一个作品同时返回 `video_urls` 和 `note_image_urls`，优先按图集展示，保留“打开原作品”链接。

错误提示：

- 后端返回 `400 unsupported aweme url` 时提示：“未识别到抖音作品链接，请粘贴作品链接或完整分享文案。”
- 后端任务失败但已创建 job 时，继续使用现有任务状态和日志入口排查。

## 本次实测样例

输入分享文案中的短链：

```text
https://v.douyin.com/Isaxn57al44/
```

后端解析出的作品 ID：

```text
7646974327342695330
```

作品详情结果：

- `aweme_type`: `68`
- `desc`: `#她真的好甜#小主人原作者@平凡的胡德禄`
- `note_image_urls`: `15` 条
- `video_urls`: `2` 条

因此前端应按图集展示，图片来源使用 `note_image_urls`。
