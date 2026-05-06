SHELL := /bin/bash

APP_NAME := httpdns-online-resolver-backend
APP_DIR := /opt/$(APP_NAME)
SERVICE_NAME := httpdns-online-resolver
SERVICE_FILE := /etc/systemd/system/$(SERVICE_NAME).service
PYTHON ?= python3
VENV_DIR := backend/.venv
PIP := $(VENV_DIR)/bin/pip
GUNICORN := $(VENV_DIR)/bin/gunicorn

DIST_DIR := dist
PKG_NAME := $(APP_NAME)-prod-offline-linux-amd64
PKG_FILE := $(DIST_DIR)/$(PKG_NAME).tar.gz

.PHONY: help venv install-deps run install-service enable start stop restart status logs package package-with-venv clean-dist deploy

help:
	@printf "Targets:\n"
	@printf "  make venv            - Create backend virtualenv\n"
	@printf "  make install-deps    - Install backend Python dependencies\n"
	@printf "  make run             - Run backend locally with gunicorn\n"
	@printf "  make install-service - Install systemd service file\n"
	@printf "  make enable          - Enable service at boot\n"
	@printf "  make start|stop|restart|status|logs\n"
	@printf "  make package         - Build offline deployment tar.gz\n"
	@printf "  make package-with-venv - Build offline package including backend/.venv\n"
	@printf "  make deploy          - Install deps + service + restart\n"

venv:
	$(PYTHON) -m venv "$(VENV_DIR)"

install-deps: venv
	"$(PIP)" install --upgrade pip
	"$(PIP)" install -r backend/requirements.txt

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
	cd "$(DIST_DIR)" && tar -czf "$(PKG_NAME).tar.gz" "$(PKG_NAME)"
	@printf "Built package: %s\n" "$(PKG_FILE)"

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
	cd "$(DIST_DIR)" && tar -czf "$(PKG_NAME)-with-venv.tar.gz" "$(PKG_NAME)"
	@printf "Built package: %s/%s-with-venv.tar.gz\n" "$(DIST_DIR)" "$(PKG_NAME)"

clean-dist:
	rm -rf "$(DIST_DIR)"
