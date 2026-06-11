# Douyin Crawler Service

独立 HTTP 服务，用来包装 `DouYin_Spider` 的公开读取能力。服务本身维护多账号、多队列、任务状态和结果文件；`DouYin_Spider` 只作为采集引擎依赖，不在源仓库里继续改业务逻辑。

## 启动

```bash
uv venv
uv pip install -e .
.venv/bin/uvicorn douyin_crawler_service.app:create_app --factory --host 127.0.0.1 --port 8099
```

默认读取本机现有仓库：

```bash
DOUYIN_SPIDER_PATH=/Users/bytedance/Documents/AIWorkspace/douyin-tool-eval/vendor/DouYin_Spider
```

也可以在 `.env` 里覆盖。

匿名 cookie 生成默认使用本机 Chrome。如果机器没有 Chrome，先安装 Chrome，或者后续把 `spider_adapter.py` 里的 Playwright 启动参数改成项目内浏览器。

## API

```bash
# 健康检查
curl http://127.0.0.1:8099/health

# 创建匿名账号，服务会打开无头浏览器生成基础 cookie
curl -X POST http://127.0.0.1:8099/accounts \
  -H 'content-type: application/json' \
  -d '{"name":"anon-1","refresh_cookie":true}'

# 创建采集任务
curl -X POST http://127.0.0.1:8099/jobs \
  -H 'content-type: application/json' \
  -d '{
    "account_id": 1,
    "queue_name": "public",
    "user_url": "https://www.douyin.com/user/MS4wLjABAAAAEpmH344CkCw2M58T33Q8TuFpdvJsOyaZcbWxAMc6H03wOVFf1Ow4mPP94TDUS4Us",
    "max_pages": 1,
    "max_comments_per_video": 20,
    "include_replies": false,
    "sleep_seconds": 1
  }'

# 查询任务
curl http://127.0.0.1:8099/jobs/1

# 查询任务摘要
curl http://127.0.0.1:8099/jobs/1/result

# 查询任务事件
curl http://127.0.0.1:8099/jobs/1/events

# 查询任务日志
curl 'http://127.0.0.1:8099/jobs/1/logs?lines=200'

# 查询带图片评论
curl 'http://127.0.0.1:8099/jobs/1/comments?with_pictures=true&limit=20'
```

`max_pages` 最小为 1，避免误触发全量翻页；`max_comments_per_video=0` 表示只抓账号和作品，不抓评论。

## 日志和排查

- 全局服务日志：`data/logs/service.log`
- 单任务日志：`data/jobs/<job_id>/job.log`
- 结构化任务事件：`GET /jobs/<job_id>/events`
- 单任务尾部日志：`GET /jobs/<job_id>/logs?lines=200`
- 任务结果文件：`data/jobs/<job_id>/user.json`、`videos.jsonl`、`comments.jsonl`、`summary.json`

日志会记录 `request_id`、`job_id`、队列名、接口耗时、队列入队/启动/结束、采集分页游标、返回状态、评论图片数量和异常堆栈。Cookie 内容不会写入日志。

## 设计边界

- 只覆盖公开账号信息、作品列表、评论和评论区图片 URL。
- 匿名 cookie 不是空 cookie，需要先由真实浏览器访问公开页生成 `ttwid/s_v_web_id` 等基础字段。
- 私信、会话、互动、发布评论等依赖当前登录用户身份的能力不放在这个服务入口里。
- 任务结果写到 `data/jobs/<job_id>/`，cookie 写到 `data/accounts/<account_id>.cookie`，SQLite 写到 `data/service.db`。
