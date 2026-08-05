# Twin's operator interface.
#
# Every target announces what it is about to do, reports what happened, and exits non-zero
# on failure. Nothing here half-succeeds quietly.
#
# Targets are added as the stages they drive are built. If a target is not in `make help`,
# it does not exist yet — there are no placeholders in this file.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE := docker compose
RUN     := $(COMPOSE) run --rm twin
DBT     := cd /app/estate/dbt && dbt

# Services that must be healthy before the estate can be built.
STACK_SERVICES := warehouse datahub-gms datahub-frontend

BLUE := \033[1;34m
DIM  := \033[2m
OFF  := \033[0m

# Announced as a plain printf rather than a $(call) macro on purpose: GNU Make splits
# $(call) arguments on commas, so any banner text containing a comma would be silently
# truncated mid-quote and hand the shell an unterminated string.
SAY := printf "$(BLUE)==>$(OFF) %s\n"

.PHONY: help
help: ## Show the available targets
	@printf "\n  $(BLUE)Twin$(OFF) — chaos engineering for data platforms\n\n"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'
	@printf "\n  $(DIM)Start with: make up && make estate && make verify-estate$(OFF)\n\n"

# ------------------------------------------------------------------ stack lifecycle

.PHONY: up
up: ## Bring up DataHub and the warehouse
	@$(SAY) "Starting DataHub and the warehouse. First run pulls ~5GB of images."
	@$(COMPOSE) up -d --wait $(STACK_SERVICES)
	@printf "\n  DataHub UI  http://localhost:$${TWIN_FRONTEND_PORT:-19002}   (login: datahub / datahub)\n"
	@printf "  Warehouse   postgresql://twin@localhost:$${TWIN_WAREHOUSE_PORT:-15432}/warehouse\n\n"

.PHONY: down
down: ## Stop everything and remove the volumes
	@$(SAY) "Tearing down the stack and its volumes."
	@$(COMPOSE) down -v --remove-orphans

.PHONY: build
build: ## Rebuild the Twin tools image
	@$(SAY) "Building the Twin tools image."
	@$(COMPOSE) build twin

.PHONY: ps
ps: ## Show what is running
	@$(COMPOSE) ps

# ------------------------------------------------------------------ the demo estate

.PHONY: estate
estate: estate-seed estate-build estate-ingest estate-workload ## Seed, build and ingest the demo estate
	@$(SAY) "Estate complete. Run 'make verify-estate' to check it."

.PHONY: estate-seed
estate-seed: ## Generate and load the raw source data
	@$(SAY) "Seeding raw source data (~1.9M rows, deterministic)."
	@$(RUN) python -m estate.seed.generate

.PHONY: estate-build
estate-build: ## Run dbt to build the warehouse models
	@$(SAY) "Building 39 dbt models and running their tests."
	@$(RUN) bash -c "$(DBT) build --target dev"
	@$(SAY) "Generating dbt catalog for ingestion."
	@$(RUN) bash -c "$(DBT) docs generate --target dev --no-compile"

.PHONY: estate-ingest
estate-ingest: ## Ingest the estate into DataHub
	@$(SAY) "Ingesting the physical warehouse into DataHub."
	@$(RUN) datahub ingest -c /app/estate/ingest/recipes/postgres.yml
	@$(SAY) "Ingesting dbt lineage, column lineage and ownership."
	@$(RUN) datahub ingest -c /app/estate/ingest/recipes/dbt.yml
	@$(SAY) "Emitting people, dashboards and the ML branch."
	@$(RUN) python -m estate.ingest.emit

.PHONY: estate-workload
estate-workload: ## Execute the consumer workload and publish usage statistics
	@$(SAY) "Running the consumer workload against the warehouse."
	@$(RUN) python -m estate.ingest.workload

.PHONY: verify-estate
verify-estate: ## Prove the estate is real (prints a table, exits non-zero on failure)
	@$(SAY) "Verifying the estate against DataHub."
	@$(RUN) python -m estate.verify_estate

# ------------------------------------------------------------------ stage 1: read

.PHONY: read
read: ## Read the estate from DataHub over MCP and cache the graph
	@$(SAY) "Reading the estate through the DataHub MCP server."
	@$(RUN) python -m twin.read

.PHONY: graph
graph: ## Print the cached estate graph without touching DataHub
	@$(RUN) python -m twin.read --cached

# ------------------------------------------------------------------ stage 4: verify

SCENARIO ?= scenarios/fx_rate_column_drop.yml

.PHONY: run
run: ## Run one scenario end to end (SCENARIO=scenarios/<name>.yml)
	@$(SAY) "Running $(SCENARIO) through stages 1-4."
	@$(RUN) python -m twin.run $(SCENARIO)

.PHONY: dry-run
dry-run: ## Print every statement a scenario would execute, without executing any
	@$(SAY) "Dry run of $(SCENARIO)."
	@$(RUN) python -m twin.run $(SCENARIO) --dry-run

.PHONY: scenarios
scenarios: ## Run every scenario in scenarios/ and grade each one
	@$(SAY) "Running every scenario."
	@for scenario in scenarios/*.yml; do \
		$(RUN) python -m twin.run $$scenario || exit 1; \
	done

# ------------------------------------------------------------------ stage 3: score

.PHONY: score
score: ## Rank the estate by fragility (knockout sweep)
	@$(SAY) "Sweeping every asset and scoring fragility."
	@$(RUN) python -m twin.score

# ------------------------------------------------------------------ tests

.PHONY: test
test: ## Run the test suite
	@$(SAY) "Running tests."
	@$(RUN) python -m pytest

