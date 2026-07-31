.PHONY: up down build logs build-openalex

PROJECT_ROOT := $(shell pwd)
COMPOSE := docker compose -f "$(PROJECT_ROOT)/docker-compose.yml"
DEDUPLICATOR_COMPOSE := docker compose -f "/home/santi/Documentos/Prebi/Backend-Modulo-Nacho/docker-compose.yml"
DSPACE_COMPOSE := docker compose -f "/home/santi/Documentos/Prebi/DSpace/docker/docker-compose.yml"

# ─── Levantar todo ────────────────────────────────────────────────────────────
up:
	@echo "▶  Starting DSpace..."
	$(DSPACE_COMPOSE) up -d
	@echo "▶  Starting external services (Deduplicator)..."
	$(DEDUPLICATOR_COMPOSE) up -d
	@echo "▶  Starting MCPs..."
	$(COMPOSE) up -d
	@echo "▶  Starting local LangGraph Platform..."
	LANGGRAPH_STARTUP_TIMEOUT=30 langgraph dev --allow-blocking

# ─── Build images ─────────────────────────────────────────────────────────────
# NOTE: OpenAlex MCP uses stdio transport → not in docker-compose, built standalone.
build:
	@echo "▶  Building MCP images..."
	$(DEDUPLICATOR_COMPOSE) build
	$(COMPOSE) build
	@echo "▶  Building OpenAlex MCP image (stdio, on-demand)..."
	docker build -t multi-servicesproject-openalex-mcp "$(PROJECT_ROOT)/mcps/openalex_mcp"

# ─── Build solo OpenAlex MCP ──────────────────────────────────────────────────
build-openalex:
	@echo "▶  Building OpenAlex MCP image..."
	docker build -t multi-servicesproject-openalex-mcp "$(PROJECT_ROOT)/mcps/openalex_mcp"

# ─── Detener todo ─────────────────────────────────────────────────────────────
down:
	@echo "▶  Stopping MCPs "
	$(COMPOSE) stop
	@echo "▶  Stopping external services (Deduplicator)..."
	$(DEDUPLICATOR_COMPOSE) stop
	@echo "▶  Stopping DSpace..."
	$(DSPACE_COMPOSE) stop

# ─── Logs en tiempo real ──────────────────────────────────────────────────────
logs:
	$(COMPOSE) logs -f

# ─── Reiniciar solo los MCPs ──────────────────────────────────────────────────
restart:
	$(COMPOSE) restart
