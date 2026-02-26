POETRY ?= /opt/homebrew/bin/poetry
IMAGE ?= schwinn-dashboard:latest
PLATFORM ?= linux/amd64
PORT ?= 8080
DATA_DIR ?= /app/data

.PHONY: install lock test run build docker-build docker-run clean

install:
	$(POETRY) install

lock:
	$(POETRY) lock

test:
	$(POETRY) run pytest -q

run:
	$(POETRY) run python app.py

build: docker-build

docker-build:
	docker build --platform $(PLATFORM) -t $(IMAGE) .

docker-run:
	docker run --rm \
		-e PORT=$(PORT) \
		-e DATA_DIR=$(DATA_DIR) \
		-p $(PORT):$(PORT) \
		-v "$(PWD)/data:$(DATA_DIR)" \
		--platform $(PLATFORM) \
		$(IMAGE)

clean:
	rm -rf .pytest_cache
