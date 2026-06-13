package main

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/lionsoul2014/ip2region/binding/golang/xdb"
)

type githubRelease struct {
	TagName    string `json:"tag_name"`
	TarballURL string `json:"tarball_url"`
}

func (a *App) ensureIP2RegionReady() error {
	if !a.cfg.IP2Region.AutoUpdate {
		return nil
	}
	updater := newIP2RegionUpdater(a.cfg.IP2Region)
	return updater.updateIfNeeded(true, a.swapSearchers)
}

func (a *App) ip2regionRefreshLoop() {
	ticker := time.NewTicker(time.Duration(a.cfg.IP2Region.UpdateIntervalSeconds) * time.Second)
	defer ticker.Stop()
	updater := newIP2RegionUpdater(a.cfg.IP2Region)
	for range ticker.C {
		if err := updater.updateIfNeeded(false, a.swapSearchers); err != nil {
			log.Printf("ip2region refresh failed: %v", err)
		}
	}
}

func (a *App) swapSearchers(v4Searcher, v6Searcher *xdb.Searcher) {
	a.xdbMu.Lock()
	defer a.xdbMu.Unlock()
	if v4Searcher != nil {
		a.v4Searcher = v4Searcher
	}
	if v6Searcher != nil {
		a.v6Searcher = v6Searcher
	}
}

type ip2regionUpdater struct {
	v4XDB       string
	v6XDB       string
	releasesURL string
	githubToken string
	versionFile string
	httpClient  *http.Client
}

func newIP2RegionUpdater(cfg struct {
	V4XDB                 string `yaml:"v4_xdb"`
	V6XDB                 string `yaml:"v6_xdb"`
	AutoUpdate            bool   `yaml:"auto_update"`
	UpdateIntervalSeconds int    `yaml:"update_interval_seconds"`
	ReleasesURL           string `yaml:"releases_url"`
	GithubToken           string `yaml:"github_token"`
	VersionFile           string `yaml:"version_file"`
}) *ip2regionUpdater {
	return &ip2regionUpdater{
		v4XDB:       cfg.V4XDB,
		v6XDB:       cfg.V6XDB,
		releasesURL: cfg.ReleasesURL,
		githubToken: cfg.GithubToken,
		versionFile: cfg.VersionFile,
		httpClient:  &http.Client{Timeout: 60 * time.Second},
	}
}

func (u *ip2regionUpdater) updateIfNeeded(force bool, reload func(v4Searcher, v6Searcher *xdb.Searcher)) error {
	if u.v4XDB == "" || u.v6XDB == "" {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(u.versionFile), 0755); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(u.v4XDB), 0755); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(u.v6XDB), 0755); err != nil {
		return err
	}
	rel, err := u.fetchLatestRelease()
	if err != nil {
		return err
	}
	localTag := u.readLocalVersion()
	missing := fileMissing(u.v4XDB) || fileMissing(u.v6XDB)
	if !force && !missing && localTag == rel.TagName {
		return nil
	}
	if err := u.downloadAndExtract(rel); err != nil {
		return err
	}
	v4Searcher, err := loadSearcher(u.v4XDB, xdb.IPv4)
	if err != nil {
		return err
	}
	v6Searcher, err := loadSearcher(u.v6XDB, xdb.IPv6)
	if err != nil {
		return err
	}
	reload(v4Searcher, v6Searcher)
	if err := os.WriteFile(u.versionFile, []byte(rel.TagName+"\n"), 0644); err != nil {
		return err
	}
	log.Printf("ip2region updated to %s", rel.TagName)
	return nil
}

func (u *ip2regionUpdater) fetchLatestRelease() (*githubRelease, error) {
	req, err := http.NewRequest("GET", u.releasesURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	if u.githubToken != "" {
		req.Header.Set("Authorization", "Bearer "+u.githubToken)
	}
	resp, err := u.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("github API returned %d", resp.StatusCode)
	}
	var rel githubRelease
	if err := json.NewDecoder(resp.Body).Decode(&rel); err != nil {
		return nil, err
	}
	rel.TagName = strings.TrimSpace(rel.TagName)
	rel.TarballURL = strings.TrimSpace(rel.TarballURL)
	if rel.TagName == "" || rel.TarballURL == "" {
		return nil, fmt.Errorf("invalid latest release payload")
	}
	return &rel, nil
}

func (u *ip2regionUpdater) readLocalVersion() string {
	b, _ := os.ReadFile(u.versionFile)
	return strings.TrimSpace(string(b))
}

func (u *ip2regionUpdater) downloadAndExtract(rel *githubRelease) error {
	req, err := http.NewRequest("GET", rel.TarballURL, nil)
	if err != nil {
		return err
	}
	if u.githubToken != "" {
		req.Header.Set("Authorization", "Bearer "+u.githubToken)
	}
	resp, err := u.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: status %d", rel.TarballURL, resp.StatusCode)
	}

	gzr, err := gzip.NewReader(resp.Body)
	if err != nil {
		return err
	}
	defer gzr.Close()
	tr := tar.NewReader(gzr)

	needed := map[string]string{
		"data/ip2region_v4.xdb": u.v4XDB,
		"data/ip2region_v6.xdb": u.v6XDB,
	}
	stageDir, err := os.MkdirTemp(filepath.Dir(u.versionFile), "ip2region-extract-*")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stageDir)
	stageFiles := map[string]string{}
	for suffix, destPath := range needed {
		stageFiles[suffix] = filepath.Join(stageDir, filepath.Base(destPath))
	}
	written := map[string]bool{}

	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		if hdr.Typeflag != tar.TypeReg {
			continue
		}
		name := strings.TrimPrefix(hdr.Name, "./")
		for suffix := range needed {
			if !strings.HasSuffix(name, suffix) || written[suffix] {
				continue
			}
			if err := writeReaderToFile(tr, stageFiles[suffix]); err != nil {
				return fmt.Errorf("extract %s: %w", suffix, err)
			}
			written[suffix] = true
			break
		}
	}
	if len(written) != len(needed) {
		missing := make([]string, 0, len(needed)-len(written))
		for suffix := range needed {
			if !written[suffix] {
				missing = append(missing, suffix)
			}
		}
		sort.Strings(missing)
		return fmt.Errorf("release tarball missing required files: %s", strings.Join(missing, ", "))
	}
	for suffix, destPath := range needed {
		src, err := os.Open(stageFiles[suffix])
		if err != nil {
			return err
		}
		if err := writeReaderToFile(src, destPath); err != nil {
			src.Close()
			return err
		}
		src.Close()
	}
	return nil
}

func writeReaderToFile(r io.Reader, destPath string) error {
	if err := os.MkdirAll(filepath.Dir(destPath), 0755); err != nil {
		return err
	}
	tmp := destPath + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, r); err != nil {
		f.Close()
		os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, destPath)
}

func fileMissing(path string) bool {
	_, err := os.Stat(path)
	return err != nil
}
