UV ?= uv
IMAGE ?= schwinn-dashboard:latest
PLATFORM ?= linux/amd64
PORT ?= 8080
DATA_DIR ?= /app/data
DOCKER_TEST_PORT ?= 18080
DOCKER_TEST_NAME ?= schwinn-dashboard-ui-test

.PHONY: install lock test coverage security run build docker-build docker-run docker-ui-test local-test clean

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

local-test: test coverage security docker-ui-test

run:
	$(UV) run python app/app.py

build: docker-build

docker-build:
	docker build --platform $(PLATFORM) -t $(IMAGE) .

docker-run:
	docker run --rm \
		-e PORT=$(PORT) \
		-e DATA_DIR=$(DATA_DIR) \
		-p $(PORT):$(PORT) \
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
