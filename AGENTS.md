## Hindsight 项目记忆
Bank：`headroom-dashboard`（见 `.codex/config.toml` 的 MCP 配置）。
- 开工前：recall 相关背景 / 决策 / 排障
- 学到长期有效信息：retain（决策理由、约束、有效方法、用户修正）
- 记忆与当前仓库冲突时：**以仓库为准**

## 项目：xiaoai-local-music
目标：NAS Docker 上的小爱本地音乐桥接服务
约束：
- 曲库：本地 NAS 目录
- 语音：劫持所有「播放 xxx」（不要求「本地」前缀）
- 镜像：public，ghcr.io/sinclairlin/xiaoai-local-music
- NAS ssh信息：linzx6@10.64.0.1，使用公钥认证
- 默认端口：8123
- NAS 部署目录：/mnt/pool1/home/linzx6/xiaoai-local-music
- 音乐挂载示例：/mnt/pool1/personal/media/音乐 -> /music:ro

## 完成定义（每次改完都要）
1. 触发github actions，构建测试镜像。
2. 等待测试镜像构建完成，ssh 进入 linzx6@10.66.0.3:/home/linzx6/code/xiaoai-local-music，使用公钥认证，并起容器，做基本界面检查
3. 测试机不可用 / 容器启动失败 / 构建失败：明确报告原因，**不算验证完成**
4. 
