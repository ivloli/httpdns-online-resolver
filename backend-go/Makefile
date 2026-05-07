.PHONY: all build test tidy install uninstall start stop restart status \
	package prepare-release build-release release-package release-checksum clean run logs

APP_NAME := httpdns-online-resolver-go
BIN_DIR := bin
BIN_PATH := $(BIN_DIR)/$(APP_NAME)

TARGET_OS ?= linux
TARGET_ARCH ?= amd64
CGO_ENABLED ?= 0
RELEASE_TAG ?= $(shell git describe --always --dirty --tags 2>/dev/null || date +%Y%m%d%H%M%S)

RELEASE_DIR := release
RELEASE_NAME := $(APP_NAME)-offline-$(TARGET_OS)-$(TARGET_ARCH)-$(RELEASE_TAG)
RELEASE_PATH := $(RELEASE_DIR)/$(RELEASE_NAME)
RELEASE_BIN := $(RELEASE_PATH)/$(APP_NAME)
RELEASE_TAR := $(RELEASE_NAME).tar.gz

PREFIX ?= /usr/local
INSTALL_BIN := $(PREFIX)/bin/$(APP_NAME)
ETC_DIR ?= /etc/httpdns-online-resolver-go
CONFIG_SRC ?= config.example.yaml
CONFIG_NAME := config.yaml
SERVICE_DIR ?= /etc/systemd/system
SERVICE_FILE ?= httpdns-online-resolver-go.service
SYSTEMD_SERVICE_PATH := $(SERVICE_DIR)/$(SERVICE_FILE)

all: build

build:
	install -d -m 755 $(BIN_DIR)
	CGO_ENABLED=$(CGO_ENABLED) go build -o $(BIN_PATH) .
	@echo "Built $(BIN_PATH)"

test:
	go test ./...

tidy:
	go mod tidy

run:
	APP_CONFIG_PATH=./config.yaml go run .

install:
	@set -e; \
	src_bin=""; \
	if [ -f "$(BIN_PATH)" ]; then \
		src_bin="$(BIN_PATH)"; \
	elif [ -f "$(APP_NAME)" ]; then \
		src_bin="$(APP_NAME)"; \
	else \
		$(MAKE) build; \
		src_bin="$(BIN_PATH)"; \
	fi; \
	install -m 755 "$$src_bin" $(INSTALL_BIN)
	install -d -m 755 $(ETC_DIR)
	install -m 644 "$(CONFIG_SRC)" "$(ETC_DIR)/$(CONFIG_NAME)"
	echo "Installed config to $(ETC_DIR)/$(CONFIG_NAME) from $(CONFIG_SRC)"
	install -m 644 $(SERVICE_FILE) $(SYSTEMD_SERVICE_PATH)
	systemctl daemon-reload
	systemctl enable httpdns-online-resolver-go
	@echo "Installed $(APP_NAME). Edit $(ETC_DIR)/$(CONFIG_NAME) then start service."

uninstall:
	systemctl stop httpdns-online-resolver-go 2>/dev/null || true
	systemctl disable httpdns-online-resolver-go 2>/dev/null || true
	rm -f $(SYSTEMD_SERVICE_PATH) $(INSTALL_BIN)
	systemctl daemon-reload

start:
	systemctl start httpdns-online-resolver-go

stop:
	systemctl stop httpdns-online-resolver-go

restart:
	systemctl restart httpdns-online-resolver-go

status:
	systemctl status httpdns-online-resolver-go

logs:
	journalctl -u httpdns-online-resolver-go -f

package:
	tar -czf $(APP_NAME)-standalone.tar.gz Makefile go.mod go.sum main.go $(SERVICE_FILE) config.example.yaml
	@echo "Created $(APP_NAME)-standalone.tar.gz"

prepare-release:
	install -d -m 755 $(RELEASE_PATH)

build-release: prepare-release
	CGO_ENABLED=$(CGO_ENABLED) GOOS=$(TARGET_OS) GOARCH=$(TARGET_ARCH) go build -ldflags="-s -w" -o $(RELEASE_BIN) .
	install -m 644 $(SERVICE_FILE) $(RELEASE_PATH)/$(SERVICE_FILE)
	install -m 644 $(CONFIG_SRC) $(RELEASE_PATH)/$(CONFIG_NAME)
	install -m 644 Makefile $(RELEASE_PATH)/Makefile
	@echo "Prepared release directory: $(RELEASE_PATH)"

release-package: build-release
	COPYFILE_DISABLE=1 COPY_EXTENDED_ATTRIBUTES_DISABLE=1 tar --no-xattrs -czf $(RELEASE_TAR) -C $(RELEASE_DIR) $(RELEASE_NAME)
	@echo "Created $(RELEASE_TAR)"

release-checksum: release-package
	shasum -a 256 $(RELEASE_TAR)

clean:
	rm -rf $(BIN_DIR) $(RELEASE_DIR) *.tar.gz
