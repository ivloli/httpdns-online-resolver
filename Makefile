SHELL := /bin/bash

APP_NAME := httpdns-online-resolver-backend
APP_DIR := /opt/$(APP_NAME)
SERVICE_NAME := httpdns-online-resolver
SERVICE_FILE := /etc/systemd/system/$(SERVICE_NAME).service
PYTHON ?= python3
VENV_DIR := backend/.venv
PIP := $(VENV_DIR)/bin/pip
GUNICORN := $(VENV_DIR)/bin/gunicorn
WHEELHOUSE_DIR := backend/wheelhouse

DIST_DIR := dist
PKG_NAME := $(APP_NAME)-prod-offline-linux-amd64
PKG_FILE := $(DIST_DIR)/$(PKG_NAME).tar.gz
PKG_VENV_FILE := $(DIST_DIR)/$(PKG_NAME)-with-venv.tar.gz

.PHONY: help venv install-deps download-wheels install-deps-offline run install-service enable start stop restart status logs package package-with-venv verify-package clean-dist deploy

help:
	@printf "Targets:\n"
	@printf "  make venv            - Create backend virtualenv\n"
	@printf "  make install-deps    - Install backend Python dependencies\n"
	@printf "  make download-wheels - Download offline wheels to backend/wheelhouse\n"
	@printf "  make install-deps-offline - Install deps from backend/wheelhouse\n"
	@printf "  make run             - Run backend locally with gunicorn\n"
	@printf "  make install-service - Install systemd service file\n"
	@printf "  make enable          - Enable service at boot\n"
	@printf "  make start|stop|restart|status|logs\n"
	@printf "  make package         - Build offline deployment tar.gz\n"
	@printf "  make package-with-venv - Build offline package including backend/.venv\n"
	@printf "  make verify-package  - Validate produced tar.gz archives\n"
	@printf "  make deploy          - Install deps + service + restart\n"

venv:
	$(PYTHON) -m venv "$(VENV_DIR)"

install-deps: venv
	"$(PIP)" install --upgrade pip
	"$(PIP)" install -r backend/requirements.txt

download-wheels: venv
	"$(PIP)" install --upgrade pip wheel
	mkdir -p "$(WHEELHOUSE_DIR)"
	"$(PIP)" download -r backend/requirements.txt -d "$(WHEELHOUSE_DIR)"
	@printf "Downloaded wheels to %s\n" "$(WHEELHOUSE_DIR)"

install-deps-offline: venv
	@if [ ! -d "$(WHEELHOUSE_DIR)" ]; then \
		echo "$(WHEELHOUSE_DIR) not found, run 'make download-wheels' on online machine first"; \
		exit 1; \
	fi
	"$(PIP)" install --upgrade pip
	"$(PIP)" install --no-index --find-links="$(WHEELHOUSE_DIR)" -r backend/requirements.txt

run: install-deps
	cd backend && APP_CONFIG_PATH="$$PWD/config.yaml" "$$PWD/.venv/bin/gunicorn" -w 2 -b 0.0.0.0:8088 wsgi:application

install-service:
	sudo install -m 644 deploy/$(SERVICE_NAME).service "$(SERVICE_FILE)"
	sudo systemctl daemon-reload

enable:
	sudo systemctl enable "$(SERVICE_NAME)"

start:
	sudo systemctl start "$(SERVICE_NAME)"

stop:
	sudo systemctl stop "$(SERVICE_NAME)"

restart:
	sudo systemctl restart "$(SERVICE_NAME)"

status:
	sudo systemctl status "$(SERVICE_NAME)"

logs:
	sudo journalctl -u "$(SERVICE_NAME)" -f

deploy: install-deps install-service enable restart status

package: clean-dist
	mkdir -p "$(DIST_DIR)"
	mkdir -p "$(DIST_DIR)/$(PKG_NAME)"
	cp -R backend "$(DIST_DIR)/$(PKG_NAME)/backend"
	cp -R deploy "$(DIST_DIR)/$(PKG_NAME)/deploy"
	cp README.md "$(DIST_DIR)/$(PKG_NAME)/README.md"
	cp Makefile "$(DIST_DIR)/$(PKG_NAME)/Makefile"
	rm -rf "$(DIST_DIR)/$(PKG_NAME)/backend/.venv"
	cd "$(DIST_DIR)" && tar -czf "$(PKG_NAME).tar.gz" "$(PKG_NAME)"
	@printf "Built package: %s\n" "$(PKG_FILE)"
	@file "$(PKG_FILE)"
	@tar -tzf "$(PKG_FILE)" >/dev/null

package-with-venv: clean-dist
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "backend/.venv not found, run 'make install-deps' first"; \
		exit 1; \
	fi
	mkdir -p "$(DIST_DIR)"
	mkdir -p "$(DIST_DIR)/$(PKG_NAME)"
	cp -R backend "$(DIST_DIR)/$(PKG_NAME)/backend"
	cp -R deploy "$(DIST_DIR)/$(PKG_NAME)/deploy"
	cp README.md "$(DIST_DIR)/$(PKG_NAME)/README.md"
	cp Makefile "$(DIST_DIR)/$(PKG_NAME)/Makefile"
	find "$(DIST_DIR)/$(PKG_NAME)/backend/.venv" -type d -name "__pycache__" -prune -exec rm -rf {} +
	find "$(DIST_DIR)/$(PKG_NAME)/backend/.venv" -type f -name "*.pyc" -delete
	cd "$(DIST_DIR)" && tar -czf "$(PKG_NAME)-with-venv.tar.gz" "$(PKG_NAME)"
	@printf "Built package: %s\n" "$(PKG_VENV_FILE)"
	@file "$(PKG_VENV_FILE)"
	@tar -tzf "$(PKG_VENV_FILE)" >/dev/null

verify-package:
	@if [ -f "$(PKG_FILE)" ]; then \
		echo "Verifying $(PKG_FILE)"; \
		file "$(PKG_FILE)"; \
		tar -tzf "$(PKG_FILE)" >/dev/null; \
	fi
	@if [ -f "$(PKG_VENV_FILE)" ]; then \
		echo "Verifying $(PKG_VENV_FILE)"; \
		file "$(PKG_VENV_FILE)"; \
		tar -tzf "$(PKG_VENV_FILE)" >/dev/null; \
	fi

clean-dist:
	rm -rf "$(DIST_DIR)"
