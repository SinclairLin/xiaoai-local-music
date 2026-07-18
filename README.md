# xiaoai-local-music

NAS Docker 上的小爱本地音乐桥接服务骨架。它扫描只读挂载的本地曲库，提供简单网页和 HTTP API，并把“播放 xxx”解析为曲目查询。当前播放行为是 Mock 确认，尚未接入真实音频输出或小爱平台协议。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

访问 <http://127.0.0.1:8123/>；健康检查为 `/healthz`。

## Docker Compose

部署前先复制模板：`cp compose.yml.example compose.yml`（`compose.yml` 已在 `.gitignore` 中，用于放本机路径）。模板默认使用 `ghcr.io/sinclairlin/xiaoai-local-music:latest`，并映射端口 `8123`。NAS 上可按需设置：

```bash
export MUSIC_HOST_DIR=/mnt/pool1/personal/media/音乐
export CONFIG_HOST_DIR=/mnt/pool1/home/linzx6/xiaoai-local-music/config
docker compose up -d
```

曲库以 `/music:ro` 挂载，配置目录为 `/config`。也可以直接设置 `MUSIC_DIR`、`CONFIG_DIR`、`HOST`、`PORT` 环境变量。

## API

- `GET /api/tracks?q=关键词`：查询曲目。
- `POST /api/play`：请求体 `{ "track_id": "..." }`，返回 Mock 播放状态。
- `POST /api/voice`：请求体 `{ "text": "播放 稻香" }`。

## GHCR

`.github/workflows/image.yml` 会在推送 `main` 或 tag 时用 Buildx 构建 amd64/arm64 镜像并推送 GHCR。首次发布后请在 GitHub Package 设置中确认镜像可见性为 public。

## 风险

服务默认假设运行在可信内网，没有认证和限流；不要直接暴露到公网。音频目录只读挂载，配置和日志目录仍需按 NAS 权限策略管理。
