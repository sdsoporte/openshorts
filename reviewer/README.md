# Reviewer — swipe → calendario

Recibe los clips que generó OpenShorts, los presenta para decisión humana y
guarda la fecha de los aprobados. El contrato completo está en
[`docs/reviewer-contract.md`](../docs/reviewer-contract.md); esto es lo que ya
está implementado.

## Estado

| Rebanada | Contenido | Estado |
| --- | --- | --- |
| 1 | Estados, almacenamiento, `/api/channels`, `/api/ingest`, `/api/queue`, `/api/decisions`, `/api/calendar` | ✅ 16 tests |
| 2 | Planificador de huecos por canal (llamada a OpenShorts) | pendiente |
| 3 | SPA de swipe | pendiente |
| 4 | Ingesta real: descarga del clip + subida al storage durable | pendiente |

## Correr

```sh
# desarrollo: SQLite en ./reviewer.db
DATABASE_URL=sqlite:///./reviewer.db \
  uvicorn reviewer.api:create_app --factory --port 8090

# producción: cualquier URL de SQLAlchemy
DATABASE_URL=postgresql://user:pass@host/reviewer
```

No necesita dependencias nuevas: usa el `fastapi` y el `sqlalchemy` que ya trae
el backend de OpenShorts.

## Flujo mínimo

```sh
# 1. un canal = un calendario = un perfil de publicación
curl -s localhost:8090/api/channels -H 'content-type: application/json' -d '{
  "slug":"reels","uploadpost_profile":"perfil-1","platforms":["tiktok"],
  "timezone":"America/Argentina/Buenos_Aires","slots_per_day":1}'

# 2. la ingesta la llama n8n con lo que devolvió OpenShorts (idempotente)
curl -s localhost:8090/api/ingest -H 'content-type: application/json' -d '{
  "job_id":"94e3dd20","clips":[
    {"clip_index":0,"video_url":"/videos/94e3dd20/clip_1.mp4",
     "predicted_score":72,"viral_hook_text":"500 mil personas en vivo"}]}'

# 3. lo que la app muestra para deslizar
curl -s 'localhost:8090/api/queue?channel=reels'

# 4. la decisión: aceptar con fecha, o rechazar
curl -s localhost:8090/api/decisions -H 'content-type: application/json' -d '{
  "clip_id":"<id>","decision":"accept","channel_id":"<id>",
  "scheduled_for":"2026-11-17T18:00:00-03:00"}'

# 5. el calendario
curl -s 'localhost:8090/api/calendar?channel=reels&start=2026-11-15&end=2026-12-15'
```

## Garantías que los tests fijan

- **Ingesta idempotente** por `(job_id, clip_index)`: reintentar el callback de
  n8n devuelve `{"ingested": 0, "skipped": n}`, no duplica tarjetas.
- **Una decisión por clip**: repetir la misma es un no-op; cambiarla devuelve
  `409`.
- **El rechazo es terminal.**
- **Programar exige canal**: un clip sin canal no tiene calendario donde caer, así
  que la API responde `400` en vez de dejarlo invisible para el planificador.
- **El hueco sugerido respeta lo ya programado** de ese día.

## Notas de diseño

- SQLAlchemy **Core** con `DATABASE_URL`: SQLite en desarrollo, Postgres en
  producción, mismo código.
- `reviewer/` no importa nada de OpenShorts ni comparte su base: la única
  dependencia es el contrato HTTP (y en la rebanada 4, `POST /api/social/post`).
- Las horas de programación salen de `store.SLOT_HOURS` (12, 18, 21) recortadas a
  `slots_per_day`; cambiarlas es una línea, y por eso no hay lógica de negocio
  escondida ahí.
