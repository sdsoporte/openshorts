# Cleanup de tipos sobre el árbol mergeado (conocidos-pendientes)

Los 158 errores de pyright que aparecen hoy son **preexistentes** en este árbol,
que está 245 commits atrás de upstream. Arreglarlos acá es trabajo que el merge
tira: `app.py` (138 de los 158) y `main.py` (12) son los dos archivos que
upstream reescribió por completo.

**Objetivo:** llevar pyright a **0 errores** y `# type: ignore` a cero nuevos,
sobre el árbol ya mergeado con `upstream/main`, con las dos suites verdes.

## Estado que hizo visible el problema (ya resuelto)

Pyright no resolvía ningún import de terceros porque no había intérprete con las
dependencias: cada import del pipeline era `reportMissingImports` y sus cascadas
tapaban todo lo demás. Eso está arreglado y **no se puede deshacer sin volver a
la ceguera**:

- `.venv-analyzer/` (gitignored) creado con `uv`, con `torch 2.15.0+cpu`,
  `ultralytics`, `mediapipe`, `scenedetect`, `google-genai`, `opencv`, `numpy`,
  `starlette`, `fastapi`, `httpx`, `sqlalchemy`, `python-dotenv`.
- `pyrightconfig.json` apunta ahí (`venvPath`/`venv`) y **no baja ninguna
  severidad**.

Recrear el entorno si hace falta:

```sh
uv venv .venv-analyzer --python 3.12
uv pip install --python .venv-analyzer/bin/python \
  --index-url https://download.pytorch.org/whl/cpu torch torchvision
uv pip install --python .venv-analyzer/bin/python \
  numpy opencv-python-headless google-genai pydantic starlette fastapi httpx \
  sqlalchemy python-dotenv scenedetect mediapipe ultralytics
```

Correr el inventario:

```sh
.venv-analyzer/bin/pyright --outputjson app.py main.py | python3 -c \
  "import json,sys; d=json.load(sys.stdin); \
   print(sum(1 for x in d['generalDiagnostics'] if x['severity']=='error'))"
```

## Inventario medido (2026-11-15, antes del merge)

`158` errores, así repartidos:

| Archivo | Errores |
| --- | ---: |
| `app.py` | 138 |
| `main.py` | 12 |
| `reframe_v2.py` | 3 |
| `thumbnail.py` | 3 |
| `hook_grounding.py` | 1 |
| `layout_picker.py` | 1 |

Por regla:

| Regla | Cuántos | Qué son |
| --- | ---: | --- |
| `reportOptionalMemberAccess` | 77 | acceso a atributo sobre algo que pyright cree `None` (pipes, módulos, resultados de API) |
| `reportArgumentType` | 33 | uniones de terceros (`contents` de `generate_content`, `params` de yt-dlp) |
| `reportAttributeAccessIssue` | 20 | stubs incompletos: `mediapipe.solutions`, `Tensor.boxes`, `yt_dlp.version` |
| **`reportPossiblyUnboundVariable`** | **18** | **variables usadas antes de asignarse en algún camino → `NameError` real** |
| `reportGeneralTypeIssues` | 4 | `raise None`, iterar `None` |
| `reportAssignmentType` | 3 | handlers y asignaciones |
| `reportCallIssue` / `reportOptionalIterable` / `reportOperatorIssue` | 3 | sueltos |

## Orden de trabajo (por valor, no por archivo)

1. **`reportPossiblyUnboundVariable` (18)** — son candidatos a crash real. Cada
   uno necesita mirar el flujo, no un `setdefault`.
2. **`reportGeneralTypeIssues` (4)** — `raise None` e iterar `None` también son
   crashes.
3. **`reportOptionalMemberAccess` (77)** — en su mayoría el código resuelve por
   contrato (un `PIPE` que existe, un módulo cargado condicionalmente). Angostar
   con un `if x is None: raise/return`, no con `assert` en producción.
4. **`reportArgumentType` (33)** — las uniones de terceros se resuelven con un
   `cast` documentado o aceptando el tipo del SDK; nunca con `# type: ignore`
   mudo.

## Reglas del trabajo

- Un commit por archivo, con la suite completa verde antes de pasar al siguiente.
- Prohibido agregar `# type: ignore` sin comentario que explique por qué el tipo
  del SDK es incorrecto; si el stub miente, va un `cast`, no un ignore.
- Si un hallazgo desaparece porque upstream lo arregló, se anota en el log y no
  se toca.
- Este branch **no se mergea antes del merge de upstream**: se rebasea sobre él.

## Criterio de salida

- `pyright` sobre los archivos del pipeline: **0 errores**.
- `pytest tests/` (860+) y `pytest reviewer/tests` (33+) verdes.
- Ningún `type: ignore` nuevo sin justificación.
- Sin cambios de comportamiento: los tests que ya existían no se tocan para que
  pasen.

## Log

**L1** (usuario, literal): "ok vamos con esto como conocidos-pendientes y
corregirlos sobre el árbol mergeado pero deja creada la nueva rama con el
objetivo para corregir. para que no se nos quede en solo conocidos pendientes."

**L2** (hecho antes de diferir): 8 bugs reales de `str | None` corregidos en
`layout_picker`, `hook_grounding`, `screencast_layout`, `active_speaker`,
`gemini_worker` y `main.py` (los cuatro `json.loads(response.text)`, los
`files.get/delete(name=...)`, el `setattr` de `ENABLED` y el
`ThinkingLevel` del SDK).

**L3** (evidencia): `git diff -U0 app.py` no tenía hunks cuando se midió el
inventario — los 138 de `app.py` son preexistentes, no de este trabajo.
