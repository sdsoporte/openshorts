# Reviewer: swipe → calendario (implementación)

Construye el componente descrito en `docs/reviewer-contract.md`: recibe los clips
que generó OpenShorts, los presenta para decisión humana y programa los
aprobados en el calendario de un canal.

## Specs

**S1.** La decisión es por clip y por canal: `accept` / `reject`, terminal al
rechazar. Un clip ya decidido no cambia de decisión (idempotencia; conflicto =
`409`).

**S2.** La ingesta es idempotente por `(job_id, clip_index)`: n8n puede reintentar
el callback sin duplicar tarjetas.

**S3.** La cola de swipe devuelve sólo `pending` del canal pedido, ordenado por
`predicted_score` descendente y antigüedad, con el primer hueco libre del
calendario como `suggested_slot`.

**S4.** Los estados son exactamente los del contrato:
`pending → accepted | rejected`, `accepted → scheduled`, `scheduled → published |
failed`, `failed → scheduled` (reintento).

**S5.** El almacenamiento es SQLAlchemy Core con `DATABASE_URL`, para correr en
SQLite en desarrollo y Postgres en producción sin cambiar el código.

**S6.** El servicio no habla con OpenShorts ni con Upload-Post en esta rebanada:
los endpoints que consumen esos servicios quedan detrás de una función que se
inyecta, para poder probar la lógica sin red.

## Rebanadas

| # | Contenido | Estado |
|---|---|---|
| 1 | Estado + almacenamiento + `/api/ingest`, `/api/queue`, `/api/decisions`, `/api/calendar` | en curso |
| 2 | Planificador (huecos por canal, llamada a OpenShorts, reintentos) | pendiente |
| 3 | SPA de swipe | pendiente |
| 4 | Ingesta real: descarga de clips + subida al storage durable | pendiente |

## Log

**L1** (usuario, literal): "inicia con la implementacion."

**L2** (decisión): el código vive en `reviewer/` dentro de este repositorio, en
FastAPI + SQLAlchemy, para reusar el stack del backend que ya existe. Si más
adelante conviene un despliegue aparte, se mueve la carpeta: no comparte nada
con el pipeline de OpenShorts.
