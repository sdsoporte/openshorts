"""Quién publica, detrás de una interfaz.

El sistema tiene que poder probarse de punta a punta sin publicar de verdad y sin
fallar porque no publicó: por eso el modo por defecto es **simulado**. El
publicador real (que llama a ``POST /api/social/post`` de OpenShorts) entra en la
rebanada 4 y no cambia nada de lo de arriba: el planificador sólo conoce esta
interfaz.

``PUBLISH_MODE=simulate`` (default) · ``live`` para el real.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field


@dataclass
class PublishResult:
    ok: bool
    external_urls: dict = field(default_factory=dict)
    publish_job_id: str | None = None
    error: str | None = None


class Publisher:
    """Contrato mínimo: publicar un clip en un canal, o decir por qué no."""

    name = "base"

    def publish(self, *, clip: dict, channel: dict) -> PublishResult:
        raise NotImplementedError


class SimulatedPublisher(Publisher):
    """Marca el clip como publicado sin tocar la red.

    Sirve para el e2e completo: el calendario avanza, los estados cambian y las
    URLs quedan apuntando a un dominio que no existe a propósito, para que nadie
    confunda una prueba con una publicación real.
    """

    name = "simulate"

    def __init__(self, *, domain: str = "simulated.invalid", should_fail: bool = False):
        self.domain = domain
        self.should_fail = should_fail

    def publish(self, *, clip: dict, channel: dict) -> PublishResult:
        if self.should_fail:
            return PublishResult(ok=False, error="simulated failure")
        publish_id = f"sim-{uuid.uuid4().hex[:12]}"
        platforms = [p for p in (channel.get("platforms") or "").split(",") if p]
        urls = {p: f"https://{self.domain}/{channel['slug']}/{p}/{clip['id']}"
                for p in platforms}
        return PublishResult(ok=True, external_urls=urls, publish_job_id=publish_id)


class OpenShortsPublisher(Publisher):
    """Publica contra ``POST /api/social/post`` de OpenShorts.

    Todavía no está cableado: la rebanada 4 le pasa el ``base_url`` y la
    credencial, y resuelve el problema de fondo (OpenShorts exige que el job
    siga existiendo, y su retención es de 24 h).
    """

    name = "open-shorts"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def publish(self, *, clip: dict, channel: dict) -> PublishResult:
        raise NotImplementedError(
            "OpenShortsPublisher llega en la rebanada 4; usá PUBLISH_MODE=simulate")


def from_env() -> Publisher:
    """El publicador que pide el entorno. Simulado salvo que se pida lo contrario."""
    mode = (os.environ.get("PUBLISH_MODE") or "simulate").strip().lower()
    if mode in ("live", "openshorts", "open-shorts"):
        base = os.environ.get("OPENSHORTS_BASE_URL") or "http://127.0.0.1:8001"
        return OpenShortsPublisher(base)
    return SimulatedPublisher(
        domain=os.environ.get("SIMULATED_PUBLISH_DOMAIN") or "simulated.invalid")
