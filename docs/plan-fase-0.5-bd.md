# Plan de Implementación — Fase 0.5: Base de datos y migraciones (Alembic)

> Documento generado para ejecutarse más adelante. Basado en el prompt de la
> Fase 0.5 y contrastado con el estado real del proyecto (2026-10-06).

## Contexto

Proyecto: agente de ofertas de empleo para España (multiusuario). Stack:
Python 3.11+, FastAPI, PostgreSQL 16 + pgvector, Redis, Docker Compose.
El esquema SQL de referencia está en `TODO.md` §2.

**Objetivo:** esquema de base de datos versionado con **Alembic**, aplicable con
un solo comando, **sin modificar el código existente** (`schemas/`, `ingestion/`).

**Restricciones:**

- NO usar `/docker-entrypoint-initdb.d/` (el volumen ya está inicializado).
- NO borrar ni recrear el volumen `pg_data`.
- NO tocar `schemas/job.py` ni `ingestion/`. Solo archivos nuevos y ediciones
  mínimas en `requirements.txt` / `docker-compose.yml` / `Dockerfile` si hace
  falta.
- NO escribir secretos en ningún archivo. La URL sale de `.env`
  (`DATABASE_URL=postgresql+asyncpg://...`); `.env.example` lleva la variable
  sin valores reales.
- NO implementar repositorios, hashing ni inserción (eso es Fase 2). Solo
  esquema y conexión.
- SQL directo con `op.execute(...)`; sin modelos ORM.

---

## Contraste: supuestos del prompt vs realidad (verificado)

| Supuesto del prompt | Realidad verificada |
|---|---|
| Volumen `pg_data` inicializado, sin tablas | ✅ Existe `jobfinder_pg_data`, **pero ningún contenedor existe** (`docker compose ps -a` vacío) → hay que levantar el stack antes de migrar |
| Fase 0 completada | ⚠️ 0.4 (backups) y la verificación de Fase 0 siguen pendientes en `TODO.md`; no bloquea esta tarea |
| Fase 1: 1.3 "terminando" | ✅ `base.py` y `adzuna.py` commiteados; 1.3 completa |
| `DATABASE_URL` sale de `.env` | ⚠️ En `.env` está **literal con `${POSTGRES_USER}...` sin expandir**. Dentro del contenedor funciona porque `docker-compose.yml:63` la redefine interpolada (tiene precedencia sobre `env_file`). **Fuera del contenedor no funciona** → Alembic debe correrse con `docker compose exec app ...` |
| `sqlalchemy`/`asyncpg` por añadir | ⚠️ Ya están en `requirements.txt` (sqlalchemy sin extra `[asyncio]`); faltan `alembic` y `pgvector` |
| "Crea `db/session.py` con..." | ❌ El prompt original estaba cortado en el paso 4; contenido propuesto más abajo |
| Esquema §2.1 con correcciones | Coherente: `jobs` en §2.1 no tiene columna `status` (solo la menciona el flujo 1.2); `include_no_salary` y los UNIQUE pedidos no existen → añadir |

---

## Pasos de implementación

### Paso 0 — Limpieza previa
- Commit de los cambios pendientes de la tarea 1.3 (hecho: `cliente Adzuna con rate limiting y reintentos (tarea 1.3)`).

### Paso 1 — Dependencias (`requirements.txt`)
- `sqlalchemy>=2.0.28,<3.0.0` → `sqlalchemy[asyncio]>=2.0.28,<3.0.0`
- Añadir `alembic>=1.13,<2.0.0`
- Añadir `pgvector>=0.3.0,<1.0.0`
- `asyncpg` ya está presente.

### Paso 2 — `.env.example`
- `DATABASE_URL` ya existe (línea 15); verificar formato `postgresql+asyncpg://...` y que no contenga valores reales.

### Paso 3 — Levantar el stack
```bash
docker compose up -d --build   # rebuild de app para instalar alembic
docker compose ps              # todos healthy
docker compose exec postgres psql -U jobfinder -d jobfinder_db -c '\dt'  # 0 tablas
```
- Si Caddy falla por puertos 80/443 ocupados: `docker compose up -d postgres redis app`.

### Paso 4 — Inicializar Alembic (dentro del contenedor)
```bash
docker compose exec app alembic init -t async db/migrations
```
- Los archivos aparecen en la raíz del repo vía volumen `.:/app`.

### Paso 5 — Configuración
- `alembic.ini`: `sqlalchemy.url =` vacío (sin secretos).
- `db/migrations/env.py`:
  - Leer `os.environ["DATABASE_URL"]` (aplicar `.replace("%", "%%")` para configparser).
  - Usar el helper `async_engine_from_config` del template async.
  - `target_metadata = None` (no hay modelos ORM; quitar imports del template).

### Paso 6 — Migración `0001_initial_schema` (SQL manual con `op.execute`)

**`upgrade()`:**
1. `CREATE EXTENSION IF NOT EXISTS vector;` **primero** (necesario para `embedding vector(384)`).
2. Tablas `jobs`, `users`, `user_profiles`, `job_scores`, `notifications_log` según `TODO.md` §2.1, con estas correcciones:
   - `jobs`: **NO** añadir columna `status` (el estado "pendiente de embedding" es `embedding IS NULL`).
   - `user_profiles`: + `UNIQUE (user_id)` y + `include_no_salary BOOLEAN DEFAULT TRUE`.
   - `notifications_log`: + `UNIQUE (user_id, job_id)`.
   - FKs a `users` y `jobs` con `ON DELETE CASCADE` (§2.1 ya las trae; `jobs` no tiene FK — es independiente de usuarios).
   - `job_scores` mantiene `CONSTRAINT uq_user_job UNIQUE (user_id, job_id)`.
3. Índices B-tree: `idx_jobs_fuzzy_hash`, `idx_jobs_active_prov`, `idx_notif_user_job`.
4. **NO** crear el índice HNSW de `jobs.embedding` (irá en la migración `0002`, Fase 3.4, tras la primera carga de embeddings).

**`downgrade()`:**
- Orden inverso: soltar índices → dropear tablas → (decisión pendiente: dropear `EXTENSION vector` o no).

### Paso 7 — `app/db/session.py` *(paso 4 del prompt original, cortado — contenido propuesto)*

Ubicación propuesta: `app/db/` (consistente con `app/schemas/`, `app/ingestion/`),
junto a `db/migrations/` en la raíz.

Contenido (solo conexión, sin modelos ni repositorios):
- `create_async_engine(DATABASE_URL, pool_pre_ping=True)`
- `async_sessionmaker(engine, expire_on_commit=False)`
- `get_session()` — generador async como dependencia FastAPI
- helper de cierre/disposal del engine
- `app/db/__init__.py` exportando lo público

### Paso 8 — Verificación
```bash
docker compose exec app alembic upgrade head
docker compose exec app alembic current          # → 0001
docker compose exec postgres psql -U jobfinder -d jobfinder_db -c '\dt'   # 5 tablas
docker compose exec postgres psql -U jobfinder -d jobfinder_db -c '\d jobs'  # B-tree, SIN hnsw
curl -s http://localhost:8000/health
# reversibilidad:
docker compose exec app alembic downgrade base
docker compose exec app alembic upgrade head
```
- Revisar constraints UNIQUE (`user_profiles.user_id`, `notifications_log(user_id, job_id)`, `uq_user_job`) y `ON DELETE CASCADE`.

### Paso 9 — `TODO.md`
- Añadir tarea **0.5. Base de datos y migraciones (Alembic)** en la Fase 0 (hoy no existe en el roadmap) y marcarla al terminar.

### Paso 10 — Commit
- Commitear la implementación separada del plan.

---

## Riesgos y notas conocidas

1. **`DATABASE_URL` con `${}` en `.env`**: solo válida dentro del contenedor
   (manda `docker-compose.yml:63`). Ejecutar Alembic siempre vía
   `docker compose exec app ...`. Para correr en el host habría que expandir
   los `${}` en `.env` a mano.
2. **`DROP EXTENSION vector` en downgrade**: puede afectar a otros objetos que
   usen el tipo; valorar no dropearla.
3. **Caddy/puertos 80/443**: si están ocupados, levantar solo `postgres redis app`.
4. **Rebuild necesario**: los paquetes nuevos se instalan en `docker build`
   (el volumen solo monta el código).

## Decisiones pendientes

- [ ] Ubicación de `session.py`: `app/db/session.py` (recomendado) vs `db/session.py` en raíz.
- [ ] `downgrade()`: ¿dropear `EXTENSION vector` o dejarla instalada?
- [ ] Añadir la tarea 0.5 al `TODO.md` (paso 9) al ejecutar el plan.
