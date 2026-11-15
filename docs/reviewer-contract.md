# Contrato del Reviewer (swipe → calendario)

Qué se construye, qué ya existe en OpenShorts y cuál es la tabla mínima. El
Reviewer es la única pieza nueva: OpenShorts genera los clips y expone la
publicación; el Reviewer guarda la decisión y el calendario.

## Qué ya existe (verificado en este fork, `main` @ `d01c2ba`)

| Necesidad | Endpoint / archivo | Notas |
| --- | --- | --- |
| Encolar un video largo | `POST /api/process` | JSON o multipart; `webhook_url` para el callback |
| Consultar el job | `GET /api/status/{job_id}` | `status`, `logs`, `result.clips[]` |
| Datos del clip para la tarjeta | `result.clips[i]` | `predicted_score`, `viral_hook_text`, títulos, `start`, `end`, `layout_ranges`, `auto_hook` |
| Descargar el clip | `GET /videos/{job_id}/{archivo}.mp4` | vive bajo `output/`, retenido `JOB_RETENTION_SECONDS` |
| Publicar / programar | `POST /api/social/post` | `{job_id, clip_index, platforms[], title?, description?, scheduled_date?, timezone?}` |
| Ver la cola programada | `GET /api/social/scheduled` | proxy de Upload-Post, filtrado por perfil |
| Cancelar una programación | `DELETE /api/social/scheduled/{job_id}` | idem |

## La restricción que define el diseño

`POST /api/social/post` exige que el job siga existiendo en OpenShorts
(`job_id in jobs`), y el job se reconstruye desde `output/` sólo si el
directorio está. Con la retención por defecto (24 h en self-host, 1 h en cloud),
**programar a tres días vista y publicar con ese endpoint no funciona**.

Tres salidas, en orden de esfuerzo:

1. **MVP**: subir `JOB_RETENTION_SECONDS` a la ventana de programación (p. ej.
   `2592000` = 30 días) y publicar desde OpenShorts cuando llega la hora. Una
   variable, cero código.
2. **Recomendado a mediano plazo**: copia durable en S3/R2 (ya existe
   `s3_uploader.py`) y publicar desde el backend propio, sin depender del estado
   de OpenShorts.
3. **Publicación directa** a cada plataforma (YouTube Data API, TikTok Content
   Posting, IG Graph): es donde vive el costo real — cuota de 1600 unidades por
   upload en YouTube (~6 por día por proyecto), app aprobada en TikTok, cuenta
   business y revisión en Meta.

El contrato de abajo es independiente de cuál elijas: sólo cambia quién hace el
último salto.

## Estados de un clip

```text
pending ──accept──→ accepted ──schedule──→ scheduled ──publish──→ published
   │                    ↑                        │
   │                    └──────── retry ─────────┴──→ failed
   └──reject──→ rejected   (terminal, no se publica)
```

- `pending`: generado, esperando decisión humana.
- `accepted`: aprobado, todavía sin fecha.
- `scheduled`: aprobado con `scheduled_for` (lo que ve el calendario).
- `published`: subido; guarda `external_urls`.
- `failed`: el publish falló; reintentable (`failed → scheduled`).
- `rejected`: terminal. Se conserva la fila (sirve para no volver a mostrarlo).

## Tabla mínima

```sql
create table channels (               -- un calendario por canal
  id              uuid primary key,
  slug            text unique not null,
  uploadpost_profile text not null,    -- el perfil que publica
  platforms       text[] not null,     -- {tiktok,instagram,youtube}
  timezone        text not null default 'UTC',
  slots_per_day   int  not null default 1,
  active          bool not null default true
);

create table reviewer_clips (
  id              uuid primary key,
  job_id          text not null,             -- OpenShorts
  clip_index      int  not null,             -- dentro del job
  storage_url     text,                      -- copia durable (S3/R2)
  duration_s      numeric(6,2) not null,
  predicted_score int,
  hook_text       text,
  title           text,
  state           text not null default 'pending'
                  check (state in ('pending','accepted','rejected',
                                   'scheduled','published','failed')),
  channel_id      uuid references channels(id),
  scheduled_for   timestamptz,
  decided_at      timestamptz,
  decided_by      text,
  publish_job_id  text,                      -- id que devuelve OpenShorts/Upload-Post
  published_at    timestamptz,
  external_urls   jsonb,
  error           text,
  created_at      timestamptz not null default now(),
  unique (job_id, clip_index)                -- idempotencia de la ingesta
);

create index on reviewer_clips (state, scheduled_for);
create index on reviewer_clips (channel_id, scheduled_for);
```

`unique (job_id, clip_index)` es lo que hace la ingesta idempotente: n8n puede
reintentar el callback sin duplicar tarjetas.

## Los endpoints del Reviewer

### 1. Ingesta (la llama n8n, no el teléfono)

`POST /api/ingest`

```json
{ "job_id": "94e3dd20-...", "clips": [ { "clip_index": 0, "video_url": "/videos/...mp4",
  "predicted_score": 72, "viral_hook_text": "...", "video_title_for_youtube_short": "..." } ] }
```

Descarga cada clip desde OpenShorts **en el momento** (la retención corre),
lo sube al storage durable, y hace `upsert` por `(job_id, clip_index)`.
Devuelve `{ "ingested": n, "skipped": m }`. Reintentable sin duplicar.

### 2. Cola de swipe

`GET /api/queue?channel=<slug>&limit=20&cursor=<ts>`

Devuelve sólo `pending` del canal, ordenado por `predicted_score desc`,
`created_at desc`. Cada item:

```json
{ "clip_id": "...", "video_url": "<firmada o CDN>", "duration_s": 45.2,
  "predicted_score": 72, "hook_text": "...", "title": "...",
  "channel": "reels-cl", "suggested_slot": "2026-11-17T18:00:00-03:00" }
```

`suggested_slot` es opcional pero es lo que evita una segunda pantalla: el
primer hueco libre del calendario de ese canal.

### 3. Decisión

`POST /api/decisions`

```json
{ "clip_id": "...", "decision": "accept", "channel_id": "...",
  "scheduled_for": "2026-11-17T18:00:00-03:00" }
```

- `reject` → `state=rejected`, `decided_at=now()`; no toca el calendario.
- `accept` + `scheduled_for` → `state=scheduled`.
- `accept` sin fecha → `state=accepted`, y el planificador le asigna hueco.

**Idempotente por `clip_id`**: repetir la llamada devuelve el mismo estado
(evita el doble swipe por red lenta). Responde `409` si el clip ya no estaba
`pending` con una decisión distinta.

### 4. Calendario

`GET /api/calendar?channel=<slug>&from=&to=`

Lista `scheduled` y `published` del rango con `scheduled_for`, `channel`,
`platforms`, `publish_job_id` y `external_urls`. Para confirmar contra la
realidad, cruza con `GET /api/social/scheduled` de OpenShorts.

## El planificador (backend, no n8n)

Cada 5–15 min, para cada canal activo:

1. Toma `accepted` más antiguos primero, hasta llenar los huecos de
   `slots_per_day` de los próximos días (respetando `timezone` y un mínimo de
   separación entre publicaciones).
2. `POST /api/social/post` en OpenShorts con `job_id`, `clip_index`,
   `platforms`, `scheduled_date` y `timezone` del canal.
3. Guarda `publish_job_id` y pasa a `scheduled`; si OpenShorts responde error,
   deja `failed` con el mensaje en `error` y reintenta con backoff.

n8n queda para lo perimetral: recibir la URL, llamar `/api/process`, esperar el
webhook, llamar `/api/ingest`, y avisar por chat cuando un `publish` falla. No
para el bucle del calendario.

## Multi-canal desde el día uno

`channels` es la tabla que hace la diferencia: un canal = un calendario = un
`uploadpost_profile` = un conjunto de `platforms`. Empezar con uno es una fila;
agregar el segundo canal no toca ni una línea de código, sólo inserta otra fila
y le asigna clips. `reviewer_clips.channel_id` ya está en el esquema por eso.

## Casos de prueba que conviene escribir primero

1. Ingesta repetida del mismo `(job_id, clip_index)` no duplica filas.
2. `reject` es terminal: un `accept` posterior sobre el mismo clip da `409`.
3. Dos `accept` concurrentes del mismo clip dejan una sola decisión.
4. El planificador no programa dos clips del mismo canal en el mismo minuto.
5. Un clip `scheduled` cuya fecha pasó y no se publicó pasa a `failed`, no
   desaparece del calendario.
6. Un canal inactivo no recibe clips nuevos del planificador.

## Qué NO hace falta construir

- Autenticación de plataformas, tokens y refresh: lo hace Upload-Post.
- Generación de clips, hooks, títulos, subtítulos: OpenShorts.
- Almacenamiento de los videos largos: OpenShorts los borra al terminar
  (`SOURCE_RETENTION_SECONDS`); sólo se conservan los clips.
