# 设计说明

## 目标

服务把 NAS 上的本地曲库整理成一个小型 HTTP/ASGI 服务，为后续小爱语音桥接提供稳定入口。当前版本实现目录扫描、HTTP 音频输出和 Mock 播放确认。

## 模块

- `config.py`：读取 `/config/config.yaml` 并应用环境变量覆盖，校验音箱可访问的 `public_base_url`，支持凭据配置和 `Settings.save()` 原子写回。
- `service.py`：启动时扫描 `mp3/flac/m4a/wav`，生成稳定曲目 ID，缓存安全文件路径与 MIME 类型，并返回 Mock 播放状态。
- `voice.py`：识别所有以“播放”开头的文本，可去掉可选“本地”前缀。
- `routes.py`：健康检查、简单 HTML、曲目查询、支持 HTTP Range 的媒体文件、播放和语音接口。
- `main.py`：组装 FastAPI 应用并提供命令行入口。

应用 lifespan 在启动阶段调用一次曲库扫描。`PUBLIC_BASE_URL` 和 `MUSIC_ROOT` 分别覆盖 YAML 的同名配置，`MUSIC_DIR` 作为兼容旧配置；配置或扫描失败会阻止应用启动。缓存不自动刷新，曲库变更需要重启服务。公开 Track 的 `path` 使用稳定 ID 组成媒体 URL，不暴露文件系统路径。

## 风险与后续

当前没有小爱平台协议适配、用户认证和数据库。服务应只暴露在可信内网；若开放到公网，需要增加认证和请求限流。后续可在不改变查询接口的前提下接入小爱播放调用。
