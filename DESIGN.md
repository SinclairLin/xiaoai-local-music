# 设计说明

## 目标

服务把 NAS 上的本地曲库整理成一个小型 HTTP/ASGI 服务，为后续小爱语音桥接提供稳定入口。当前版本实现目录扫描、HTTP 音频输出和 Mock 播放确认。

## 模块

- `config.py`：读取 `/config/config.yaml` 并应用环境变量覆盖，校验音箱可访问的 `public_base_url`，支持凭据配置和 `Settings.save()` 原子写回。
- `mina_client.py`：提供可注入的 Mina client（miservice 直连或 mock），同步接口经 `asyncio.run` 桥接 miservice 的 async API，封装设备、TTS 与播放控制；token 由 miservice 的 `MiTokenStore` 以 600 权限落盘复用。
- `service.py`：启动时扫描 `mp3/flac/m4a/wav`，生成稳定曲目 ID，缓存安全文件路径与 MIME 类型，维护内存播放队列并委托 Mina 播放。
- `voice.py`：解析中文播放和播放控制意图，可去掉唤醒词/礼貌前缀及可选“本地”前缀。
- `voice_worker.py`：lifespan 管理的 asyncio 轮询 worker；优先 conversation 历史 API，失败时回退 MiNA ubus NLP，并将事件送入本地播放器。
- `routes.py`：管理台首页（返回 `app/static/index.html`）、健康检查、配置读写（密码脱敏）、设备发现、曲目查询、支持 HTTP Range 的媒体文件、播放和队列控制接口。
- `main.py`：组装 FastAPI 应用并提供命令行入口。

应用 lifespan 在启动阶段调用一次曲库扫描，并按 `voice.enabled` 启停语音 worker。`PUBLIC_BASE_URL` 和 `MUSIC_ROOT` 分别覆盖 YAML 的同名配置，`MUSIC_DIR` 作为兼容旧配置；配置或扫描失败会阻止应用启动。缓存不自动刷新，曲库变更需要重启服务。公开 Track 的 `path` 使用稳定 ID 组成媒体 URL，不暴露文件系统路径。

## 风险与后续

服务仍没有用户认证和数据库。Mina 控制通过 miservice 直连小米云：同步端点在无事件循环的线程池中以 `asyncio.run` 逐次桥接（端点若改为 async 需更换桥接方案）；小米风控触发 OTP 时服务无法交互，需在宿主机 `python -m miservice` 预登录并把 `.mi.token` 放入 config 目录。同步端点在线程池并发执行，内存队列与 Mina client 的替换未加锁，家庭单用户场景可接受，高并发使用需先补锁；`music_root`、`host`、`port`、`public_base_url` 只在启动时被消费，运行时修改需重启生效，`PUT /api/config` 响应以 `restart_required` 标注。真实部署应在可信内网使用；若开放到公网，需要增加认证和请求限流。
