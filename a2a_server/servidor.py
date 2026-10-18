"""
SERVIDOR A2A — Parachute S.A.

Expone el Multi Agent System (arquitectura CENTRALIZADA: orquestador +
especialistas FAQs, Clima, Seguridad y Agenda) como UN SOLO agente A2A.
Hacia afuera solo se ve un Agent Card con sus skills; los especialistas y el
servidor MCP quedan detrás.

    Cliente A2A (terminal) ──JSON-RPC/A2A──▶ este servidor
                                               └─ Orquestador (OpenAI Agents SDK)
                                                   ├─ FAQs ──────┐
                                                   ├─ Clima ─────┤
                                                   ├─ Seguridad ─┼──MCP (Streamable HTTP)──▶ mcp_server
                                                   └─ Agenda ────┘

- Agent Card:  GET http://127.0.0.1:9999/.well-known/agent-card.json
- JSON-RPC:    POST http://127.0.0.1:9999/
- La conversación se conserva por `context_id` (multi-turno).
- Mientras trabaja, el agente publica actualizaciones de estado (streaming)
  indicando qué especialista está consultando.

Ejecutar (desde la raíz del repo, con el servidor MCP ya corriendo):
    python -m a2a_server.servidor
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")

from a2a.helpers import new_task_from_user_message, new_text_message, new_text_part  # noqa: E402
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater  # noqa: E402
from a2a.types import (  # noqa: E402
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    TaskState,
)
from agents import RunHooks, Runner  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

import centralizada  # noqa: E402
from core import mcp_cliente  # noqa: E402
from core.config import EMPRESA, MODEL_NAME  # noqa: E402

HOST = os.getenv("A2A_HOST", "127.0.0.1")
PORT = int(os.getenv("A2A_PORT", "9999"))
URL_PUBLICA = os.getenv("A2A_URL", f"http://{HOST}:{PORT}")
MAX_TURNOS = 20

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
(RAIZ / "logs").mkdir(exist_ok=True)
log = logging.getLogger("a2a.parachute")
log.setLevel(logging.INFO)
log.propagate = False
_fmt = logging.Formatter("%(asctime)s | A2A | %(levelname)-5s | %(message)s", "%H:%M:%S")
for _h in (logging.StreamHandler(sys.stdout), logging.FileHandler(RAIZ / "logs" / "a2a_server.log", encoding="utf-8")):
    _h.setFormatter(_fmt)
    log.addHandler(_h)


def _corto(texto: str, n: int = 140) -> str:
    plano = " ".join(str(texto).split())
    return plano if len(plano) <= n else plano[: n - 3] + "..."


# Herramientas del orquestador = especialistas expuestos con as_tool
ESPECIALISTAS = {
    "consultar_informacion": "Especialista FAQs",
    "consultar_pronostico": "Especialista Clima",
    "dictaminar_seguridad": "Oficial de Seguridad",
    "gestionar_agenda": "Especialista Agenda",
}


# --------------------------------------------------------------------------
# Hooks: log + actualizaciones de estado A2A por cada especialista invocado
# --------------------------------------------------------------------------
class HooksA2A(RunHooks):
    def __init__(self, updater: TaskUpdater, etiqueta: str):
        self.updater = updater
        self.etiqueta = etiqueta
        self._inicios: dict[str, float] = {}

    async def on_tool_start(self, context, agent, tool) -> None:
        nombre = getattr(tool, "name", str(tool))
        self._inicios[nombre] = time.perf_counter()
        destino = ESPECIALISTAS.get(nombre, nombre)
        log.info("%s   ↳ %s llama a %s (%s)", self.etiqueta, agent.name, destino, nombre)
        await self.updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=new_text_message(f"Consultando a {destino}..."),
        )

    async def on_tool_end(self, context, agent, tool, result) -> None:
        nombre = getattr(tool, "name", str(tool))
        ms = (time.perf_counter() - self._inicios.pop(nombre, time.perf_counter())) * 1000
        log.info("%s   ↲ %s respondió en %.0f ms: %s", self.etiqueta, ESPECIALISTAS.get(nombre, nombre), ms, _corto(result))


# --------------------------------------------------------------------------
# AgentExecutor: envuelve el Multi Agent System
# --------------------------------------------------------------------------
class ParachuteAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.orquestador = centralizada.construir_orquestador()
        self._historiales: dict[str, list] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        etiqueta = f"[ctx {task.context_id[:8]} | task {task.id[:8]}]"
        texto = context.get_user_input().strip()
        log.info("%s ◀ mensaje recibido: %r", etiqueta, _corto(texto))

        if not texto:
            await updater.reject(new_text_message("No recibí texto en el mensaje."))
            log.warning("%s mensaje vacío: rechazado", etiqueta)
            return

        await updater.start_work(new_text_message("Procesando tu solicitud..."))
        lock = self._locks.setdefault(task.context_id, asyncio.Lock())
        async with lock:
            historial = self._historiales.get(task.context_id, [])
            entrada = historial + [{"role": "user", "content": texto}]
            log.info("%s ▶ ejecutando orquestador (turnos previos en contexto: %d)", etiqueta,
                     sum(1 for m in historial if isinstance(m, dict) and m.get("role") == "user"))
            inicio = time.perf_counter()
            try:
                resultado = await Runner.run(
                    self.orquestador, entrada, max_turns=MAX_TURNOS, hooks=HooksA2A(updater, etiqueta)
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("%s ✖ error ejecutando el MAS: %s", etiqueta, exc)
                await updater.failed(new_text_message(f"Ocurrió un error interno: {type(exc).__name__}: {exc}"))
                return

            self._historiales[task.context_id] = resultado.to_input_list()

        respuesta = str(resultado.final_output)
        segundos = time.perf_counter() - inicio
        await updater.add_artifact(parts=[new_text_part(text=respuesta, media_type="text/plain")], name="respuesta")
        await updater.complete()
        log.info("%s ▶ respuesta enviada en %.1f s: %r", etiqueta, segundos, _corto(respuesta))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        log.info("[task %s] cancelación solicitada", (context.task_id or "")[:8])
        if context.current_task:
            updater = TaskUpdater(event_queue, context.current_task.id, context.current_task.context_id)
            await updater.cancel(new_text_message("Tarea cancelada por el cliente."))


# --------------------------------------------------------------------------
# Agent Card: el MAS se presenta como un solo agente con 4 skills
# --------------------------------------------------------------------------
SKILLS = [
    AgentSkill(
        id="faqs",
        name="Información de la empresa",
        description="Responde preguntas frecuentes de Parachute S.A.: precios, requisitos, "
        "duración, políticas de cancelación, equipo y restricciones médicas.",
        tags=["faq", "rag", "informacion"],
        examples=["¿Cuánto cuesta un salto tándem?", "¿Cuál es la edad mínima para saltar?"],
    ),
    AgentSkill(
        id="clima",
        name="Pronóstico de la zona de aterrizaje",
        description="Consulta Open-Meteo para una fecha (máx. 16 días) y reporta temperatura, "
        "precipitación, nubosidad, viento y ráfagas.",
        tags=["clima", "open-meteo", "pronostico"],
        examples=["¿Cómo estará el clima el próximo sábado?"],
    ),
    AgentSkill(
        id="seguridad",
        name="Dictamen de seguridad de salto",
        description="Aplica los criterios oficiales y dictamina si un día es IDEAL, MARGINAL o PROHIBIDO.",
        tags=["seguridad", "veredicto"],
        examples=["¿Es seguro saltar mañana?"],
    ),
    AgentSkill(
        id="agenda",
        name="Agenda de saltos",
        description="Consulta horarios disponibles y registra, lista o cancela citas, solo en días "
        "con veredicto IDEAL o MARGINAL.",
        tags=["agenda", "citas", "reservas"],
        examples=["Quiero agendar un salto el viernes a las 08:30, soy Ana López, tel 5555-1234"],
    ),
]

AGENT_CARD = AgentCard(
    name=f"Agente {EMPRESA}",
    description=(
        f"Asistente de {EMPRESA} (saltos en paracaídas, Guatemala). Internamente es un sistema "
        "multiagente centralizado (orquestador + especialistas) cuyas herramientas viven en un servidor MCP."
    ),
    version="1.0.0",
    provider=AgentProvider(organization=EMPRESA, url=URL_PUBLICA),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    capabilities=AgentCapabilities(streaming=True),
    supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=URL_PUBLICA, protocol_version="1.0")],
    skills=SKILLS,
)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app):
    log.info("Conectando al servidor MCP en %s ...", mcp_cliente.MCP_URL)
    servidor = await mcp_cliente.conectar()
    # Listado crudo (sin el filtro por agente) solo para el log de arranque
    herramientas = [t.name for t in (await servidor.session.list_tools()).tools]
    log.info("MCP conectado. Herramientas disponibles: %s", ", ".join(herramientas))
    try:
        yield
    finally:
        await mcp_cliente.desconectar()
        log.info("MCP desconectado.")


def crear_app() -> Starlette:
    handler = DefaultRequestHandler(
        agent_executor=ParachuteAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=AGENT_CARD,
    )
    rutas = []
    rutas.extend(create_agent_card_routes(AGENT_CARD))
    rutas.extend(create_jsonrpc_routes(handler, "/"))
    return Starlette(routes=rutas, lifespan=lifespan)


def main() -> None:
    log.info("=" * 70)
    log.info("Servidor A2A '%s' (modelo %s)", AGENT_CARD.name, MODEL_NAME)
    log.info("Agent Card: %s/.well-known/agent-card.json", URL_PUBLICA)
    log.info("Skills: %s", ", ".join(s.id for s in SKILLS))
    log.info("=" * 70)
    uvicorn.run(crear_app(), host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
