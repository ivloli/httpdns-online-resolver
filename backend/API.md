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
  "data": {
    "cache": {
      "cache_key": "www.baidu.com|111.55.146.208|global|4,6",
      "expire_at": 1778063792,
      "hit": false,
      "ttl": 120
    },
    "dispatch": {
      "endpoints": [
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": ""
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "8.163.43.38"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "8.163.67.198"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "39.107.68.250"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "39.107.70.115"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "101.37.193.206"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "101.37.195.65"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "118.190.152.160"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "47.103.218.137"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "47.103.219.65"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "47.104.13.65"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "8.148.5.57"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "8.162.7.114"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "8.156.94.192"
        },
        {
          "host": "r.dp.dgovl.com",
          "connect_ip": "8.156.94.201"
        }
      ]
    },
    "display": {
      "request_url": "https://r.dp.dgovl.com/v1/d?enc=89ea61fa18a79c9bf1fbe43a425de175c47343755867a5b1e8b8c26bcfea49ee845f64dec82dd0766ed0986225c3446d87d55172cb9d119874b198152e0739768a428f1753d5de293f1af9f78ef6a44fa9a87f6c5e5355df28a47520dd58b7146317a42543c5b9bccc036f9917b7b80573b99355&id=430992419037876224&sign=8S7yX8ECTpiGUKqEX6WiXmesM7E%3D",
      "summary": {
        "client_ip": "111.55.146.208",
        "domain": "www.baidu.com",
        "line": "CN",
        "region": "中国"
      },
      "table_groups": [
        {
          "domain": "www.baidu.com",
          "record_type": "A",
          "rows": [
            {
              "ip": "39.156.70.239",
              "isp": "移动",
              "region": "中国-北京-北京市",
              "ttl": 120
            },
            {
              "ip": "39.156.70.46",
              "isp": "移动",
              "region": "中国-北京-北京市",
              "ttl": 120
            }
          ]
        },
        {
          "domain": "www.baidu.com",
          "record_type": "AAAA",
          "rows": [
            {
              "ip": "2409:8c00:6c21:118b:0:ff:b0e8:f003",
              "isp": "移动",
              "region": "中国-广东-广州市",
              "ttl": 120
            },
            {
              "ip": "2409:8c00:6c21:11eb:0:ff:b0bf:59ca",
              "isp": "移动",
              "region": "中国-广东-广州市",
              "ttl": 120
            }
          ]
        }
      ]
    },
    "raw_response": {
      "answers": [
        {
          "dn": "www.baidu.com",
          "ttl": 120,
          "v4": {
            "ips": [
              "39.156.70.239",
              "39.156.70.46"
            ],
            "ttl": 120
          },
          "v6": {
            "ips": [
              "2409:8c00:6c21:118b:0:ff:b0e8:f003",
              "2409:8c00:6c21:11eb:0:ff:b0bf:59ca"
            ],
            "ttl": 120
          }
        }
      ],
      "cip": "111.55.146.208",
      "latency": 229
    },
    "request": {
      "plain_payload": "{\"cip\":\"111.55.146.208\",\"dn\":\"www.baidu.com\",\"exp\":1778064270,\"q\":\"4,6\",\"sdns-os\":\"ios\"}"
    }
  },
  "elapsed_ms": 2668,
  "ok": true
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
