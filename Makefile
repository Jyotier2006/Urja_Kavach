.DEFAULT_GOAL := help
COMPOSE := docker compose
PY := .venv/bin/python

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install Node and Python dependencies, including test-only extras
	npm ci
	$(PY) -m pip install -r requirements.lock
	$(PY) -m pip install -r requirements-dev.lock

dev: ## Run the web app in development
	npm run dev

api: ## Run the Python API locally
	.venv/bin/uvicorn services.api.app.main:app --host 127.0.0.1 --port 8000 --reload

test: ## Python tests
	$(PY) -m pytest services tests -q

e2e: ## Browser journey tests (set E2E_PORT if 3000 is taken)
	npm run test:e2e

check: ## Typecheck, lint and Python tests
	npm run typecheck
	npm run lint
	$(PY) -m pytest services tests -q

build: ## Production static export plus service worker
	npm run build

up: ## Full stack in Docker: web on :8080, API on :8000, PostgreSQL
	$(COMPOSE) up --build -d
	@echo "web  http://localhost:8080"
	@echo "api  http://localhost:8000/docs"

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Follow stack logs
	$(COMPOSE) logs -f

images: ## Build both container images
	docker build -f docker/api.Dockerfile -t urjakavach-api:latest .
	docker build -f docker/web.Dockerfile -t urjakavach-web:latest .

k8s-validate: ## Render and schema-check both Kubernetes overlays, no cluster needed
	@command -v kubeconform >/dev/null 2>&1 || { \
		echo "kubeconform is required: https://github.com/yannh/kubeconform/releases"; \
		echo "kubectl apply --dry-run=client cannot be used here; it needs a live cluster for the OpenAPI schema."; \
		exit 1; }
	@for overlay in dev prod; do \
		echo "== $$overlay =="; \
		kubectl kustomize k8s/overlays/$$overlay | kubeconform -strict -summary -kubernetes-version 1.30.0 ; \
	done

k8s-dev: ## Apply the dev overlay to the current kube context
	kubectl apply -k k8s/overlays/dev

care-train: ## Train and evaluate M2 on one CARE farm, e.g. make care-train FARM=B
	$(PY) scripts/train_care_nbm.py $(or $(FARM),B)

care-publish: ## Publish measured CARE results to the web artifact
	$(PY) scripts/publish_care_evaluation.py

datasets: ## Download a dataset, e.g. make datasets NAME=care
	$(PY) scripts/download_datasets.py $(or $(NAME),care)

.PHONY: help install dev api test e2e check build up down logs images k8s-validate k8s-dev care-train care-publish datasets
