"""Estados de un clip y sus transiciones, sin base de datos ni HTTP.

El contrato (``docs/reviewer-contract.md``) fija seis estados y una regla que no
se puede aflojar: **el rechazo es terminal**. Todo lo demás es reintentable,
porque una publicación que falla no debería perder el clip que el usuario ya
aprobó.

Mantener esto como funciones puras es lo que permite probar la máquina de
estados sin levantar nada: los endpoints y el planificador sólo la llaman.
"""

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
SCHEDULED = "scheduled"
PUBLISHED = "published"
FAILED = "failed"

STATES = (PENDING, ACCEPTED, REJECTED, SCHEDULED, PUBLISHED, FAILED)
DECISIONS = ("accept", "reject")

# Estados en los que un clip ya no recibe decisiones humanas.
TERMINAL = (REJECTED, PUBLISHED)

# Lo que el planificador puede programar.
SCHEDULABLE = (ACCEPTED, FAILED)


class TransitionError(ValueError):
    """La transición pedida no existe para el estado actual."""


class AlreadyDecided(TransitionError):
    """El clip ya tenía una decisión humana distinta."""

    def __init__(self, current: str, requested: str):
        super().__init__(f"clip is {current}, cannot apply {requested}")
        self.current = current
        self.requested = requested


def apply_decision(current: str, decision: str) -> str:
    """Estado luego de una decisión humana: ``accept`` o ``reject``.

    Idempotente por diseño: repetir la misma decisión devuelve el mismo estado
    en vez de fallar (un swipe repetido por red lenta no es un conflicto).
    Cualquier otra combinación es ``AlreadyDecided``.
    """
    decision = (decision or "").strip().lower()
    if decision not in DECISIONS:
        raise TransitionError(f"unknown decision {decision!r}")
    if current == PENDING:
        return ACCEPTED if decision == "accept" else REJECTED
    if current == ACCEPTED and decision == "accept":
        return ACCEPTED
    if current == REJECTED and decision == "reject":
        return REJECTED
    raise AlreadyDecided(current, decision)


def schedule(current: str, when) -> str:
    """Pasar a ``scheduled`` con una fecha, desde ``accepted`` o ``failed``."""
    if when is None:
        raise TransitionError("scheduling needs a date")
    if current not in SCHEDULABLE:
        raise TransitionError(f"cannot schedule a clip in state {current!r}")
    return SCHEDULED


def mark_published(current: str) -> str:
    if current != SCHEDULED:
        raise TransitionError(f"cannot publish a clip in state {current!r}")
    return PUBLISHED


def mark_failed(current: str, reason: str = "") -> str:
    """Una publicación que falla se conserva: el clip sigue aprobado."""
    if current not in (SCHEDULED, ACCEPTED):
        raise TransitionError(f"cannot fail a clip in state {current!r}")
    return FAILED


def unschedule(current: str) -> str:
    """Volver a la bolsa de aprobados sin fecha (desprogramar)."""
    if current not in (SCHEDULED, FAILED):
        raise TransitionError(f"cannot unschedule a clip in state {current!r}")
    return ACCEPTED
