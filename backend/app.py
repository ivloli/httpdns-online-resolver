import json
import importlib
import io
import os
import socket
import ssl
import threading
import time
import urllib.parse
import base64
import hashlib
import hmac
from dataclasses import dataclass
from http.client import HTTPSConnection, HTTPResponse
from typing import Dict, Any, List, Tuple, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Flask, jsonify, request
import yaml


def load_config() -> Dict[str, Any]:
    config_path = os.getenv("APP_CONFIG_PATH", os.path.join(os.path.dirname(__file__), "config.yaml"))
    with open(config_path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return loaded


CFG = load_config()
SERVICE_CFG = CFG.get("service", {})
HTTPDNS_CFG = CFG.get("httpdns", {})
IP2REGION_CFG = CFG.get("ip2region", {})

ACCOUNT_ID = str(HTTPDNS_CFG.get("account_id", "")).strip()
AES_KEY_RAW = str(HTTPDNS_CFG.get("aes_key", "")).strip()
DISPATCH_HOST = str(HTTPDNS_CFG.get("dispatch_host", "r.pp.fgnlo.com")).strip()
RESOLVE_HOST = str(HTTPDNS_CFG.get("resolve_host", "r.dp.dgovl.com")).strip()
TIMEOUT_SECONDS = int(SERVICE_CFG.get("timeout_seconds", 10))
DEFAULT_SDNS_OS = str(HTTPDNS_CFG.get("default_sdns_os", "ios")).strip()
SIGN_KEY = str(HTTPDNS_CFG.get("sign_key", "")).strip()
SIGN_ALGORITHM = str(HTTPDNS_CFG.get("sign_algorithm", "hmac-sha1")).strip()
SIGN_PARAM_NAME = str(HTTPDNS_CFG.get("sign_param_name", "sign")).strip() or "sign"
DISPATCH_REFRESH_INTERVAL_SECONDS = int(SERVICE_CFG.get("dispatch_refresh_interval_seconds", 3600))
IP2REGION_V4_XDB = str(IP2REGION_CFG.get("v4_xdb", "")).strip()
IP2REGION_V6_XDB = str(IP2REGION_CFG.get("v6_xdb", "")).strip()
IP2REGION_PYTHON_PATH = str(IP2REGION_CFG.get("python_path", "")).strip()
IP2REGION_CACHE_POLICY = str(IP2REGION_CFG.get("cache_policy", "vectorIndex")).strip()


dispatch_lock = threading.Lock()
dispatch_pools: Dict[str, List[Tuple[str, str]]] = {}
dispatch_meta: Dict[str, Dict[str, Any]] = {}

cache_lock = threading.Lock()
resolve_cache: Dict[str, Dict[str, Any]] = {}


class IP2RegionResolver:
    def __init__(self, v4_xdb: str, v6_xdb: str, python_path: str, cache_policy: str):
        self._v4 = None
        self._v6 = None
        self._enabled = False
        self._init_error = ""

        try:
            bundled_path = os.path.join(os.path.dirname(__file__), "third_party")
            if bundled_path not in os.sys.path:
                os.sys.path.insert(0, bundled_path)
            if python_path and python_path not in os.sys.path:
                os.sys.path.insert(0, python_path)
            self._ip2util = importlib.import_module("ip2region.util")
            self._ip2searcher = importlib.import_module("ip2region.searcher")
            self._v4 = self._create_searcher(v4_xdb, cache_policy) if v4_xdb else None
            self._v6 = self._create_searcher(v6_xdb, cache_policy) if v6_xdb else None
            self._enabled = self._v4 is not None or self._v6 is not None
            if not self._enabled:
                self._init_error = "ip2region xdb not configured"
        except Exception as error:
            self._enabled = False
            self._init_error = str(error)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "error": self._init_error,
            "v4_loaded": self._v4 is not None,
            "v6_loaded": self._v6 is not None,
        }

    def _create_searcher(self, db_path: str, cache_policy: str):
        handle = io.open(db_path, "rb")
        try:
            header = self._ip2util.load_header(handle)
            version = self._ip2util.version_from_header(header)
            if version is None:
                raise RuntimeError(f"invalid xdb header: {db_path}")

            if cache_policy == "file":
                return self._ip2searcher.new_with_file_only(version, db_path)
            if cache_policy == "vectorIndex":
                vector_index = self._ip2util.load_vector_index(handle)
                return self._ip2searcher.new_with_vector_index(version, db_path, vector_index)
            if cache_policy == "content":
                content_buffer = self._ip2util.load_content(handle)
                return self._ip2searcher.new_with_buffer(version, content_buffer)
            raise RuntimeError(f"unsupported ip2region cache policy: {cache_policy}")
        finally:
            handle.close()

    def lookup(self, ip_text: str) -> Dict[str, str]:
        if not self._enabled:
            return {
                "ip": ip_text,
                "country": "",
                "province": "",
                "city": "",
                "line": "",
                "region_raw": "",
                "error": self._init_error or "ip2region disabled",
            }
        try:
            ip_obj = socket.inet_pton(socket.AF_INET, ip_text)
            searcher = self._v4
        except OSError:
            try:
                socket.inet_pton(socket.AF_INET6, ip_text)
                searcher = self._v6
            except OSError:
                return {
                    "ip": ip_text,
                    "country": "",
                    "province": "",
                    "city": "",
                    "line": "",
                    "region_raw": "",
                    "error": "invalid ip",
                }

        if searcher is None:
            return {
                "ip": ip_text,
                "country": "",
                "province": "",
                "city": "",
                "line": "",
                "region_raw": "",
                "error": "xdb not loaded for this ip version",
            }

        region_raw = searcher.search(ip_text)
        country, province, city, line = parse_region_detail(region_raw)
        return {
            "ip": ip_text,
            "country": country,
            "province": province,
            "city": city,
            "line": line,
            "region_raw": region_raw,
            "error": "",
        }


def _clean_item(value: str) -> str:
    text = str(value or "").strip()
    if text == "0":
        return ""
    return text


def _normalize_province(value: str) -> str:
    text = _clean_item(value)
    if text.endswith("省"):
        text = text[:-1]
    if text.endswith("市"):
        text = text[:-1]
    return text.strip()


def _normalize_city(value: str) -> str:
    return _clean_item(value)


def _normalize_line(value: str) -> str:
    return _clean_item(value)


def _looks_like_country_code(value: str) -> bool:
    text = _clean_item(value)
    return len(text) in {2, 3} and text.isascii() and text.upper() == text and text.isalpha()


def parse_region_detail(region_raw: str) -> Tuple[str, str, str, str]:
    parts = [item.strip() for item in str(region_raw or "").split("|")]
    if len(parts) < 3:
        return "", "", "", ""

    # 常见格式兼容：
    # 1) 中国|0|广东省|广州市|电信
    # 2) 中国|江苏省|南京市|电信|CN
    # 3) United States|California|0|Google LLC|US
    # 4) Australia|Queensland|Brisbane|AU
    country = _clean_item(parts[0])

    # 旧格式：第二段为0，后续分别是省/市/线路
    if len(parts) >= 5 and _clean_item(parts[1]) == "":
        province = _normalize_province(parts[2])
        city = _normalize_city(parts[3])
        line = _normalize_line(parts[4])
        return country, province, city, line

    # 新格式5段：国家|省|市|线路|国家码
    if len(parts) >= 5:
        province = _normalize_province(parts[1])
        city = _normalize_city(parts[2])
        line_candidate = _normalize_line(parts[3])
        # 兜底：若第四段像国家码，则不当作线路
        line = "" if _looks_like_country_code(line_candidate) else line_candidate
        return country, province, city, line

    # 4段：国家|省|市|国家码（通常无线路）
    province = _normalize_province(parts[1])
    city = _normalize_city(parts[2])
    tail = _clean_item(parts[3])
    line = "" if _looks_like_country_code(tail) else _normalize_line(tail)
    return country, province, city, line


def collect_answer_ips(parsed_payload: Any) -> List[str]:
    if not isinstance(parsed_payload, dict):
        return []
    answers = parsed_payload.get("answers", [])
    if not isinstance(answers, list):
        return []
    out: List[str] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        v4 = answer.get("v4") if isinstance(answer.get("v4"), dict) else {}
        v6 = answer.get("v6") if isinstance(answer.get("v6"), dict) else {}
        for ip in v4.get("ips", []):
            ip_text = str(ip).strip()
            if ip_text:
                out.append(ip_text)
        for ip in v6.get("ips", []):
            ip_text = str(ip).strip()
            if ip_text:
                out.append(ip_text)
    return out


def build_result_rows(query_host: str, parsed_payload: Any, ip2region_locations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not isinstance(parsed_payload, dict):
        return []

    location_map: Dict[str, Dict[str, str]] = {}
    for item in ip2region_locations:
        ip_text = str(item.get("ip", "")).strip()
        if ip_text and ip_text not in location_map:
            location_map[ip_text] = item

    answers = parsed_payload.get("answers", [])
    if not isinstance(answers, list):
        return []

    rows: List[Dict[str, Any]] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue

        ttl = int(answer.get("ttl", 0) or 0)
        dn = str(answer.get("dn", "") or query_host)

        for record_type, field in [("A", "v4"), ("AAAA", "v6")]:
            family = answer.get(field, {}) if isinstance(answer.get(field, {}), dict) else {}
            ips = family.get("ips", []) if isinstance(family.get("ips", []), list) else []
            for ip_value in ips:
                ip_text = str(ip_value).strip()
                if not ip_text:
                    continue
                loc = location_map.get(ip_text, {})
                rows.append(
                    {
                        "domain": dn,
                        "record_type": record_type,
                        "ttl": ttl,
                        "answer_ip": ip_text,
                        "country": str(loc.get("country", "") or ""),
                        "province": str(loc.get("province", "") or ""),
                        "city": str(loc.get("city", "") or ""),
                        "region": "-".join([v for v in [loc.get("country", ""), loc.get("province", ""), loc.get("city", "")] if v]),
                        "operator": str(loc.get("line", "") or ""),
                        "region_raw": str(loc.get("region_raw", "") or ""),
                    }
                )
    return rows


def build_result_groups(result_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in result_rows:
        domain = str(row.get("domain", "") or "")
        record_type = str(row.get("record_type", "") or "")
        key = (domain, record_type)

        item = grouped.get(key)
        if item is None:
            item = {
                "domain": domain,
                "record_type": record_type,
                "rows": [],
            }
            grouped[key] = item

        child = {
            "ip": str(row.get("answer_ip", "") or ""),
            "ttl": int(row.get("ttl", 0) or 0),
            "region": str(row.get("region", "") or ""),
            "isp": str(row.get("operator", "") or ""),
        }
        if child["ip"] and child not in item["rows"]:
            item["rows"].append(child)

    return list(grouped.values())


def build_user_context_rows(user_ip: str, query_host: str, resolver: IP2RegionResolver) -> List[Dict[str, str]]:
    info = resolver.lookup(user_ip) if user_ip else {}
    country = str(info.get("country", "") or "")
    province = str(info.get("province", "") or "")
    city = str(info.get("city", "") or "")
    operator = str(info.get("line", "") or "")
    region = "-".join([v for v in [country, province, city] if v])
    return [
        {
            "user_ip": user_ip,
            "domain": query_host,
            "user_ip_region": region,
            "user_ip_region_country": country,
            "user_ip_isp": operator,
            "user_ip_region_raw": str(info.get("region_raw", "") or ""),
        }
    ]


def parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_q_from_resolve_type(resolve_type: str) -> str:
    mapping = {
        "A": "4",
        "AAAA": "6",
        "A+AAAA": "4,6",
        "A+AAAAA": "4,6",
    }
    key = str(resolve_type or "").strip().upper()
    return mapping.get(key, "")


def rfc3986_encode(value: str) -> str:
    return urllib.parse.quote(value, safe="-_.~")


def build_signature(method: str, path: str, query_params: Dict[str, str], sign_key: str, algorithm: str) -> str:
    canonical_pairs = []
    for key in sorted(query_params.keys()):
        canonical_pairs.append(f"{rfc3986_encode(str(key))}={rfc3986_encode(str(query_params[key]))}")
    canonical_query = "&".join(canonical_pairs)
    string_to_sign = f"{method.upper()}&{rfc3986_encode(path)}&{rfc3986_encode(canonical_query)}"
    digestmod = hashlib.sha1 if algorithm == "hmac-sha1" else hashlib.sha256
    digest = hmac.new(sign_key.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod).digest()
    return base64.b64encode(digest).decode("utf-8")


ip2region_resolver = IP2RegionResolver(
    v4_xdb=IP2REGION_V4_XDB,
    v6_xdb=IP2REGION_V6_XDB,
    python_path=IP2REGION_PYTHON_PATH,
    cache_policy=IP2REGION_CACHE_POLICY,
)


class SniHttpsConnection(HTTPSConnection):
    def __init__(self, connect_host: str, server_hostname: str, timeout: int, context: ssl.SSLContext):
        super().__init__(host=connect_host, port=443, timeout=timeout, context=context)
        self._server_hostname_override = server_hostname

    def connect(self) -> None:
        address = (self.host, self.port)
        sock = socket.create_connection(address, self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._context.wrap_socket(sock, server_hostname=self._server_hostname_override)


@dataclass
class HttpResult:
    status: int
    reason: str
    headers: Dict[str, str]
    body: str


def parse_aes_key(raw_key: str, mode: str) -> bytes:
    key_utf8 = raw_key.encode("utf-8")
    if mode == "utf8":
        if len(key_utf8) in {16, 24, 32}:
            return key_utf8
        raise ValueError("AES_KEY invalid for utf8 mode")
    if mode == "hex":
        if len(raw_key) in {32, 48, 64}:
            return bytes.fromhex(raw_key)
        raise ValueError("AES_KEY invalid for hex mode")
    raise ValueError("unknown key mode")


def encrypt_hex(aes_key: bytes, plaintext: bytes) -> str:
    iv = os.urandom(12)
    cipher_text = AESGCM(aes_key).encrypt(iv, plaintext, None)
    return (iv + cipher_text).hex()


def decrypt_hex(aes_key: bytes, hex_data: str) -> str:
    raw = bytes.fromhex(hex_data)
    if len(raw) <= 12:
        raise ValueError("invalid encrypted payload")
    iv = raw[:12]
    cipher_text = raw[12:]
    plaintext = AESGCM(aes_key).decrypt(iv, cipher_text, None)
    return plaintext.decode("utf-8")


def encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            return bytes(out)


def encode_length_delimited(field_number: int, raw: bytes) -> bytes:
    return bytes([(field_number << 3) | 2]) + encode_varint(len(raw)) + raw


def encode_varint_field(field_number: int, value: int) -> bytes:
    return bytes([(field_number << 3) | 0]) + encode_varint(value)


def build_dispatch_proto_plain(region: str, exp_ts: int) -> bytes:
    region_bytes = region.encode("utf-8")
    return encode_length_delimited(1, region_bytes) + encode_varint_field(3, exp_ts)


def make_request(host: str, path_with_query: str, connect_ip: str = "") -> HttpResult:
    target = connect_ip.strip() if connect_ip.strip() else host
    context = ssl.create_default_context()
    conn = SniHttpsConnection(connect_host=target, server_hostname=host, timeout=TIMEOUT_SECONDS, context=context)
    try:
        conn.request("GET", path_with_query, headers={"Host": host})
        response: HTTPResponse = conn.getresponse()
        raw = response.read()
        body = raw.decode("utf-8", errors="replace")
        return HttpResult(
            status=response.status,
            reason=response.reason,
            headers={k: v for k, v in response.getheaders()},
            body=body,
        )
    finally:
        conn.close()


def parse_data_field_and_decrypt(aes_key: bytes, raw_body: str):
    root = json.loads(raw_body)
    data_hex = root.get("data", "")
    if not data_hex:
        raise ValueError("no data field found")
    decrypted = decrypt_hex(aes_key, data_hex)
    parsed = None
    try:
        parsed = json.loads(decrypted)
    except json.JSONDecodeError:
        parsed = None
    return data_hex, decrypted, parsed


def make_cache_key(host: str, cip: str, region: str, q: str) -> str:
    return "|".join([host.strip().lower(), cip.strip(), region.strip().lower(), q.strip()])


def get_cached_result(cache_key: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with cache_lock:
        item = resolve_cache.get(cache_key)
        if not item:
            return None
        if now >= item.get("expire_at", 0):
            resolve_cache.pop(cache_key, None)
            return None
        return item


def set_cached_result(cache_key: str, response_payload: Dict[str, Any], ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    expire_at = time.time() + ttl_seconds
    with cache_lock:
        resolve_cache[cache_key] = {
            "expire_at": expire_at,
            "ttl": ttl_seconds,
            "payload": response_payload,
        }


def extract_ttl_seconds(parsed_payload: Any, request_host: str) -> int:
    if not isinstance(parsed_payload, dict):
        return 0
    answers = parsed_payload.get("answers", [])
    if not isinstance(answers, list):
        return 0

    for answer in answers:
        if not isinstance(answer, dict):
            continue
        dn = str(answer.get("dn", "")).strip().lower()
        if dn == request_host.strip().lower():
            ttl = int(answer.get("ttl", 0) or 0)
            return ttl if ttl > 0 else 0

    if answers and isinstance(answers[0], dict):
        ttl = int(answers[0].get("ttl", 0) or 0)
        return ttl if ttl > 0 else 0

    return 0


def build_resolve_path(host: str, q: str, cip: str, sdns_os: str, sign_enabled: bool) -> Tuple[str, str]:
    content: Dict[str, Any] = {
        "exp": int(time.time()) + 600,
        "dn": host,
        "q": q,
    }
    if cip.strip():
        content["cip"] = cip.strip()
    if sdns_os.strip():
        content["sdns-os"] = sdns_os.strip()

    payload = json.dumps(content, separators=(",", ":")).encode("utf-8")
    aes_key = parse_aes_key(AES_KEY_RAW, "hex")
    enc = encrypt_hex(aes_key, payload)
    query_params = {
        "id": ACCOUNT_ID,
        "enc": enc,
    }
    if sign_enabled:
        if not SIGN_KEY:
            raise ValueError("sign is enabled but sign_key is empty in config")
        signature = build_signature("GET", "/v1/d", query_params, SIGN_KEY, SIGN_ALGORITHM)
        query_params[SIGN_PARAM_NAME] = signature

    query = "&".join([f"{rfc3986_encode(k)}={rfc3986_encode(v)}" for k, v in query_params.items()])
    return f"/v1/d?{query}", payload.decode("utf-8")


def build_dispatch_path(region: str) -> Tuple[str, str]:
    payload = build_dispatch_proto_plain(region, int(time.time()) + 600)
    aes_key = parse_aes_key(AES_KEY_RAW, "utf8")
    enc = encrypt_hex(aes_key, payload)
    encoded_account = urllib.parse.quote(ACCOUNT_ID, safe="")
    encoded_enc = urllib.parse.quote(enc, safe="")
    return f"/dnps-apis/v1/httpdns/endpoints?account_id={encoded_account}&enc={encoded_enc}", f"proto_hex={payload.hex()}"


def extract_dispatch_endpoints(parsed_payload: Dict[str, Any]) -> List[Tuple[str, str]]:
    endpoints: List[Tuple[str, str]] = []
    answers = parsed_payload.get("list", []) if isinstance(parsed_payload, dict) else []
    if not answers:
        return [(RESOLVE_HOST, "")]

    first = answers[0] if isinstance(answers[0], dict) else {}
    domains = first.get("domains", []) if isinstance(first.get("domains", []), list) else []
    ips = first.get("ips", []) if isinstance(first.get("ips", []), list) else []

    for domain in domains:
        domain_text = str(domain).strip()
        if domain_text:
            endpoints.append((domain_text, ""))

    host_for_ip = str(domains[0]).strip() if domains else RESOLVE_HOST
    for ip in ips:
        ip_text = str(ip).strip()
        if ip_text:
            endpoints.append((host_for_ip, ip_text))

    if not endpoints:
        endpoints.append((RESOLVE_HOST, ""))
    return endpoints


def fetch_dispatch_endpoints(region: str) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    path, plain_payload = build_dispatch_path(region)
    dispatch_result = make_request(DISPATCH_HOST, path)
    aes_key_dispatch = parse_aes_key(AES_KEY_RAW, "utf8")
    data_hex, decrypted_payload, parsed = parse_data_field_and_decrypt(aes_key_dispatch, dispatch_result.body)

    parsed_obj = parsed if isinstance(parsed, dict) else {}
    endpoints = extract_dispatch_endpoints(parsed_obj)
    dispatch_debug = {
        "request": {
            "target_host": DISPATCH_HOST,
            "path_with_query": path,
            "plain_payload": plain_payload,
            "key_mode": "utf8",
            "payload_format": "proto",
        },
        "response": {
            "status_code": dispatch_result.status,
            "status_text": dispatch_result.reason,
            "headers": dispatch_result.headers,
            "raw_body": dispatch_result.body,
            "data_hex": data_hex,
            "decrypted_payload": decrypted_payload,
            "parsed": parsed,
        },
        "endpoints": [{"host": host, "connect_ip": ip} for host, ip in endpoints],
    }
    return endpoints, dispatch_debug


def get_or_refresh_dispatch_pool(region: str, force_refresh: bool = False) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    normalized_region = region.strip().lower() or "global"
    if not force_refresh:
        with dispatch_lock:
            existing = dispatch_pools.get(normalized_region)
            meta = dispatch_meta.get(normalized_region)
            if existing:
                return existing, meta or {}

    endpoints, debug = fetch_dispatch_endpoints(normalized_region)
    with dispatch_lock:
        dispatch_pools[normalized_region] = endpoints
        dispatch_meta[normalized_region] = {
            "updated_at": int(time.time()),
            "debug": debug,
        }
    return endpoints, debug


def get_dispatch_pool_snapshot(region: str) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    normalized_region = region.strip().lower() or "global"
    with dispatch_lock:
        existing = dispatch_pools.get(normalized_region)
        meta = dispatch_meta.get(normalized_region)
        if existing:
            return existing, (meta or {}).get("debug", {})

        # fallback 到 global，避免请求链路触发 dispatch
        global_existing = dispatch_pools.get("global")
        global_meta = dispatch_meta.get("global")
        if global_existing:
            return global_existing, (global_meta or {}).get("debug", {})

    raise RuntimeError("dispatch pool is empty, wait for startup preload or refresh cycle")


def dispatch_refresh_loop() -> None:
    while True:
        time.sleep(DISPATCH_REFRESH_INTERVAL_SECONDS)
        with dispatch_lock:
            regions = list(dispatch_pools.keys())
        if "global" not in regions:
            regions.append("global")
        for region in regions:
            try:
                get_or_refresh_dispatch_pool(region, force_refresh=True)
            except Exception:
                pass


app = Flask(__name__)


def start_background_jobs() -> None:
    if getattr(start_background_jobs, "_started", False):
        return
    setattr(start_background_jobs, "_started", True)
    # 启动阶段必须先拿到至少一份 dispatch 池，避免线上首个请求触发 dispatch。
    last_error: Optional[str] = None
    for _ in range(3):
        try:
            get_or_refresh_dispatch_pool("global", force_refresh=True)
            last_error = None
            break
        except Exception as error:
            last_error = str(error)
            time.sleep(1)
    if last_error is not None:
        raise RuntimeError(f"startup dispatch preload failed: {last_error}")
    threading.Thread(target=dispatch_refresh_loop, daemon=True).start()


@app.get("/health")
def health():
    with dispatch_lock:
        region_status = {k: v.get("updated_at") for k, v in dispatch_meta.items()}
    with cache_lock:
        cache_size = len(resolve_cache)
    return jsonify(
        {
            "ok": True,
            "status": "ok",
            "dispatch_regions": region_status,
            "cache_size": cache_size,
            "ip2region": ip2region_resolver.status(),
        }
    )


@app.get("/api/resolve")
def resolve_api():
    start = time.time()
    try:
        host = (request.args.get("host") or "").strip()
        cip = (request.args.get("cip") or "").strip()
        resolve_type = (request.args.get("resolve_type") or request.args.get("type") or "").strip()
        q = (request.args.get("q") or "").strip()
        region = (request.args.get("region") or "global").strip()
        sign_enabled = parse_bool(request.args.get("sign_enabled"), default=True)
        sdns_os = DEFAULT_SDNS_OS
        user_ip = cip

        if not q and resolve_type:
            q = parse_q_from_resolve_type(resolve_type)

        validation_errors: Dict[str, str] = {}
        if not host:
            validation_errors["host"] = "请输入解析域名"
        if not cip:
            validation_errors["cip"] = "请输入客户端IP"
        if not resolve_type and not q:
            validation_errors["resolve_type"] = "请选择解析类型"
        if resolve_type and not parse_q_from_resolve_type(resolve_type):
            validation_errors["resolve_type"] = "解析类型仅支持 A / AAAA / A+AAAAA"
        if q and q not in {"4", "6", "4,6"}:
            validation_errors["q"] = "q 仅支持 4 / 6 / 4,6"
        if validation_errors:
            return jsonify({"ok": False, "error": "validation failed", "validation_errors": validation_errors, "elapsed_ms": int((time.time() - start) * 1000)}), 400

        if not resolve_type:
            resolve_type = "A+AAAAA" if q == "4,6" else ("A" if q == "4" else "AAAA")

        cache_key = make_cache_key(host, cip, region, q)
        cached = get_cached_result(cache_key)
        if cached:
            payload = dict(cached["payload"])
            payload["cache"] = {
                "hit": True,
                "ttl": cached.get("ttl", 0),
                "expire_at": int(cached.get("expire_at", 0)),
                "cache_key": cache_key,
            }
            return jsonify(
                {
                    "ok": True,
                    "elapsed_ms": int((time.time() - start) * 1000),
                    "data": payload,
                }
            )

        resolve_path, plain_payload = build_resolve_path(host, q, cip, sdns_os, sign_enabled=sign_enabled)
        endpoints, dispatch_debug = get_dispatch_pool_snapshot(region)

        result: Optional[HttpResult] = None
        used_endpoint: Dict[str, str] = {"host": "", "connect_ip": ""}
        last_error: Optional[str] = None
        for endpoint_host, endpoint_ip in endpoints:
            try:
                result = make_request(endpoint_host, resolve_path, connect_ip=endpoint_ip)
                used_endpoint = {"host": endpoint_host, "connect_ip": endpoint_ip}
                break
            except Exception as req_error:
                last_error = str(req_error)

        if result is None:
            raise RuntimeError(f"resolve request failed on all dispatch endpoints: {last_error}")

        aes_key_resolve = parse_aes_key(AES_KEY_RAW, "hex")
        data_hex, decrypted_payload, parsed = parse_data_field_and_decrypt(aes_key_resolve, result.body)

        locations = [ip2region_resolver.lookup(ip) for ip in collect_answer_ips(parsed)]
        result_rows = build_result_rows(query_host=host, parsed_payload=parsed, ip2region_locations=locations)
        result_groups = build_result_groups(result_rows)
        user_context_rows = build_user_context_rows(user_ip=user_ip, query_host=host, resolver=ip2region_resolver)

        response_payload = {
                    "request": {
                        "target_host": used_endpoint["host"],
                        "target_connect_ip": used_endpoint["connect_ip"],
                        "path_with_query": resolve_path,
                        "plain_payload": plain_payload,
                        "request_cip": cip,
                        "request_sdns_os": sdns_os,
                        "user_ip": user_ip,
                        "resolve_type": resolve_type,
                        "sign_enabled": sign_enabled,
                        "key_mode": "hex",
                        "payload_format": "json",
                        "request_url": f"https://{used_endpoint['host']}{resolve_path}",
                    },
            "response": {
                "status_code": result.status,
                "status_text": result.reason,
                "headers": result.headers,
                "raw_body": result.body,
                "data_hex": data_hex,
                "decrypted_payload": decrypted_payload,
                "parsed": parsed,
            },
            "dispatch": dispatch_debug,
            "ip2region": {
                "status": ip2region_resolver.status(),
                "locations": locations,
            },
            "display": {
                "request_url": f"https://{used_endpoint['host']}{resolve_path}",
                "summary": {
                    "client_ip": user_ip,
                    "domain": host,
                    "region": user_context_rows[0].get("user_ip_region_country", "") if user_context_rows else "",
                    "line": user_context_rows[0].get("user_ip_isp", "") if user_context_rows else "",
                },
                "table_groups": result_groups,
            },
            "raw_response": parsed if isinstance(parsed, dict) else {},
            "cache": {
                "hit": False,
                "cache_key": cache_key,
            },
        }

        ttl_seconds = extract_ttl_seconds(parsed, host)
        if ttl_seconds > 0:
            set_cached_result(cache_key, response_payload, ttl_seconds)
            response_payload["cache"]["ttl"] = ttl_seconds
            response_payload["cache"]["expire_at"] = int(time.time() + ttl_seconds)

        return jsonify(
            {
                "ok": True,
                "elapsed_ms": int((time.time() - start) * 1000),
                "data": response_payload,
            }
        )
    except Exception as error:
        return jsonify({"ok": False, "error": str(error), "elapsed_ms": int((time.time() - start) * 1000)}), 500


@app.get("/api/dispatch")
def dispatch_api():
    start = time.time()
    try:
        region = (request.args.get("region") or "global").strip()
        endpoints, dispatch_debug = get_or_refresh_dispatch_pool(region, force_refresh=True)

        return jsonify(
            {
                "ok": True,
                "elapsed_ms": int((time.time() - start) * 1000),
                "data": {
                    "dispatch": dispatch_debug,
                    "endpoints": [{"host": host, "connect_ip": ip} for host, ip in endpoints],
                },
            }
        )
    except Exception as error:
        return jsonify({"ok": False, "error": str(error), "elapsed_ms": int((time.time() - start) * 1000)}), 500


if __name__ == "__main__":
    if not ACCOUNT_ID or not AES_KEY_RAW:
        raise RuntimeError("account_id or aes_key is empty, please check backend/config.yaml")
    start_background_jobs()
    app.run(
        host=str(SERVICE_CFG.get("host", "127.0.0.1")),
        port=int(SERVICE_CFG.get("port", 8088)),
        debug=False,
    )
