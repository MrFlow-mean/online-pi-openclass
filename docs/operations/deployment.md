# OpenClass 线上部署

本文件记录当前生产环境的拓扑与运维命令。密钥只存在于服务器 `/opt/openclass/.env`，不要打印、复制到日志或提交。

## 生产入口

- 域名：`https://open-classes.com`
- 公网 IP：`47.88.9.54`
- 私网 IP：`172.18.55.60`
- 登录：`ssh root@47.88.9.54`
- 反代与证书：Nginx + Certbot，站点配置为 `/etc/nginx/sites-enabled/openclass.conf`。
- 应用目录：`/opt/openclass`
- 当前版本：`/opt/openclass/repo` 是指向 `/opt/openclass/releases/<release-id>` 的符号链接；发布时切换 release，不要在该链接内直接 `git pull`。
- 运行配置：`/opt/openclass/.env`，属主必须是 `root:openclass`，权限必须是 `0640`。systemd 读取该文件后，应用还会以 `openclass` 用户再次读取它，因此不能改成 `0600 root:openclass`。

## 运行拓扑

- `openclass-api.service`：FastAPI，用户 `openclass`，绑定 `127.0.0.1:8000`。
- `openclass-web.service`：Next.js，用户 `openclass`，绑定 `127.0.0.1:3000`。
- `cliproxyapi.service`：Codex OAuth 与 Realtime 转接，用户 `cliproxyapi`，仅绑定 `127.0.0.1:8317`；配置在 `/etc/cliproxyapi/config.yaml`，OAuth 凭据在 `/var/lib/cliproxyapi/auths/`。
- Nginx：监听公网 `80/443`，把网页与 API 请求转发到上述两个本机端口。
- Apache Answer 社区：Docker 容器 `answer-answer-1`，绑定 `127.0.0.1:9080`。
- MySQL：绑定 `127.0.0.1:3306`，供社区服务使用。
- SQLite 主库：`/var/lib/openclass/openclass.sqlite3`，使用 WAL，`busy_timeout=5000`。
- 上传与导出：`/var/lib/openclass/uploads/`、`/var/lib/openclass/exports/`。
- AI 使用日志：`/var/lib/openclass/logs/ai-usage.jsonl`，配置项为 `AI_USAGE_LOG_PATH`。
- Codex 实时语音：对外保留 `gpt-live-1-codex` 别名，`cliproxyapi` 在上游请求中转换为当前 Codex Desktop Realtime v3 模型 `gpt-live-1-boulder-alpha`，并转发 OAuth、DeviceCheck 和 WebRTC SDP。生产 DeviceCheck envelope 保存在 `/etc/cliproxyapi/device-attestation`，属主为 `root:cliproxyapi`、权限 `0640`；代理只在受信任的 `/v1/live` 请求缺少证明时读取并注入，不得写入日志或返回浏览器。
- 拓扑约束：只能有一个后端写入进程；不允许多机或多进程同时写同一 SQLite。

持久化目录和 AI 日志文件必须允许 `openclass` 用户写入：

```bash
install -d -o openclass -g openclass -m 0750 /var/lib/openclass/logs
touch /var/lib/openclass/logs/ai-usage.jsonl
chown openclass:openclass /var/lib/openclass/logs/ai-usage.jsonl
chmod 0640 /var/lib/openclass/logs/ai-usage.jsonl
```

## 发布前检查

在本地仓库根目录运行：

```bash
npm run verify
```

服务器采用 release 目录加原子符号链接切换。新 release 必须完成依赖安装和构建后，才能把 `/opt/openclass/repo` 指向它；不要在运行中的 release 内原地覆盖文件。切换后重启并检查服务：

```bash
ssh root@47.88.9.54
systemctl restart openclass-api openclass-web
systemctl status openclass-api openclass-web --no-pager
ss -ltnp '( sport = :3000 or sport = :8000 )'
```

同步运行配置时先备份服务器文件，安装后的权限保持为 `root:openclass 0640`：

```bash
scp .env root@47.88.9.54:/opt/openclass/.env.incoming
ssh root@47.88.9.54 'cp -a /opt/openclass/.env /opt/openclass/env-backups/.env-before-sync-$(date +%Y%m%d%H%M%S) && install -o root -g openclass -m 0640 /opt/openclass/.env.incoming /opt/openclass/.env && rm /opt/openclass/.env.incoming'
```

生产环境至少应包含以下非密钥配置：

```dotenv
OPENCLASS_PUBLIC_ORIGIN=https://open-classes.com
OPENCLASS_WEB_ORIGIN=https://open-classes.com
OPENCLASS_DATABASE_PATH=/var/lib/openclass/openclass.sqlite3
OPENCLASS_UPLOAD_DIR=/var/lib/openclass/uploads
OPENCLASS_EXPORT_DIR=/var/lib/openclass/exports
AI_USAGE_LOG_PATH=/var/lib/openclass/logs/ai-usage.jsonl
OPENCLASS_REALTIME_ENABLED=true
OPENCLASS_CODEX_REALTIME_ENABLED=true
OPENCLASS_CODEX_REALTIME_PROXY_URL=http://127.0.0.1:8317/v1/live
OPENCLASS_CODEX_REALTIME_PROXY_API_KEY_FILE=/etc/cliproxyapi/api-key
```

`gpt-live-1-codex` 使用 ChatGPT Codex 的内部 Realtime 通道，不是正式 OpenAI API Key 模型。2026-07-27 已使用生产服务器 OAuth、同一 DeviceCheck 证明和两份独立的浏览器 WebRTC offer 完成连续回归：两次上游均返回 `201 Created`，PeerConnection 均达到 `connectionState=connected`、`iceConnectionState=connected`、`signalingState=stable`。此前的 `Voice session access denied` 是旧模型与旧会话结构造成，不能单凭该响应判断账号缺少 entitlement。内部模型、会话 schema 与证明规则都可能随 Codex Desktop 更新；每次升级代理或证明失效后必须重新完成 SDP answer 和 ICE/DTLS 建连回归。正式 OpenAI Realtime 继续作为独立 API Key 路径保留。

## 常用运维

仅重启 API：

```bash
ssh root@47.88.9.54 'systemctl restart openclass-api && systemctl status openclass-api --no-pager'
```

仅重启前端：

```bash
ssh root@47.88.9.54 'systemctl restart openclass-web && systemctl status openclass-web --no-pager'
```

Nginx 配置检查与重载：

```bash
ssh root@47.88.9.54 'nginx -t && systemctl reload nginx'
```

社区容器状态：

```bash
ssh root@47.88.9.54 'docker ps --filter name=answer-answer-1 && curl -fsS http://127.0.0.1:9080/answer/api/v1/siteinfo >/dev/null'
```

## 线上验证

```bash
curl -fsSIL https://open-classes.com/
curl -fsS https://open-classes.com/health
curl -sS -o /dev/null -w 'ai_models=%{http_code}\n' https://open-classes.com/api/ai-models
echo | openssl s_client -servername open-classes.com -connect open-classes.com:443 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

`/api/ai-models` 在未登录请求下可能返回 `401`；完整回归应使用已登录会话检查模型列表。AI 日志修复或迁移后，还要确认运行用户能够写入持久化文件：

```bash
ssh root@47.88.9.54 'runuser -u openclass -- test -w /var/lib/openclass/logs/ai-usage.jsonl'
```

## 查看日志

```bash
ssh root@47.88.9.54 'journalctl -u openclass-api -u openclass-web -n 200 --no-pager'
ssh root@47.88.9.54 'journalctl -u nginx -n 100 --no-pager && tail -n 100 /var/log/nginx/error.log'
```

排查旧版本残留进程时，先核对监听端口、进程工作目录和当前 release；不得按进程名批量结束：

```bash
ssh root@47.88.9.54 'ss -ltnp; readlink -f /opt/openclass/repo'
```

发生 SQLite 写入异常时，先停止唯一写入者并保留数据库、WAL、SHM、日志和上传文件证据，再备份或回滚：

```bash
ssh root@47.88.9.54 'systemctl stop openclass-api'
```
