# 免费云端部署清单（需用户自己的云账号）

当前工具环境没有你的 Oracle Cloud / GitHub / DNS 账号授权，不能替你创建云主机或绑定域名。准备好 Oracle Cloud Always Free 实例后：

1. 创建 Ubuntu LTS VM，使用免费额度内配置；确认所选区域确实有 Always Free 容量。
2. 配置 SSH 密钥，限制 SSH 来源 IP；不要开放数据库端口。
3. 安装 Docker Engine 与 Compose Plugin。
4. 将本项目上传到 VM，复制 `.env.example` 为 `.env` 并生成高强度 `INGEST_TOKEN`。
5. `docker compose up -d --build` 启动服务。
6. 配置 Caddy/Nginx + HTTPS，启用访问认证；公网部署前先完成安全加固。
7. 再接入获得授权的行情数据源。没有数据源时系统保持“行情未接入”。

免费额度、实例容量、网络流量和服务条款可能变化，免费层不保证 SLA。建议先在本地验证，再进行云部署。
