# HTTPDNS Online Resolver 后端接口文档

本文档用于前后端联调，接口统一以 `GET /api/resolve` 为主。

## 1. 基础信息

- Base URL（本地）：`http://127.0.0.1:8088`
- Content-Type：`application/json`

### Dispatch 预热与刷新策略

- 服务启动时会先主动请求一次 dispatch（`global`），成功后才对外提供解析服务。
- `/api/resolve` 请求链路只使用内存中的 dispatch 池，不会再触发 dispatch 请求。
- 后台每 1 小时轮询刷新 dispatch 池，用于更新解析接口 host/IP。

## 2. 健康检查

### 2.1 `GET /health`

用于服务可用性探活。

## 3. 在线解析接口

### 3.1 `GET /api/resolve`

按 PRD 约定：

- 解析域名：必填
- 客户端IP：必填
- 解析类型：必填（`A` / `AAAA` / `A+AAAAA`）
- 请求加签：非必填，默认开启

### 3.2 请求参数

| 参数名 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `host` | 是 | string | 解析域名，输入框文案：请输入解析域名 |
| `cip` | 是 | string | 客户端 IP，输入框文案：请输入客户端IP |
| `resolve_type` | 是 | string | 解析类型，枚举：`A` / `AAAA` / `A+AAAAA` |
| `sign_enabled` | 否 | bool-string | 请求加签开关，默认 `true`，可传 `true/false/1/0` |
| `region` | 否 | string | 调度区域，默认 `global` |

说明：

- `resolve_type` 映射规则：
  - `A` -> `q=4`
  - `AAAA` -> `q=6`
  - `A+AAAAA` -> `q=4,6`

请求示例：

```bash
curl -G 'http://127.0.0.1:8088/api/resolve' \
  --data-urlencode 'host=www.baidu.com' \
  --data-urlencode 'cip=111.55.146.208' \
  --data-urlencode 'resolve_type=A+AAAAA' \
  --data-urlencode 'sign_enabled=true'
```

### 3.3 成功响应（示例）

```json
{
  "ok": true,
  "elapsed_ms": 86,
  "data": {
    "display": {
      "request_url": "https://r.dp.dgovl.com/v1/d?id=...&enc=...&sign=...",
      "summary": {
        "client_ip": "1.2.3.4",
        "domain": "www.baidu.com",
        "region": "中国",
        "line": "移动"
      },
      "table_groups": [
        {
          "domain": "www.baidu.com",
          "record_type": "A",
          "rows": [
            {
              "ip": "39.156.70.46",
              "ttl": 120,
              "region": "中国-北京-北京",
              "isp": "移动"
            },
            {
              "ip": "39.156.70.239",
              "ttl": 120,
              "region": "中国-北京-北京",
              "isp": "移动"
            }
          ]
        },
        {
          "domain": "www.baidu.com",
          "record_type": "AAAA",
          "rows": [
            {
              "ip": "240e:e1:8800:aa06:0:ff:b0e1:fe69",
              "ttl": 120,
              "region": "中国-北京-北京",
              "isp": "移动"
            },
            {
              "ip": "240e:e1:8800:aa06:0:ff:b07e:36c5",
              "ttl": 120,
              "region": "中国-北京-北京",
              "isp": "移动"
            }
          ]
        }
      ]
    },
    "raw_response": {
      "answers": [],
      "cip": "1.2.3.4",
      "latency": 0
    },
    "ip2region": {
      "status": {
        "enabled": true,
        "error": "",
        "v4_loaded": true,
        "v6_loaded": true
      },
      "locations": []
    },
    "dispatch": {},
    "cache": {
      "hit": false,
      "cache_key": "..."
    }
  }
}
```

### 3.4 失败响应（必填校验）

HTTP 状态码：`400`

```json
{
  "ok": false,
  "error": "validation failed",
  "validation_errors": {
    "host": "请输入解析域名",
    "cip": "请输入客户端IP",
    "resolve_type": "请选择解析类型"
  },
  "elapsed_ms": 1
}
```

前端可根据 `validation_errors` 对应字段在输入框下方显示红字。

## 4. 字段说明（展示结构）

`data.display.summary`：顶部摘要卡片

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `client_ip` | string | 用户客户端 IP |
| `domain` | string | 解析域名 |
| `region` | string | 用户 IP 地区（建议国家级展示） |
| `line` | string | 用户 IP 线路/运营商 |

`data.display.table_groups`：解析结果分组表

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `domain` | string | 解析域名 |
| `record_type` | string | 记录类型，`A` 或 `AAAA` |
| `rows` | array | 当前组的结果列表 |

`rows[*]` 子字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ip` | string | 返回 IP（IPv4 或 IPv6） |
| `ttl` | number | 该记录 TTL |
| `region` | string | 该 IP 地区，格式 `国家-省份-市` |
| `isp` | string | 该 IP 线路/运营商 |

`data.raw_response`：仅保留原始解析结果（解密后的 `parsed`）

## 5. 前端渲染建议

页面输出区域分三块：

1. 请求 URL：`data.display.request_url`
2. 解析结果详情卡片：`data.display.summary`
   - `client_ip`
   - `domain`
   - `region`
   - `line`
3. 解析结果表格：`data.display.table_groups`
   - 外层：按 `domain + record_type` 分组
   - 内层 `rows`：每行都包含 `ip`、`ttl`、`region`、`isp`
4. 原始 JSON（可折叠）：`data.raw_response` 或完整响应体

## 6. 配置项

配置文件：`backend/config.yaml`

- `httpdns.account_id`
- `httpdns.aes_key`
- `httpdns.sign_key`
- `ip2region.v4_xdb`
- `ip2region.v6_xdb`
- `ip2region.python_path`（可选，默认使用内置 `backend/third_party/ip2region`）
