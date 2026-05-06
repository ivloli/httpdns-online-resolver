# HTTPDNS Online Resolver

一个本地可运行的在线解析工具：

- 后端：Python Flask（`backend/app.py`）
- 前端：Streamlit（`frontend/app.py`）

后端关键配置在：`backend/config.yaml`。

## 启动后端

```bash
cd backend
pip3 install -r requirements.txt
cp config.example.yaml config.yaml
# 编辑 config.yaml，填写 account_id / aes_key / xdb 路径
python3 app.py
```

默认监听：`http://127.0.0.1:8088`

生产环境建议：

```bash
cd backend
pip3 install -r requirements.txt
gunicorn -w 2 -b 0.0.0.0:8088 wsgi:application
```

## 无 Git 服务器离线部署

在有代码的机器上打包：

```bash
cd /path/to/httpdns-online-resolver
make package
# 如需把 backend/.venv 一起打进包：
# make package-with-venv
```

产物：`dist/httpdns-online-resolver-backend-prod-offline-linux-amd64.tar.gz`

将压缩包上传到目标服务器后：

```bash
cd /opt
tar -xzf httpdns-online-resolver-backend-prod-offline-linux-amd64.tar.gz
mv httpdns-online-resolver-backend-prod-offline-linux-amd64 httpdns-online-resolver
cd /opt/httpdns-online-resolver
cp backend/config.example.yaml backend/config.yaml
# 编辑 backend/config.yaml
make deploy
```

常用命令：

```bash
make status
make logs
make restart
```

### 可选：开启 IP 地区/线路展示（ip2region）

后端支持用 `ip2region xdb` 对解析结果中的 IP 做地区/线路增强（国家-省份-市 + 线路）。

默认使用后端内置的 `third_party/ip2region` Python 包，仅需在 `backend/config.yaml` 配置 `v4_xdb` / `v6_xdb` 路径。

## 启动前端

```bash
cd frontend
pip3 install -r requirements.txt
streamlit run app.py
```

默认页面：`http://127.0.0.1:8501`

在“域名解析”页会新增“地区与线路（IP2Region）”表格，展示每个返回 IP 的：

- 地区（国家-省份-市）
- 线路（运营商）

## 接口

- `GET /health`
- `GET /api/resolve?host=www.baidu.com&q=4,6&cip=1.2.3.4`

`/api/resolve` 的展示字段位于 `data.display`：

- `request_url`
- `summary`
- `table_groups`（按 `domain + record_type` 分组，组内每行包含 `ip/ttl/region/isp`）
