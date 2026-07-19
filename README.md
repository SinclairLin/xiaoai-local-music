# xiaoai-local-music

NAS Docker 上的小爱本地音乐桥接服务。它扫描只读挂载的本地曲库，提供简单网页和 HTTP API，并通过 [miservice](https://github.com/Yonsm/MiService) 直连小米云（或 mock 模式）控制音箱播放。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PUBLIC_BASE_URL=http://192.168.1.10:8123
python -m app.main
```

访问 <http://127.0.0.1:8123/>；健康检查为 `/healthz`。

服务启动时会读取 `/config/config.yaml` 中的 `music_root` 和 `public_base_url` 并扫描曲库，扫描结果缓存在内存中。`public_base_url` 必须是音箱可访问的绝对 HTTP(S) 地址，也可用环境变量 `PUBLIC_BASE_URL` 覆盖。环境变量 `MUSIC_ROOT` 优先级最高，旧变量 `MUSIC_DIR` 仍可用；如果公开地址缺失、非法或曲库目录不存在、不是目录，服务会启动失败。曲库目录不可读时服务仍会启动，但曲目列表为空。启动后的文件变化需要重启服务才能进入曲目列表。

## Docker Compose

部署前先复制模板：`cp compose.yml.example compose.yml`（`compose.yml` 已在 `.gitignore` 中，用于放本机路径）。模板默认使用 `ghcr.io/sinclairlin/xiaoai-local-music:latest`，并映射端口 `8123`。NAS 上可按需设置：

```bash
export MUSIC_HOST_DIR=/mnt/pool1/personal/media/音乐
export CONFIG_HOST_DIR=/mnt/pool1/home/linzx6/xiaoai-local-music/config
export PUBLIC_BASE_URL=http://nas-host:8123
docker compose up -d
```

曲库以 `/music:ro` 挂载，配置目录为 `/config`。应用读取 `/config/config.yaml`，可先复制
`config/config.yaml.example` 为 `config/config.yaml`。配置文件使用扁平键：
`xiaomi_user`、`xiaomi_password`、`mina_mode`、`mina_device_id`、`public_base_url`、`music_root`、`host` 和 `port`。

小米登录 token 保存在 `/config/.mi.token`，权限为 600，服务不会把它放进镜像或 Git。没有真实账号时设置 `mina_mode: mock`；`miservice` 模式需要提供小米账号、密码和设备 ID。

环境变量优先于 YAML 配置，空字符串不会覆盖文件值：

- `XIAOMI_USER`、`XIAOMI_PASSWORD`、`MINA_MODE`、`MINA_DEVICE_ID`、`PUBLIC_BASE_URL`、`MUSIC_ROOT`
- 兼容变量 `MUSIC_DIR`（仅在未设置 `MUSIC_ROOT` 时使用）
- 运行参数 `CONFIG_DIR`、`HOST`、`PORT`

配置模块提供 `Settings.save()` 显式写回配置文件；服务启动不会自动回写。保存使用同目录临时文件原子替换，配置文件包含凭据时应限制为仅服务用户可读。
也可以直接设置 `PUBLIC_BASE_URL`、`MUSIC_ROOT`、`MUSIC_DIR`、`CONFIG_DIR`、`HOST`、`PORT`、`XIAOMI_USER`、`XIAOMI_PASSWORD`、`MINA_MODE` 和 `MINA_DEVICE_ID` 环境变量。

曲库索引保存在内存中，不落盘；曲目新增、删除或修改后需要重启服务才会生效。当前版本不解析音频标签，曲目标题取自文件名；配置目录仅在调用 `Settings.save()` 写回配置时需要写权限。

## 为何选 miservice 而非 miservice-fork

| 维度 | miservice (Yonsm/MiService) | miservice-fork (yihong0618/MiService) |
|---|---|---|
| PyPI 最新版 | 3.0.1，2026-07-03 发布 | 2.9.3，2025-10-31 发布 |
| GitHub | 817 stars，2026-07 活跃 | 441 stars，2026-05 的登录修复未发版到 PyPI |
| 依赖 | 零硬依赖（aiohttp 可选，内置 biohttp 回退） | setuptools/aiohttp/mutagen/rich/fake-useragent |
| 登录 | 支持 SMS/Email OTP 两步验证（3.0 新增，应对小米风控） | 无 OTP，异常登录需手工跑仓库脚本 |
| 协议 | MIT | MIT |

两者 API 同源，`MiNAService` 接口一致；原版发版更勤、依赖更干净且内置 OTP 支持，故选原版。可选安装 `aiohttp` 提升网络性能，未安装时 miservice 自动回退到内置 biohttp。

## 登录与 OTP

服务进程内无法完成交互式 OTP 验证。若小米风控要求两步验证，服务会返回 502 并附带提示。此时请在宿主机预登录：

```bash
pip install miservice
export MI_USER=<小米账号>
export MI_PASS=<密码>
python -m miservice mina   # 触发登录（必要时交互输入 OTP），并列出音箱设备
```

登录成功后 token 写入 `~/.mi.token`，将其复制到服务的 config 目录（容器内 `/config`），重启后服务复用该 token，不再触发交互登录。上述 `mina` 命令还会列出账号下的小爱音箱，可从中取 `mina_device_id`。在网页或 API 中修改小米账号或密码会删除旧 token，下次调用时重新登录。

## API

- `GET /api/tracks?q=关键词`：查询曲目。
- `GET /media/by-id/{track_id}`：获取音频文件，支持 HTTP Range。
- `POST /api/play`：请求体 `{ "track_id": "..." }`，返回 Mock 播放状态。
- `POST /api/play`：可选 `queue_ids` 建立有序内存队列，并调用 Mina `play_by_url`。
- `POST /api/voice`：请求体 `{ "text": "播放 稻香" }`。
- `GET /api/config`、`PUT /api/config`：读取或更新配置；密码在 GET 响应中脱敏。PUT 响应含 `restart_required`：`music_root`、`host`、`port`、`public_base_url` 的变更会写入配置文件，但需重启服务才生效。
- `GET /api/devices`、`GET /api/queue`：查看设备和当前队列状态。
- `POST /api/next`、`/api/previous`、`/api/pause`、`/api/resume`、`/api/stop`、`/api/volume`：播放控制。

曲目响应中的 `path` 是 `{public_base_url}/media/by-id/{track_id}`，可直接作为后续交给音箱的媒体 URL。

## GHCR

`.github/workflows/image.yml` 会在推送 `main` 或 tag 时用 Buildx 构建 amd64/arm64 镜像并推送 GHCR。首次发布后请在 GitHub Package 设置中确认镜像可见性为 public。

## 风险

服务默认假设运行在可信内网，没有认证和限流；不要直接暴露到公网。音频目录只读挂载，配置和日志目录仍需按 NAS 权限策略管理。
