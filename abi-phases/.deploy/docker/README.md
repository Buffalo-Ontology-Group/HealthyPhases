# Docker Configuration

> **Centralized Docker configuration and containerization assets for ABI**

This directory contains all Docker-related files organized for maintainability and clarity, following industry best practices used by major open source projects.

## 📁 Directory Structure

```
docker/
├── compose/
│   └── docker-compose.yml          # Main orchestration configuration
├── images/
│   ├── Dockerfile                  # Primary application container
│   └── Dockerfile.linux.x86_64     # Linux x86_64 optimized build
├── scripts/
│   └── cleanup.sh                  # Docker cleanup utilities
└── README.md                       # This documentation
```

## 🎯 Purpose & Organization

### **compose/**
Contains Docker Compose configurations for orchestrating multi-container applications:
- **`docker-compose.yml`** - Main service orchestration (Apache Jena Fuseki, PostgreSQL, Dagster, etc.)

### **images/**
Contains all Dockerfile variants for different deployment scenarios:
- **`Dockerfile`** - Standard application container
- **`Dockerfile.linux.x86_64`** - Optimized for Linux x86_64 architecture

### **scripts/**
Docker-related utility scripts:
- **`cleanup.sh`** - Comprehensive Docker cleanup to resolve conflicts

## 🚀 Usage

### Building Images
```bash
# Build standard image
docker build -f docker/images/Dockerfile -t abi .

# Build Linux x86_64 optimized
docker build -f docker/images/Dockerfile.linux.x86_64 -t abi-linux .

```

### Running Services
```bash
# Start all local services
docker compose -f docker-compose.yml up -d

# Start specific service
docker compose -f docker-compose.yml up fuseki
```

### Upgrading an Existing PostgreSQL Volume

PostgreSQL only runs `docker-entrypoint-initdb.d` scripts when it initializes a
new volume. After upgrading an existing deployment, create the dedicated,
idempotent DuckLake catalog before starting ABI:

```bash
docker compose exec -T postgres psql -U "${POSTGRES_USER:-abi}" -d postgres \
  -f /docker-entrypoint-initdb.d/005-create-ducklake-db.sql
```

This script only provisions the PostgreSQL database and grants. The dataset
adapter initializes and migrates DuckLake metadata when it connects.

Changing a DuckLake catalog from SQLite to PostgreSQL requires a metadata
migration. Changing only the catalog DSN loses snapshot history and can leave
inlined rows behind.

### Cleanup
```bash
# Run comprehensive cleanup
./docker/scripts/cleanup.sh
```

## 🔧 Makefile Integration

All Docker operations are integrated into the main Makefile:

```bash
make local-up          # Start local development environment
make api-prod          # Build and run production API
make docker-cleanup    # Run Docker cleanup
make build.linux.x86_64  # Build Linux x86_64 image
```

## 📋 Best Practices

1. **Organized Structure**: All Docker files are centralized and categorized
2. **Clear Naming**: File names indicate their specific purpose and target
3. **Documentation**: Each component is documented for maintainability
4. **Integration**: Seamless integration with existing build and deployment workflows

This organization follows patterns established by major projects like Kubernetes, Prometheus, and HashiCorp Vault for maximum developer familiarity and maintainability.

## Scanned-paper OCR

The shared ABI/Dagster image installs Tesseract and English language data.
When normal PDF-to-Markdown conversion returns no text, the phases_v2 renderer
tries page text extraction and OCRs image-only pages sequentially at 150 DPI.
Successful normal rendering remains unchanged. OCR quality depends on the scan;
the fallback defaults to English for the current corpus.

Production deployment uses `docker compose up -d --build --no-deps
--force-recreate abi dagster` so native dependency changes reach the running
containers. Cached layers are reused. Allow up to 15 minutes for the deployment
command, and finish active ingestion runs before merging runtime image changes.
The OCR smoke test skips when local English Tesseract data is unavailable.

## Fuseki: keep the 5.6 build, don't regenerate this compose file

The `fuseki` service builds `fuseki/Dockerfile`: Apache Fuseki 5.6.0 on the
`stain/jena-fuseki` base, with the download checked against Apache's SHA-512.
The stain image alone is Fuseki 5.1.0, which leaks one TDB2 read lock per
query (apache/jena#2755, fixed in 5.2.0). After 65,535 requests every query
fails with HTTP 500, and the API can no longer start.
`fuseki/fuseki-server-launcher.sh` turns off Shiro session storage, once, in
the volume's `shiro.ini`, because Fuseki 5.6 has no session manager.

Do not regenerate `docker-compose.yml` with `abi deploy local` until both of
these are done:

- the upstream template fix,
  [jupyter-naas/abi#1450](https://github.com/jupyter-naas/abi/pull/1450), is
  released;
- the `.abi` submodule is bumped to that release, together with the
  `nexus-web` image tag.

Until then, the template still renders `image: stain/jena-fuseki:latest`.
`tests/fuseki_image_test.py`, run by `make test`, fails in either of these
cases:

- the `fuseki` service names a `stain/jena-fuseki` image, or any `image:`;
- it stops building `.deploy/docker/fuseki`, or the Dockerfile's Fuseki
  version drops below 5.2.

## When the API fails to start

At startup the API checks Fuseki: six attempts over about 16 seconds. If
Fuseki is unreachable or answers an error, the check raises and the API
process exits with code 1. Docker's `restart: unless-stopped` then starts it
again, about every 20 to 25 seconds, for as long as Fuseki stays down. Once
Fuseki is healthy, the next start succeeds and the container turns healthy on
its own. No one needs to act.

This works because `config.local.yaml` sets `api.reload: false`. The compose
stack runs the API with `ENV=local`, so this file is its config, in
production too. Upstream's default is `reload: true`, and with the uvicorn
reloader a failed startup kills only the server worker. The reloader parent
stays up under `uv run`, so the container shows "Up (unhealthy)" forever and
Docker never restarts it. That is how production stayed down for days after
the Fuseki outage of 2026-10-28. `tests/api_restart_test.py` keeps the
setting.

The API therefore no longer reloads on code changes. After changing code
under `src/`, recreate it: `docker compose up -d --no-deps --force-recreate
abi`. To watch a startup that keeps failing:

```bash
docker compose ps abi                                     # Up (health: starting), again and again
docker inspect abi-phases-abi-1 --format '{{.RestartCount}}'   # grows by one per failed start
docker compose logs --since 5m abi | grep -E 'Fuseki connectivity|RequestError'
```
