package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/lionsoul2014/ip2region/binding/golang/xdb"
	"gopkg.in/yaml.v3"
)

type Config struct {
	Service struct {
		Host                          string `yaml:"host"`
		Port                          int    `yaml:"port"`
		TimeoutSeconds                int    `yaml:"timeout_seconds"`
		DispatchRefreshIntervalSecond int    `yaml:"dispatch_refresh_interval_seconds"`
	} `yaml:"service"`
	HTTPDNS struct {
		AccountID    string `yaml:"account_id"`
		AESKey       string `yaml:"aes_key"`
		SignKey      string `yaml:"sign_key"`
		SignAlg      string `yaml:"sign_algorithm"`
		SignParam    string `yaml:"sign_param_name"`
		DispatchHost string `yaml:"dispatch_host"`
		ResolveHost  string `yaml:"resolve_host"`
		DefaultOS    string `yaml:"default_sdns_os"`
	} `yaml:"httpdns"`
	IP2Region struct {
		V4XDB string `yaml:"v4_xdb"`
		V6XDB string `yaml:"v6_xdb"`
	} `yaml:"ip2region"`
}

type DispatchMeta struct {
	UpdatedAt int64       `json:"updated_at"`
	Debug     interface{} `json:"debug"`
}

type App struct {
	cfg Config

	v4Searcher *xdb.Searcher
	v6Searcher *xdb.Searcher

	dispatchMu    sync.RWMutex
	dispatchPools map[string][]Endpoint
	dispatchMeta  map[string]DispatchMeta

	cacheMu      sync.RWMutex
	resolveCache map[string]CachedEntry

	httpClient *http.Client
}

type Endpoint struct {
	Host      string `json:"host"`
	ConnectIP string `json:"connect_ip"`
}

type CachedEntry struct {
	ExpireAt int64                  `json:"expire_at"`
	TTL      int                    `json:"ttl"`
	Payload  map[string]interface{} `json:"payload"`
}

func main() {
	cfgPath := os.Getenv("APP_CONFIG_PATH")
	if cfgPath == "" {
		cfgPath = filepath.Join(".", "config.yaml")
	}
	cfg, err := loadConfig(cfgPath)
	if err != nil {
		log.Fatalf("load config: %v", err)
	}
	app, err := newApp(cfg)
	if err != nil {
		log.Fatalf("init app: %v", err)
	}
	if err := app.preloadDispatch(); err != nil {
		log.Fatalf("preload dispatch failed: %v", err)
	}
	go app.dispatchRefreshLoop()

	mux := http.NewServeMux()
	// API paths (no backward compatibility)
	mux.HandleFunc("/dnps-apis/v1/online-experience/health", app.health)
	mux.HandleFunc("/dnps-apis/v1/online-experience/resolve", app.resolve)

	addr := net.JoinHostPort(cfg.Service.Host, strconv.Itoa(cfg.Service.Port))
	log.Printf("backend-go listening on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}

func loadConfig(path string) (Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	var cfg Config
	if err := yaml.Unmarshal(b, &cfg); err != nil {
		return Config{}, err
	}
	if cfg.Service.Port == 0 {
		cfg.Service.Port = 8088
	}
	if cfg.Service.TimeoutSeconds <= 0 {
		cfg.Service.TimeoutSeconds = 10
	}
	if cfg.Service.DispatchRefreshIntervalSecond <= 0 {
		cfg.Service.DispatchRefreshIntervalSecond = 3600
	}
	if cfg.HTTPDNS.SignAlg == "" {
		cfg.HTTPDNS.SignAlg = "hmac-sha1"
	}
	if cfg.HTTPDNS.SignParam == "" {
		cfg.HTTPDNS.SignParam = "sign"
	}
	if cfg.Service.Host == "" {
		cfg.Service.Host = "127.0.0.1"
	}
	return cfg, nil
}

func newApp(cfg Config) (*App, error) {
	app := &App{
		cfg:          cfg,
		dispatchPools: map[string][]Endpoint{},
		dispatchMeta:  map[string]DispatchMeta{},
		resolveCache:  map[string]CachedEntry{},
		httpClient: &http.Client{Timeout: time.Duration(cfg.Service.TimeoutSeconds) * time.Second},
	}
	if cfg.IP2Region.V4XDB != "" {
		s, err := loadSearcher(cfg.IP2Region.V4XDB, xdb.IPv4)
		if err != nil {
			return nil, err
		}
		app.v4Searcher = s
	}
	if cfg.IP2Region.V6XDB != "" {
		s, err := loadSearcher(cfg.IP2Region.V6XDB, xdb.IPv6)
		if err != nil {
			return nil, err
		}
		app.v6Searcher = s
	}
	return app, nil
}

func loadSearcher(path string, version *xdb.Version) (*xdb.Searcher, error) {
	cBuff, err := xdb.LoadContentFromFile(path)
	if err != nil {
		return nil, err
	}
	return xdb.NewWithBuffer(version, cBuff)
}

func (a *App) health(w http.ResponseWriter, r *http.Request) {
	a.dispatchMu.RLock()
	regions := map[string]int64{}
	for k, v := range a.dispatchMeta {
		regions[k] = v.UpdatedAt
	}
	a.dispatchMu.RUnlock()

	a.cacheMu.RLock()
	cacheSize := len(a.resolveCache)
	a.cacheMu.RUnlock()

	writeJSON(w, 200, map[string]interface{}{
		"ok":               true,
		"status":           "ok",
		"dispatch_regions": regions,
		"cache_size":       cacheSize,
	})
}

func (a *App) resolve(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	host := strings.TrimSpace(r.URL.Query().Get("host"))
	cip := strings.TrimSpace(r.URL.Query().Get("cip"))
	resolveType := strings.TrimSpace(r.URL.Query().Get("resolve_type"))
	q := strings.TrimSpace(r.URL.Query().Get("q"))
	region := strings.TrimSpace(r.URL.Query().Get("region"))
	if region == "" {
		region = "global"
	}
	signEnabled := parseBool(r.URL.Query().Get("sign_enabled"), true)

	if q == "" && resolveType != "" {
		q = parseQ(resolveType)
	}
	errs := map[string]string{}
	if host == "" { errs["host"] = "请输入解析域名" }
	if cip == "" { errs["cip"] = "请输入客户端IP" }
	if resolveType == "" && q == "" { errs["resolve_type"] = "请选择解析类型" }
	if resolveType != "" && parseQ(resolveType) == "" { errs["resolve_type"] = "解析类型仅支持 A / AAAA / A+AAAAA" }
	if q != "" && q != "4" && q != "6" && q != "4,6" { errs["q"] = "q 仅支持 4 / 6 / 4,6" }
	if len(errs) > 0 {
		writeJSON(w, 400, map[string]interface{}{"ok": false, "error": "validation failed", "validation_errors": errs, "elapsed_ms": elapsedMs(start)})
		return
	}

	cacheKey := strings.Join([]string{strings.ToLower(host), cip, strings.ToLower(region), q}, "|")
	if payload, ok := a.getCached(cacheKey); ok {
		payload["cache"] = map[string]interface{}{"hit": true, "ttl": payload["cache"].(map[string]interface{})["ttl"], "expire_at": payload["cache"].(map[string]interface{})["expire_at"], "cache_key": cacheKey}
		writeJSON(w, 200, map[string]interface{}{"ok": true, "elapsed_ms": elapsedMs(start), "data": payload})
		return
	}

	resolvePath, plainPayload, err := a.buildResolvePath(host, q, cip, signEnabled)
	if err != nil {
		writeJSON(w, 500, map[string]interface{}{"ok": false, "error": err.Error(), "elapsed_ms": elapsedMs(start)})
		return
	}
	endpoints, dispatchDebug, err := a.getDispatchSnapshot(region)
	if err != nil {
		writeJSON(w, 500, map[string]interface{}{"ok": false, "error": err.Error(), "elapsed_ms": elapsedMs(start)})
		return
	}

	var rawBody string
	used := Endpoint{}
	for _, ep := range endpoints {
		b, err := a.request(ep.Host, resolvePath, ep.ConnectIP)
		if err == nil {
			rawBody = b
			used = ep
			break
		}
	}
	if rawBody == "" {
		writeJSON(w, 500, map[string]interface{}{"ok": false, "error": "resolve request failed on all dispatch endpoints", "elapsed_ms": elapsedMs(start)})
		return
	}

	parsed, err := a.parseAndDecrypt(rawBody, false)
	if err != nil {
		writeJSON(w, 500, map[string]interface{}{"ok": false, "error": err.Error(), "elapsed_ms": elapsedMs(start)})
		return
	}
	rows := a.buildRows(host, parsed)
	groups := buildGroups(rows)

	country, _, _, isp, _ := a.lookupRegion(cip)
	payload := map[string]interface{}{
		"display": map[string]interface{}{
			"request_url": "https://" + used.Host + resolvePath,
			"summary": map[string]interface{}{"client_ip": cip, "domain": host, "region": country, "line": isp},
			"table_groups": groups,
		},
		"raw_response": parsed,
		"cache": map[string]interface{}{"hit": false, "cache_key": cacheKey},
		"dispatch": dispatchDebug,
		"request": map[string]interface{}{"plain_payload": plainPayload},
	}

	ttl := extractTTL(parsed, host)
	if ttl > 0 {
		exp := time.Now().Add(time.Duration(ttl) * time.Second).Unix()
		payload["cache"] = map[string]interface{}{"hit": false, "cache_key": cacheKey, "ttl": ttl, "expire_at": exp}
		a.setCached(cacheKey, payload, ttl)
	}

	writeJSON(w, 200, map[string]interface{}{"ok": true, "elapsed_ms": elapsedMs(start), "data": payload})
}

func (a *App) parseAndDecrypt(rawBody string, dispatch bool) (map[string]interface{}, error) {
	var root map[string]interface{}
	if err := json.Unmarshal([]byte(rawBody), &root); err != nil { return nil, err }
	dataHex, _ := root["data"].(string)
	if dataHex == "" { return nil, errors.New("no data field found") }
	key, err := parseAESKey(a.cfg.HTTPDNS.AESKey, map[bool]string{true: "utf8", false: "hex"}[dispatch])
	if err != nil { return nil, err }
	plain, err := decryptHex(key, dataHex)
	if err != nil { return nil, err }
	var parsed map[string]interface{}
	if err := json.Unmarshal([]byte(plain), &parsed); err != nil { return map[string]interface{}{}, nil }
	return parsed, nil
}

func (a *App) buildResolvePath(host, q, cip string, signEnabled bool) (string, string, error) {
	content := map[string]interface{}{"exp": time.Now().Unix() + 600, "dn": host, "q": q, "sdns-os": a.cfg.HTTPDNS.DefaultOS}
	if cip != "" { content["cip"] = cip }
	payloadBytes, _ := json.Marshal(content)
	key, err := parseAESKey(a.cfg.HTTPDNS.AESKey, "hex")
	if err != nil { return "", "", err }
	enc, err := encryptHex(key, payloadBytes)
	if err != nil { return "", "", err }
	qv := map[string]string{"id": a.cfg.HTTPDNS.AccountID, "enc": enc}
	if signEnabled {
		if a.cfg.HTTPDNS.SignKey == "" { return "", "", errors.New("sign is enabled but sign_key is empty in config") }
		qv[a.cfg.HTTPDNS.SignParam] = sign("GET", "/v1/d", qv, a.cfg.HTTPDNS.SignKey, a.cfg.HTTPDNS.SignAlg)
	}
	keys := make([]string, 0, len(qv)); for k := range qv { keys = append(keys, k) }; sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys { parts = append(parts, rfc3986(k)+"="+rfc3986(qv[k])) }
	return "/v1/d?" + strings.Join(parts, "&"), string(payloadBytes), nil
}

func (a *App) buildDispatchPath(region string) (string, error) {
	payload := buildDispatchProto(region, time.Now().Unix()+600)
	key, err := parseAESKey(a.cfg.HTTPDNS.AESKey, "utf8")
	if err != nil { return "", err }
	enc, err := encryptHex(key, payload)
	if err != nil { return "", err }
	return "/dnps-apis/v1/httpdns/endpoints?account_id=" + url.QueryEscape(a.cfg.HTTPDNS.AccountID) + "&enc=" + url.QueryEscape(enc), nil
}

func (a *App) request(host, path, connectIP string) (string, error) {
	target := host
	if strings.TrimSpace(connectIP) != "" { target = strings.TrimSpace(connectIP) }
	u := "https://" + target + path
	req, _ := http.NewRequest(http.MethodGet, u, nil)
	req.Host = host
	resp, err := a.httpClient.Do(req)
	if err != nil { return "", err }
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return string(b), nil
}

func (a *App) preloadDispatch() error {
	for i := 0; i < 3; i++ {
		if _, _, err := a.refreshDispatch("global"); err == nil { return nil }
		time.Sleep(time.Second)
	}
	return errors.New("startup dispatch preload failed")
}

func (a *App) dispatchRefreshLoop() {
	ticker := time.NewTicker(time.Duration(a.cfg.Service.DispatchRefreshIntervalSecond) * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		a.dispatchMu.RLock(); regions := make([]string, 0, len(a.dispatchPools)); for k := range a.dispatchPools { regions = append(regions, k) }; a.dispatchMu.RUnlock()
		if len(regions) == 0 { regions = []string{"global"} }
		if !contains(regions, "global") { regions = append(regions, "global") }
		for _, r := range regions { _, _, _ = a.refreshDispatch(r) }
	}
}

func (a *App) refreshDispatch(region string) ([]Endpoint, interface{}, error) {
	path, err := a.buildDispatchPath(strings.ToLower(strings.TrimSpace(region)))
	if err != nil { return nil, nil, err }
	body, err := a.request(a.cfg.HTTPDNS.DispatchHost, path, "")
	if err != nil { return nil, nil, err }
	parsed, err := a.parseAndDecrypt(body, true)
	if err != nil { return nil, nil, err }
	endpoints := extractDispatchEndpoints(parsed, a.cfg.HTTPDNS.ResolveHost)
	debug := map[string]interface{}{"endpoints": endpoints}
	a.dispatchMu.Lock()
	a.dispatchPools[strings.ToLower(strings.TrimSpace(region))] = endpoints
	a.dispatchMeta[strings.ToLower(strings.TrimSpace(region))] = DispatchMeta{UpdatedAt: time.Now().Unix(), Debug: debug}
	a.dispatchMu.Unlock()
	return endpoints, debug, nil
}

func (a *App) getDispatchSnapshot(region string) ([]Endpoint, interface{}, error) {
	k := strings.ToLower(strings.TrimSpace(region))
	if k == "" { k = "global" }
	a.dispatchMu.RLock()
	if eps, ok := a.dispatchPools[k]; ok { dbg := a.dispatchMeta[k].Debug; a.dispatchMu.RUnlock(); return eps, dbg, nil }
	if eps, ok := a.dispatchPools["global"]; ok { dbg := a.dispatchMeta["global"].Debug; a.dispatchMu.RUnlock(); return eps, dbg, nil }
	a.dispatchMu.RUnlock()
	return nil, nil, errors.New("dispatch pool is empty, wait for startup preload or refresh cycle")
}

func extractDispatchEndpoints(parsed map[string]interface{}, fallback string) []Endpoint {
	arr, _ := parsed["list"].([]interface{})
	if len(arr) == 0 { return []Endpoint{{Host: fallback, ConnectIP: ""}} }
	first, _ := arr[0].(map[string]interface{})
	var out []Endpoint
	var firstDomain string
	if ds, ok := first["domains"].([]interface{}); ok {
		for _, d := range ds { s := strings.TrimSpace(fmt.Sprint(d)); if s != "" { if firstDomain == "" { firstDomain = s }; out = append(out, Endpoint{Host: s}) } }
	}
	if firstDomain == "" { firstDomain = fallback }
	if ips, ok := first["ips"].([]interface{}); ok {
		for _, ip := range ips { s := strings.TrimSpace(fmt.Sprint(ip)); if s != "" { out = append(out, Endpoint{Host: firstDomain, ConnectIP: s}) } }
	}
	if len(out) == 0 { out = append(out, Endpoint{Host: fallback}) }
	return out
}

func (a *App) buildRows(host string, parsed map[string]interface{}) []map[string]interface{} {
	answers, _ := parsed["answers"].([]interface{})
	rows := []map[string]interface{}{}
	for _, ans := range answers {
		m, _ := ans.(map[string]interface{})
		dn := strings.TrimSpace(fmt.Sprint(m["dn"]))
		if dn == "" { dn = host }
		ttl := int(toFloat(m["ttl"]))
		for _, entry := range []struct{ key, rt string }{{"v4", "A"}, {"v6", "AAAA"}} {
			fam, _ := m[entry.key].(map[string]interface{})
			ips, _ := fam["ips"].([]interface{})
			for _, ipi := range ips {
				ip := strings.TrimSpace(fmt.Sprint(ipi))
				c, p, city, isp, _ := a.lookupRegion(ip)
				region := strings.Trim(strings.Join([]string{c, p, city}, "-"), "-")
				rows = append(rows, map[string]interface{}{"domain": dn, "record_type": entry.rt, "answer_ip": ip, "ttl": ttl, "region": region, "operator": isp})
			}
		}
	}
	return rows
}

func buildGroups(rows []map[string]interface{}) []map[string]interface{} {
	type key struct{ d, t string }
	m := map[key]map[string]interface{}{}
	for _, r := range rows {
		k := key{fmt.Sprint(r["domain"]), fmt.Sprint(r["record_type"])}
		g, ok := m[k]
		if !ok {
			g = map[string]interface{}{"domain": k.d, "record_type": k.t, "rows": []map[string]interface{}{}}
			m[k] = g
		}
		child := map[string]interface{}{"ip": fmt.Sprint(r["answer_ip"]), "ttl": int(toFloat(r["ttl"])), "region": fmt.Sprint(r["region"]), "isp": fmt.Sprint(r["operator"])}
		g["rows"] = append(g["rows"].([]map[string]interface{}), child)
	}
	keys := make([]key, 0, len(m)); for k := range m { keys = append(keys, k) }
	sort.Slice(keys, func(i, j int) bool { if keys[i].d == keys[j].d { return keys[i].t < keys[j].t }; return keys[i].d < keys[j].d })
	out := make([]map[string]interface{}, 0, len(keys))
	for _, k := range keys { out = append(out, m[k]) }
	return out
}

func (a *App) lookupRegion(ip string) (country, province, city, isp, raw string) {
	raw = ""
	if strings.Contains(ip, ":") {
		if a.v6Searcher == nil { return }
		r, err := a.v6Searcher.Search(ip)
		if err != nil { return }
		raw = r
	} else {
		if a.v4Searcher == nil { return }
		r, err := a.v4Searcher.Search(ip)
		if err != nil { return }
		raw = r
	}
	parts := strings.Split(raw, "|")
	if len(parts) < 3 { return }
	clean := func(s string) string { s = strings.TrimSpace(s); if s == "0" { return "" }; return s }
	normP := func(s string) string { s = clean(s); s = strings.TrimSuffix(s, "省"); s = strings.TrimSuffix(s, "市"); return strings.TrimSpace(s) }
	normI := func(s string) string { s = clean(s); s = strings.ReplaceAll(s, "中国", ""); s = strings.ReplaceAll(s, "云", ""); return strings.TrimSpace(s) }
	country = clean(parts[0])
	if len(parts) >= 5 && clean(parts[1]) == "" {
		province = normP(parts[2]); city = clean(parts[3]); isp = normI(parts[4]); return
	}
	if len(parts) >= 5 {
		province = normP(parts[1]); city = clean(parts[2]); isp = normI(parts[3]); return
	}
	if len(parts) == 4 {
		province = normP(parts[1]); city = clean(parts[2]); return
	}
	return
}

func extractTTL(parsed map[string]interface{}, host string) int {
	answers, _ := parsed["answers"].([]interface{})
	h := strings.ToLower(strings.TrimSpace(host))
	for _, a := range answers {
		m, _ := a.(map[string]interface{})
		dn := strings.ToLower(strings.TrimSpace(fmt.Sprint(m["dn"])))
		if dn == h {
			ttl := int(toFloat(m["ttl"]))
			if ttl > 0 { return ttl }
		}
	}
	if len(answers) > 0 {
		if m, ok := answers[0].(map[string]interface{}); ok {
			ttl := int(toFloat(m["ttl"]))
			if ttl > 0 { return ttl }
		}
	}
	return 0
}

func (a *App) getCached(key string) (map[string]interface{}, bool) {
	a.cacheMu.RLock(); entry, ok := a.resolveCache[key]; a.cacheMu.RUnlock()
	if !ok { return nil, false }
	if time.Now().Unix() >= entry.ExpireAt {
		a.cacheMu.Lock(); delete(a.resolveCache, key); a.cacheMu.Unlock()
		return nil, false
	}
	return cloneMap(entry.Payload), true
}

func (a *App) setCached(key string, payload map[string]interface{}, ttl int) {
	if ttl <= 0 { return }
	a.cacheMu.Lock()
	a.resolveCache[key] = CachedEntry{ExpireAt: time.Now().Add(time.Duration(ttl) * time.Second).Unix(), TTL: ttl, Payload: cloneMap(payload)}
	a.cacheMu.Unlock()
}

func cloneMap(in map[string]interface{}) map[string]interface{} {
	b, _ := json.Marshal(in)
	out := map[string]interface{}{}
	_ = json.Unmarshal(b, &out)
	return out
}

func parseAESKey(raw, mode string) ([]byte, error) {
	utf8 := []byte(raw)
	switch mode {
	case "utf8":
		if l := len(utf8); l == 16 || l == 24 || l == 32 { return utf8, nil }
		return nil, errors.New("AES_KEY invalid for utf8 mode")
	case "hex":
		if l := len(raw); l == 32 || l == 48 || l == 64 { return hex.DecodeString(raw) }
		return nil, errors.New("AES_KEY invalid for hex mode")
	default:
		return nil, errors.New("unknown key mode")
	}
}

func encryptHex(key, plain []byte) (string, error) {
	blk, err := aes.NewCipher(key); if err != nil { return "", err }
	gcm, err := cipher.NewGCM(blk); if err != nil { return "", err }
	iv := make([]byte, 12)
	if _, err := rand.Read(iv); err != nil { return "", err }
	ct := gcm.Seal(nil, iv, plain, nil)
	b := append(iv, ct...)
	return hex.EncodeToString(b), nil
}

func decryptHex(key []byte, hexData string) (string, error) {
	raw, err := hex.DecodeString(hexData); if err != nil { return "", err }
	if len(raw) <= 12 { return "", errors.New("invalid encrypted payload") }
	iv, ct := raw[:12], raw[12:]
	blk, err := aes.NewCipher(key); if err != nil { return "", err }
	gcm, err := cipher.NewGCM(blk); if err != nil { return "", err }
	pt, err := gcm.Open(nil, iv, ct, nil); if err != nil { return "", err }
	return string(pt), nil
}

func sign(method, path string, params map[string]string, key, alg string) string {
	keys := make([]string, 0, len(params)); for k := range params { keys = append(keys, k) }
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys { parts = append(parts, rfc3986(k)+"="+rfc3986(params[k])) }
	canonical := strings.Join(parts, "&")
	stringToSign := strings.ToUpper(method) + "&" + rfc3986(path) + "&" + rfc3986(canonical)
	if strings.EqualFold(alg, "hmac-sha256") {
		h := hmac.New(sha256.New, []byte(key)); h.Write([]byte(stringToSign)); return base64.StdEncoding.EncodeToString(h.Sum(nil))
	}
	h := hmac.New(sha1.New, []byte(key)); h.Write([]byte(stringToSign)); return base64.StdEncoding.EncodeToString(h.Sum(nil))
}

func rfc3986(s string) string {
	u := url.QueryEscape(s)
	u = strings.ReplaceAll(u, "+", "%20")
	u = strings.ReplaceAll(u, "*", "%2A")
	u = strings.ReplaceAll(u, "%7E", "~")
	return u
}

func buildDispatchProto(region string, exp int64) []byte {
	r := []byte(region)
	out := []byte{(1 << 3) | 2}
	out = append(out, encodeVarint(uint64(len(r)))...)
	out = append(out, r...)
	out = append(out, byte((3<<3)|0))
	out = append(out, encodeVarint(uint64(exp))...)
	return out
}

func encodeVarint(v uint64) []byte {
	buf := make([]byte, 0, 10)
	for {
		b := byte(v & 0x7f)
		v >>= 7
		if v != 0 { b |= 0x80 }
		buf = append(buf, b)
		if v == 0 { break }
	}
	return buf
}

func parseQ(v string) string {
	s := strings.ToUpper(strings.TrimSpace(v))
	switch s {
	case "A": return "4"
	case "AAAA": return "6"
	case "A+AAAA", "A+AAAAA": return "4,6"
	default: return ""
	}
}

func parseBool(v string, def bool) bool {
	if strings.TrimSpace(v) == "" { return def }
	s := strings.ToLower(strings.TrimSpace(v))
	return s == "1" || s == "true" || s == "yes" || s == "on"
}

func toFloat(v interface{}) float64 {
	switch t := v.(type) {
	case float64: return t
	case float32: return float64(t)
	case int: return float64(t)
	case int64: return float64(t)
	case json.Number:
		f, _ := t.Float64(); return f
	default:
		f, _ := strconv.ParseFloat(strings.TrimSpace(fmt.Sprint(v)), 64); return f
	}
}

func elapsedMs(start time.Time) int { return int(math.Round(float64(time.Since(start).Milliseconds()))) }

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func contains(items []string, target string) bool {
	for _, v := range items { if v == target { return true } }
	return false
}
