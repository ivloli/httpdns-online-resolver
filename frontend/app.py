import requests
import streamlit as st

BACKEND = "http://127.0.0.1:8088"


st.set_page_config(page_title="HTTPDNS 在线解析", layout="wide")
st.title("HTTPDNS 在线解析工具")
st.caption("统一使用 /api/resolve 接口")

dispatch_status = {"ok": False, "text": "unknown"}
try:
    health_resp = requests.get(f"{BACKEND}/health", timeout=5)
    health_data = health_resp.json()
    regions = health_data.get("dispatch_regions", {}) if isinstance(health_data, dict) else {}
    global_updated_at = regions.get("global") if isinstance(regions, dict) else None
    if global_updated_at:
        dispatch_status["ok"] = True
        dispatch_status["text"] = f"已完成（global 更新时间戳: {global_updated_at}）"
    else:
        dispatch_status["text"] = "未完成（global 暂无更新时间）"
except Exception as exc:
    dispatch_status["text"] = f"健康检查失败: {exc}"

if dispatch_status["ok"]:
    st.success(f"Dispatch 状态: {dispatch_status['text']}")
else:
    st.warning(f"Dispatch 状态: {dispatch_status['text']}")

col1, col2, col3, col4 = st.columns([2, 2, 1.5, 1])
with col1:
    host = st.text_input("解析域名", value="www.baidu.com", placeholder="请输入解析域名")
with col2:
    cip = st.text_input("客户端IP", value="", placeholder="请输入客户端IP")
with col3:
    resolve_type = st.selectbox("解析类型", ["A", "AAAA", "A+AAAAA"], index=2)
with col4:
    sign_enabled = st.toggle("请求加签", value=True)

if st.button("在线解析", type="primary"):
    params = {
        "host": host.strip(),
        "cip": cip.strip(),
        "resolve_type": resolve_type,
        "sign_enabled": str(sign_enabled).lower(),
    }

    try:
        resp = requests.get(f"{BACKEND}/api/resolve", params=params, timeout=20)
        data = resp.json()
        if not data.get("ok"):
            validation_errors = data.get("validation_errors", {}) if isinstance(data, dict) else {}
            if validation_errors:
                for key, message in validation_errors.items():
                    st.error(f"{key}: {message}")
            else:
                st.error(f"请求失败: {data.get('error', 'unknown error')}")
        else:
            st.success(f"请求完成，耗时 {data.get('elapsed_ms', 0)} ms")
            payload = data.get("data", {})
            display = payload.get("display", {}) if isinstance(payload, dict) else {}
            cache_info = payload.get("cache", {}) if isinstance(payload, dict) else {}

            st.subheader("请求URL")
            request_url = str(display.get("request_url", "") or "")
            st.code(request_url if request_url else "<empty>", language="text")

            st.subheader("解析结果详情")
            summary = display.get("summary", {}) if isinstance(display, dict) else {}
            s1, s2, s3 = st.columns(3)
            with s1:
                st.metric("客户端 IP", str(summary.get("client_ip", "") or ""))
            with s2:
                st.metric("地区", str(summary.get("region", "") or ""))
            with s3:
                st.metric("线路", str(summary.get("line", "") or ""))

            st.subheader("解析结果")
            result_groups = display.get("table_groups", []) if isinstance(display, dict) else []
            if not result_groups:
                st.warning("本次未返回可展示的解析结果")
            else:
                table_rows = []
                for group in result_groups:
                    if not isinstance(group, dict):
                        continue
                    row_items = group.get("rows", []) if isinstance(group.get("rows", []), list) else []
                    if not row_items:
                        row_items = [{"ip": "", "ttl": "", "region": "", "operator": ""}]
                    for index, row_item in enumerate(row_items):
                        if not isinstance(row_item, dict):
                            continue
                        table_rows.append(
                            {
                                "解析域名": str(group.get("domain", "") or "") if index == 0 else "",
                                "解析类型": str(group.get("record_type", "") or "") if index == 0 else "",
                                "IP地址": str(row_item.get("ip", "") or ""),
                                "TTL": str(row_item.get("ttl", "") or ""),
                                "地区": str(row_item.get("region", "") or ""),
                                "线路": str(row_item.get("isp", "") or ""),
                            }
                        )

                st.dataframe(table_rows, width="stretch")

            st.caption(f"缓存命中: {cache_info.get('hit', False)}")

            with st.expander("原始解析结果 JSON"):
                st.json(payload.get("raw_response", payload))
    except Exception as exc:
        st.error(f"请求失败: {exc}")
