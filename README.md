# 五核智选 FiveCore（MVP）

个人使用的 A 股五核短线决策辅助终端。当前版本提供手机适配 PWA 界面、FastAPI 服务、可解释规则引擎、SQLite 快照存储和 Docker 部署配置。

> **重要：当前工程没有内置真实行情源。** 页面在收到授权行情适配器推送前明确显示“行情未接入”，不生成实时行情或买入结论。不要将模拟数据用于实盘。行情 API 的使用必须符合数据供应商、交易所及券商授权条款。

## 本地运行

需要 Python 3.11+。

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Windows PowerShell: $env:INGEST_TOKEN="替换为随机长密钥"
# macOS/Linux: export INGEST_TOKEN="替换为随机长密钥"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`。手机与电脑处于同一局域网时，可通过 Windows 电脑局域网 IP 的 `:8000` 访问（需自行配置防火墙）。

## Docker 运行

```bash
cp .env.example .env
# 编辑 .env，设置高强度随机 INGEST_TOKEN
docker compose up -d --build
```

打开 `http://服务器IP:8000`。正式暴露公网前，必须配置 HTTPS、反向代理、访问控制、防火墙和密钥管理；不要裸露 8000 端口。

## 行情推送接口

`POST /api/ingest/market`，请求头 `X-Ingest-Token: <INGEST_TOKEN>`，JSON 示例：

```json
{
  "source": "licensed-provider-name",
  "timestamp": "2026-11-02T09:30:00+08:00",
  "data_quality": "licensed_level1",
  "advancers": 2200,
  "decliners": 2500,
  "unchanged": 300,
  "limit_up": 35,
  "limit_down": 8,
  "broken_limit": 12,
  "yesterday_limit_premium_pct": 0.8,
  "turnover_yi": 4200,
  "turnover_vs_prev_pct": 5.2,
  "indices": {},
  "sectors": []
}
```

以上数值仅为接口格式示例，**不是行情数据**。行情供应商适配器应独立实现，先核验授权、字段定义、交易日历、时区、复权口径、断线重连和数据延迟，再向接口推送。

## API

- `GET /api/health`：服务状态。
- `GET /api/config`：行情接入配置状态。
- `GET /api/market/latest`：最近一条市场快照及市场层规则判断。
- `POST /api/ingest/market`：受密钥保护的市场快照写入。
- `POST /api/analyze/stock`：基于输入证据及最新市场快照进行个股规则检查。

## 当前边界 / 下一阶段

1. 目前是 MVP，不包含已签约的实时行情供应商，也不具备全市场自动扫描。
2. 当前个股检查字段由用户/上游适配器提供；没有提供的证据按未通过处理。
3. 当前规则阈值是透明的初始工程实现，尚未用历史逐笔/分钟数据完成回测校准；不应当视为已经验证的策略参数。
4. 目前没有云账号凭证、域名、行情授权或部署授权，因此没有替用户创建云资源或公网服务。
5. 上线前应补充用户认证、HTTPS、速率限制、审计日志、行情数据质量监控、交易日历、时钟同步、备份和告警。
6. 不包含自动下单。所有输出仅为研究辅助，不构成投资建议。

## 免费运行提示

代码和依赖均可免费使用；运行费用是否为零取决于云平台免费额度、流量、存储、可用区容量和服务条款。免费云资源不保证持续在线。要达到稳定、合规的全市场实时 Level-1，可能需要付费行情授权。
