# OpenClass 线上部署

本文件记录生产环境的拓扑与运维命令。密钥只存在于服务器 `/opt/openclass/.env`，不要打印、复制或提交。

## 生产入口

- 域名：`https://class.bupt8.com`
- 服务器：`188.166.185.136`
- 登录：`ssh root@188.166.185.136`
- 反代与证书：Caddy，配置文件 `/etc/caddy/Caddyfile`，证书自动签发和续期。
- 应用目录：`/opt/openclass`
- Git 源码：`/opt/openclass/repo`，由 `git clone git@github.com:MrFlow-mean/openclass.git` 部署。
- 运行配置：`/opt/openclass/.env`，从本地仓库 `.env` 同步，不要打印或提交密钥。
- 持久化数据：`/opt/openclass/data` 挂载到容器内 `/var/lib/openclass`。
- 数据库：容器内 `/var/lib/openclass/openclass.sqlite3`（WAL，`busy_timeout=5000`）。
- 上传与导出目录放在容器内 `/var/lib/openclass/uploads/`、`/var/lib/openclass/exports/`，不要放进仓库、`.next/` 或临时目录。
- 拓扑约束：单后端写入进程 + 文件级备份 + WAL；不允许多机/多进程同时写同一 sqlite。
- 容器：`openclass-api` 绑定 `127.0.0.1:8000`，`openclass-web` 绑定 `127.0.0.1:3000`。

本地部署前 gate：

```bash
npm run verify
```

同步本地环境变量到服务器：

```bash
scp .env root@188.166.185.136:/opt/openclass/.env
ssh root@188.166.185.136 'chmod 600 /opt/openclass/.env && if grep -q "^OPENCLASS_PUBLIC_ORIGIN=" /opt/openclass/.env; then sed -i "s#^OPENCLASS_PUBLIC_ORIGIN=.*#OPENCLASS_PUBLIC_ORIGIN=https://class.bupt8.com#" /opt/openclass/.env; else printf "\nOPENCLASS_PUBLIC_ORIGIN=https://class.bupt8.com\n" >> /opt/openclass/.env; fi'
```

更新线上代码并重启：

```bash
ssh -A root@188.166.185.136
cd /opt/openclass/repo
git fetch origin main
git checkout main
git pull --ff-only origin main

cd /opt/openclass
docker compose build api web
docker compose up -d
docker compose ps
```

仓库是私有仓库，服务器没有长期保存 GitHub 私钥；需要从本机更新时用 `ssh -A` 走 agent forwarding。若以后改为服务器自主拉取，再单独配置 GitHub deploy key。

仅重启现有容器：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose restart'
```

仅重建前端（改域名或前端环境变量后）：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose build web && docker compose up -d web'
```

Caddy 配置检查与重载：

```bash
ssh root@188.166.185.136 'caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy'
```

线上验证：

```bash
curl -fsSI https://class.bupt8.com/
curl -fsS https://class.bupt8.com/health
curl -fsS https://class.bupt8.com/api/ai-models
echo | openssl s_client -servername class.bupt8.com -connect class.bupt8.com:443 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

查看日志：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose logs --tail=100 api web'
ssh root@188.166.185.136 'journalctl -u caddy -n 100 --no-pager'
```

写入异常先停服务，保留 sqlite、WAL、日志和上传文件证据，再回滚：

```bash
ssh root@188.166.185.136 'cd /opt/openclass && docker compose stop api web'
```
