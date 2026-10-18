"""
INTERFAZ DE TERMINAL — Cliente A2A

La terminal ya no ejecuta los agentes en proceso: descubre al agente remoto
por su Agent Card y le envía cada mensaje por A2A (JSON-RPC, streaming).
Mantiene el `context_id` para que la conversación sea multi-turno.

Ejecutar (con el servidor MCP y el servidor A2A corriendo):
    python cliente.py
    python cliente.py --url http://127.0.0.1:9999 --sin-streaming
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState

load_dotenv()

ANCHO = 74
GRIS, CIAN, VERDE, ROJO, RESET = "\033[90m", "\033[96m", "\033[92m", "\033[91m", "\033[0m"

ESTADOS_FINALES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


def _banner(card) -> None:
    print("=" * ANCHO)
    print(f" {card.name}  (v{card.version}) — cliente A2A")
    print("-" * ANCHO)
    print(f" {card.description}")
    print(f" Endpoint: {card.supported_interfaces[0].url}  [{card.supported_interfaces[0].protocol_binding}]")
    print(" Skills:")
    for s in card.skills:
        print(f"   • {s.name} ({s.id})")
    print(" Escribe 'salir' para terminar.")
    print("=" * ANCHO)


async def _enviar(client, texto: str, context_id: str | None) -> str | None:
    """Envía un mensaje y muestra el progreso. Devuelve el context_id de la conversación."""
    mensaje = new_text_message(texto, context_id=context_id, role=Role.ROLE_USER)
    respuesta_impresa = False
    ultimo_estado = ""

    async for evento in client.send_message(SendMessageRequest(message=mensaje)):
        if evento.HasField("task"):
            tarea = evento.task
            context_id = tarea.context_id or context_id
            # Respuesta no-streaming (o snapshot final): artefactos de la tarea
            if tarea.status.state in ESTADOS_FINALES and not respuesta_impresa:
                textos = [get_artifact_text(a) for a in tarea.artifacts]
                if any(textos):
                    print(f"\n{VERDE}Agente >{RESET} " + "\n".join(t for t in textos if t))
                    respuesta_impresa = True
                elif tarea.status.HasField("message"):
                    print(f"\n{ROJO}Agente >{RESET} {get_message_text(tarea.status.message)}")
                    respuesta_impresa = True

        elif evento.HasField("status_update"):
            upd = evento.status_update
            context_id = upd.context_id or context_id
            if upd.status.HasField("message"):
                txt = get_message_text(upd.status.message)
                if txt and txt != ultimo_estado:
                    color = ROJO if upd.status.state == TaskState.TASK_STATE_FAILED else GRIS
                    print(f"{color}  · {txt}{RESET}")
                    ultimo_estado = txt

        elif evento.HasField("artifact_update"):
            upd = evento.artifact_update
            context_id = upd.context_id or context_id
            print(f"\n{VERDE}Agente >{RESET} {get_artifact_text(upd.artifact)}")
            respuesta_impresa = True

        elif evento.HasField("message"):
            context_id = evento.message.context_id or context_id
            print(f"\n{VERDE}Agente >{RESET} {get_message_text(evento.message)}")
            respuesta_impresa = True

    if not respuesta_impresa:
        print(f"{ROJO}(el agente no devolvió respuesta){RESET}")
    return context_id


async def main(url: str, streaming: bool) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as http:
        # 1. Descubrimiento: obtener el Agent Card
        resolver = A2ACardResolver(httpx_client=http, base_url=url)
        try:
            card = await resolver.get_agent_card()
        except Exception as exc:  # noqa: BLE001
            print(f"{ROJO}No se pudo obtener el Agent Card en {url}: {exc}{RESET}")
            print("¿Está corriendo `python -m a2a_server.servidor`?")
            return

        # 2. Crear el cliente A2A a partir del card
        client = await create_client(
            agent=card,
            client_config=ClientConfig(streaming=streaming, httpx_client=http),
        )
        _banner(card)

        context_id: str | None = None
        try:
            while True:
                try:
                    entrada = (await asyncio.to_thread(input, f"\n{CIAN}Usuario >{RESET} ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nHasta luego.")
                    return
                if not entrada:
                    continue
                if entrada.lower() in {"salir", "exit", "quit", "bye"}:
                    print("Hasta luego.")
                    return
                try:
                    context_id = await _enviar(client, entrada, context_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"{ROJO}[error A2A] {type(exc).__name__}: {exc}{RESET}")
        finally:
            await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cliente A2A de terminal para Parachute S.A.")
    parser.add_argument("--url", default=os.getenv("A2A_URL", "http://127.0.0.1:9999"))
    parser.add_argument("--sin-streaming", action="store_true", help="usa message/send en lugar de streaming")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.url, streaming=not args.sin_streaming))
    except KeyboardInterrupt:
        print("\nHasta luego.")
