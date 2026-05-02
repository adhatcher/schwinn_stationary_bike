UV ?= uv
CODEQL ?= codeql
CODEQL_DB ?= codeql-db
CODEQL_OUTPUT ?= codeql-results.sarif
IMAGE ?= schwinn-dashboard:latest
PLATFORM ?= linux/amd64
PORT ?= 8080
DOCKER_RUN_PORT ?= $(PORT)
DATA_DIR ?= /app/data
DOCKER_TEST_PORT ?= 18080
DOCKER_TEST_NAME ?= schwinn-dashboard-ui-test

.PHONY: install lock test coverage security codeql run build docker-build docker-run docker-ui-test local-test precommit clean

install:
	$(UV) sync

lock:
	$(UV) lock

test:
	$(UV) run pytest -q

coverage:
	$(UV) run pytest --cov=app.app --cov-report=term-missing --cov-report=xml

security:
	$(UV) run pip-audit
	$(UV) run bandit -r app -x app/logs
	$(MAKE) codeql

codeql:
	@command -v $(CODEQL) >/dev/null 2>&1 || { echo "CodeQL CLI is not installed. Install from https://github.com/github/codeql-cli-binaries/releases/latest" >&2; exit 1; }
	@rm -rf $(CODEQL_DB)
	$(CODEQL) database create $(CODEQL_DB) --language=python --source-root=.
	$(CODEQL) database analyze $(CODEQL_DB) --format=sarif-latest --output=$(CODEQL_OUTPUT)

local-test: test coverage security docker-ui-test

git-hooks:
	@git config core.hooksPath .githooks
	@echo "Git hooks path configured to .githooks"

precommit: local-test

run:
	$(UV) run python app/app.py

build: docker-build

docker-build:
	docker build --platform $(PLATFORM) -t $(IMAGE) .

docker-run: docker-build
	docker run --rm \
		-e PORT=$(PORT) \
		-e DATA_DIR=$(DATA_DIR) \
		-p $(DOCKER_RUN_PORT):$(PORT) \
		-v "$(PWD)/app/data:$(DATA_DIR)" \
		--platform $(PLATFORM) \
		$(IMAGE)

docker-ui-test: docker-build
	npm ci
	npx playwright install chromium
	docker rm -f $(DOCKER_TEST_NAME) >/dev/null 2>&1 || true; \
	docker run --rm -d \
		--name $(DOCKER_TEST_NAME) \
		-e PORT=$(PORT) \
		-e DATA_DIR=$(DATA_DIR) \
		-p $(DOCKER_TEST_PORT):$(PORT) \
		--platform $(PLATFORM) \
		$(IMAGE); \
	trap 'docker rm -f $(DOCKER_TEST_NAME) >/dev/null 2>&1 || true' EXIT; \
	BASE_URL=http://127.0.0.1:$(DOCKER_TEST_PORT) \
	CONTAINER_NAME=$(DOCKER_TEST_NAME) \
	npx playwright test tests/docker-ui.spec.mjs --browser=chromium

clean:
	rm -rf .pytest_cache .venv
