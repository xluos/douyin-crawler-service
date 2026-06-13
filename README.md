# Douyin Crawler Service

独立 HTTP 服务，用来包装 `DouYin_Spider` 的公开读取能力。服务本身维护多账号、多队列、任务状态和结果文件；`DouYin_Spider` 只作为采集引擎依赖，不在源仓库里继续改业务逻辑。

## 启动

```bash
uv venv
uv pip install -e .
.venv/bin/douyin-crawler-service
```

默认读取本机现有仓库：

```bash
DOUYIN_SPIDER_PATH=/Users/bytedance/Documents/AIWorkspace/douyin-tool-eval/vendor/DouYin_Spider
```

也可以在 `.env` 里覆盖。

本机启动默认监听 `127.0.0.1:18099`，避开常见开发端口。匿名 cookie 生成默认使用本机 Chrome；容器部署时会把 `BROWSER_CHANNEL` 置空，改用 Playwright 镜像内置 Chromium。

## Docker 部署

```bash
docker compose up -d --build
curl http://127.0.0.1:18099/health
```

Docker 默认配置：

- 服务端口：`18099`
- 数据目录：宿主机 `./data` 挂载到容器 `/app/data`
- 采集引擎：构建时克隆 `https://github.com/xluos/DouYin_Spider.git` 的固定提交 `d9766c9dd0f3bf801d3dd09facb1d24f2a1c5c53` 到 `/opt/DouYin_Spider`
- 浏览器：Playwright 官方镜像内置 Chromium，`BROWSER_CHANNEL=""`

如果要换 DouYin_Spider 来源：

```bash
docker compose build \
  --build-arg DOUYIN_SPIDER_REPO=https://github.com/xluos/DouYin_Spider.git \
  --build-arg DOUYIN_SPIDER_REF=d9766c9dd0f3bf801d3dd09facb1d24f2a1c5c53
```

`DOUYIN_SPIDER_REF` 可以传分支、tag 或 commit SHA；生产发布默认使用 commit SHA，避免同一个服务提交在不同时间构建出不同镜像。

## GitHub Actions 自动发布

`.github/workflows/deploy-service.yml` 会在 `main` 分支 push 后自动发布到服务器，逻辑参考 `outfit-master`，但部署方式改成更适合这个服务的镜像流：

1. GitHub Actions 构建 Docker 镜像。
2. 推送到 GitHub Container Registry：`ghcr.io/xluos/douyin-crawler-service:<commit-sha>` 和 `latest`。
3. SSH 到服务器。
4. 服务器使用本次 workflow 的 `github.token` 临时登录 GHCR，拉取镜像后立即 `docker logout`。
5. `docker compose up -d` 启动服务。
6. 检查 `http://127.0.0.1:18099/health`。

需要在 GitHub 仓库配置这些 Variables，路径是 `Settings` -> `Secrets and variables` -> `Actions` -> `Variables`，这些值后续可以二次查看：

- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_PORT`，可选，不填默认 `22`
- `DEPLOY_DIR`，可选，不填默认 `/opt/douyin-crawler-service`
- `SERVICE_PORT`，可选，不填默认 `18099`

还需要配置这个 Secret，路径是 `Settings` -> `Secrets and variables` -> `Actions` -> `Secrets`：

- `DEPLOY_SSH_KEY`
- `BARK_DEPLOY_NOTIFY_KEY`，可选，配置后部署成功会发送 Bark 通知

`DEPLOY_HOST` 是部署服务器的公网 IP 或 DNS 名称，适合放在可见的 Variables 里。`DEPLOY_SSH_KEY` 是服务器私钥，不能放到可见变量里；Secret 保存后不可回显是正常设计，只能重新写入。

默认部署目录是服务器 `/opt/douyin-crawler-service`，健康检查端口是 `18099`。workflow 会在服务器上生成 `docker-compose.deploy.yml`，其中只引用已构建好的 GHCR 镜像，不再在服务器上 build 源码。

## API

```bash
# 健康检查
curl http://127.0.0.1:18099/health

# 创建匿名账号，服务会打开无头浏览器生成基础 cookie
curl -X POST http://127.0.0.1:18099/accounts \
  -H 'content-type: application/json' \
  -d '{"name":"anon-1","refresh_cookie":true}'

# 创建采集任务
curl -X POST http://127.0.0.1:18099/jobs \
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

# 创建单作品采集任务，只抓指定 aweme_id 的作品图片和评论。
# aweme_id 可传纯数字 ID、/video/ 长链、/note/ 图文长链，或包含 v.douyin.com 短链的整段分享文案。
curl -X POST http://127.0.0.1:18099/aweme-jobs \
  -H 'content-type: application/json' \
  -d '{
    "account_id": 1,
    "queue_name": "public",
    "aweme_id": "7370000000000000000",
    "max_comments_per_video": 10,
    "include_replies": false,
    "sleep_seconds": 1
  }'

# 查询任务
curl http://127.0.0.1:18099/jobs/1

# 查询任务摘要
curl http://127.0.0.1:18099/jobs/1/result

# 查询任务事件
curl http://127.0.0.1:18099/jobs/1/events

# 查询任务日志
curl 'http://127.0.0.1:18099/jobs/1/logs?lines=200'

# 查询带图片评论
curl 'http://127.0.0.1:18099/jobs/1/comments?with_pictures=true&limit=20'
```

`max_pages` 最小为 1，避免误触发全量翻页；`max_comments_per_video=0` 表示只抓账号和作品，不抓评论。

`POST /aweme-jobs` 不会翻账号作品列表；它直接请求单个作品详情和该作品评论，结果仍写到同一组文件：
`user.json`、`videos.jsonl`、`comments.jsonl`、`summary.json`。作品或图集图片在 `videos.jsonl` 的
`note_image_urls` 字段，评论图片在 `comments.jsonl` 的 `picture_urls` 字段。

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
