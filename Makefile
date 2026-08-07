# Twin's operator interface.
#
# Every target announces what it is about to do, reports what happened, and exits non-zero
# on failure. Nothing here half-succeeds quietly.
#
# Targets are added when they are usable. If a target is not in `make help`,
# it does not exist yet — there are no placeholders in this file.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE := docker compose
RUN     := $(COMPOSE) run --rm twin

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
	@$(SAY) "Seeding the $(TARGET) raw source data."
	@$(RUN) python -m twin.target seed --target $(TARGET)

.PHONY: estate-build
estate-build: ## Run dbt to build the warehouse models
	@$(SAY) "Building the $(TARGET) dbt project and running its tests."
	@$(RUN) python -m twin.target build --target $(TARGET)

.PHONY: estate-ingest
estate-ingest: ## Ingest the estate into DataHub
	@$(SAY) "Ingesting $(TARGET) physical tables, dbt lineage and operational metadata."
	@$(RUN) python -m twin.target ingest --target $(TARGET)

.PHONY: estate-workload
estate-workload: ## Execute the consumer workload and publish usage statistics
	@$(SAY) "Running the $(TARGET) consumer workload against the warehouse."
	@$(RUN) python -m twin.target workload --target $(TARGET)

.PHONY: verify-estate
verify-estate: ## Prove the estate is real (prints a table, exits non-zero on failure)
	@$(SAY) "Verifying the $(TARGET) estate against DataHub."
	@$(RUN) python -m twin.target verify --target $(TARGET)

# ------------------------------------------------------------------ read

.PHONY: read
read: ## Read the estate from DataHub over MCP and cache the graph
	@$(SAY) "Reading the estate through the DataHub MCP server."
	@$(RUN) python -m twin.read --target $(TARGET)

.PHONY: graph
graph: ## Print the cached estate graph without touching DataHub
	@$(RUN) python -m twin.read --target $(TARGET) --cached

# ------------------------------------------------------------------ verify

SCENARIO ?= scenarios/fx_rate_column_drop.yml
TARGET ?= commerce

.PHONY: run
run: ## Run one scenario end to end (SCENARIO=scenarios/<name>.yml)
	@$(SAY) "Running $(TARGET):$(SCENARIO) through simulation and verification."
	@$(RUN) python -m twin.run --target $(TARGET) $(SCENARIO)

.PHONY: incidents
incidents: ## Run a scenario and raise DataHub incidents for what actually broke
	@$(SAY) "Running $(TARGET):$(SCENARIO) and raising incidents for observed failures."
	@$(RUN) python -m twin.run --target $(TARGET) $(SCENARIO) --incidents

.PHONY: dry-run
dry-run: ## Print every statement a scenario would execute, without executing any
	@$(SAY) "Dry run of $(TARGET):$(SCENARIO)."
	@$(RUN) python -m twin.run --target $(TARGET) $(SCENARIO) --dry-run

.PHONY: scenarios
scenarios: ## Run every scenario in scenarios/ and grade each one
	@$(SAY) "Running every $(TARGET) scenario."
	@$(RUN) python -m twin.target scenarios --target $(TARGET)

.PHONY: campaign
CAMPAIGN_EXECUTE ?= 0
campaign: ## Rank context-integrity experiments (CAMPAIGN_EXECUTE=1 runs the top one)
	@$(SAY) "Planning the deterministic $(TARGET) context-integrity campaign."
	@$(RUN) python -m twin.campaign --target $(TARGET) $(if $(filter 1,$(CAMPAIGN_EXECUTE)),--execute,)

# ------------------------------------------------------------------ score

.PHONY: score
score: ## Rank the estate by fragility (knockout sweep)
	@$(SAY) "Sweeping every asset and scoring fragility."
	@$(RUN) python -m twin.score --target $(TARGET)

# ------------------------------------------------------------------ catalog

.PHONY: writeback prove-writeback unwrite
writeback: ## Write fragility scores into DataHub as structured properties
	@$(SAY) "Writing the fragility dimension into DataHub."
	@$(RUN) python -m twin.write --target $(TARGET)

prove-writeback: ## Read Twin's scores back out of DataHub over MCP
	@$(SAY) "Reading the scores back over MCP, the way an agent would."
	@$(RUN) python -m twin.write --target $(TARGET) --prove

unwrite: ## Remove everything Twin wrote to DataHub
	@$(SAY) "Removing every property Twin wrote."
	@$(RUN) python -m twin.write --target $(TARGET) --unwrite

# ------------------------------------------------------------------ examples

.PHONY: examples
examples: ## Regenerate examples/ from real runs (needs the stack up; ~7 min)
	@./ops/capture-examples.sh

.PHONY: repair
REPAIR_OUTPUT_DIR ?= examples/repair-prs
REPAIR_SCENARIO ?=
repair: ## Generate an evidence-backed catalog repair proposal
	@$(SAY) "Generating a catalog repair proposal for $(TARGET)."
	@$(RUN) python -m twin.repair --target $(TARGET) --output-dir $(REPAIR_OUTPUT_DIR) $(if $(REPAIR_SCENARIO),--scenario $(REPAIR_SCENARIO),)

.PHONY: gate
gate: ## Run repository invariants, target validation, determinism checks and tests
	@$(SAY) "Running the repository quality gate."
	@$(RUN) python -m twin.gate

# ------------------------------------------------------------------ tests

.PHONY: test
test: ## Run the test suite
	@$(SAY) "Running tests."
	@$(RUN) python -m pytest
