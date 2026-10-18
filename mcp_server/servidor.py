"""
SERVIDOR MCP — Parachute S.A.

Un único servidor MCP que abstrae TODAS las herramientas de los agentes
(FAQs, clima, seguridad y agenda). Transporte: Streamable HTTP.

Además de las tools expone:
  - Resources: la base de FAQs completa y la configuración operativa.
  - Prompts:   plantillas reutilizables para agendar un salto y para
               consultar si un día es apto.

Cada ejecución de herramienta emite un log con argumentos, duración y
resultado (a consola y a logs/mcp_server.log).

Ejecutar (desde la raíz del repo):
    python -m mcp_server.servidor
"""

from __future__ import annotations

import functools
import itertools
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")

from core import knowledge, operaciones  # noqa: E402
from core.config import (  # noqa: E402
    DIAS_MAX_PRONOSTICO,
    EMPRESA,
    HORARIOS_DISPONIBLES,
    LATITUD,
    LONGITUD,
    ZONA_HORARIA,
)

HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8000"))

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
(RAIZ / "logs").mkdir(exist_ok=True)
log = logging.getLogger("mcp.parachute")
log.setLevel(logging.INFO)
log.propagate = False
_fmt = logging.Formatter("%(asctime)s | MCP | %(levelname)-5s | %(message)s", "%H:%M:%S")
for _h in (logging.StreamHandler(sys.stdout), logging.FileHandler(RAIZ / "logs" / "mcp_server.log", encoding="utf-8")):
    _h.setFormatter(_fmt)
    log.addHandler(_h)

_contador = itertools.count(1)


def _resumen(texto: str, n: int = 160) -> str:
    plano = " ".join(str(texto).split())
    return plano if len(plano) <= n else plano[: n - 3] + "..."


def _con_logs(fn):
    """Envuelve una operación para registrar cada ejecución de la herramienta."""

    @functools.wraps(fn)
    def envoltura(*args, **kwargs):
        n = next(_contador)
        argumentos = json.dumps(kwargs if not args else {"args": list(args), **kwargs}, ensure_ascii=False)
        log.info("[#%03d] ▶ tool=%s args=%s", n, fn.__name__, argumentos)
        inicio = time.perf_counter()
        try:
            salida = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            ms = (time.perf_counter() - inicio) * 1000
            log.error("[#%03d] ✖ tool=%s %.0f ms error=%s: %s", n, fn.__name__, ms, type(exc).__name__, exc)
            raise
        ms = (time.perf_counter() - inicio) * 1000
        ok = True
        try:
            ok = json.loads(salida).get("ok", True)
        except (ValueError, AttributeError):
            pass
        nivel = logging.INFO if ok else logging.WARNING
        log.log(nivel, "[#%03d] ◀ tool=%s %.0f ms ok=%s resultado=%s", n, fn.__name__, ms, ok, _resumen(salida))
        return salida

    return envoltura


# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------
mcp = MCPServer(
    name="parachute-tools",
    title=f"Herramientas de {EMPRESA}",
    description="FAQs, clima (Open-Meteo), reglas de seguridad de salto y agenda de citas.",
    version="1.0.0",
    instructions=(
        f"Servidor de herramientas de {EMPRESA}. Usa consultar_faqs para información de la "
        "empresa, consultar_clima/evaluar_fecha para el pronóstico y el veredicto de seguridad, "
        "y las herramientas de agenda para registrar o cancelar citas."
    ),
    log_level="WARNING",  # el log interesante es el de herramientas (arriba)
)

# Tools: todas las operaciones de core/operaciones.py
for _op in operaciones.CATALOGO:
    mcp.tool()(_con_logs(_op))


# Resources -----------------------------------------------------------------
@mcp.resource(
    "parachute://faqs",
    name="faqs",
    title="Base de conocimiento (FAQs)",
    description="Documento completo de preguntas frecuentes de Parachute S.A.",
    mime_type="text/markdown",
)
def recurso_faqs() -> str:
    log.info("resource leído: parachute://faqs")
    return knowledge.documento_completo()


@mcp.resource(
    "parachute://operacion",
    name="operacion",
    title="Configuración operativa",
    description="Zona de aterrizaje, horarios de salto y ventana de pronóstico.",
    mime_type="application/json",
)
def recurso_operacion() -> str:
    log.info("resource leído: parachute://operacion")
    return json.dumps(
        {
            "empresa": EMPRESA,
            "zona_aterrizaje": {"latitud": LATITUD, "longitud": LONGITUD, "zona_horaria": ZONA_HORARIA},
            "horarios": HORARIOS_DISPONIBLES,
            "dias_max_pronostico": DIAS_MAX_PRONOSTICO,
        },
        ensure_ascii=False,
        indent=2,
    )


# Prompts -------------------------------------------------------------------
@mcp.prompt(name="agendar_salto", title="Agendar un salto", description="Solicitud estándar para agendar un salto tándem.")
def prompt_agendar(nombre: str, telefono: str, fecha: str, hora: str = "08:30") -> str:
    log.info("prompt solicitado: agendar_salto")
    return (
        f"Quiero agendar un salto tándem el {fecha} a las {hora}. "
        f"Mi nombre es {nombre} y mi teléfono es {telefono}. "
        "Verifica primero el clima y el veredicto de seguridad antes de registrar la cita."
    )


@mcp.prompt(name="dia_apto", title="¿Es apto el día?", description="Pregunta si una fecha es segura para saltar.")
def prompt_dia_apto(fecha: str) -> str:
    log.info("prompt solicitado: dia_apto")
    return f"¿Las condiciones del {fecha} son seguras para saltar? Dame el veredicto y los valores del clima."


def main() -> None:
    log.info("=" * 70)
    log.info("Servidor MCP '%s' — transporte Streamable HTTP", mcp.name)
    log.info("Endpoint: http://%s:%d/mcp", HOST, PORT)
    log.info("Tools (%d): %s", len(operaciones.CATALOGO), ", ".join(f.__name__ for f in operaciones.CATALOGO))
    log.info("Resources: parachute://faqs, parachute://operacion | Prompts: agendar_salto, dia_apto")
    log.info("=" * 70)
    mcp.run(transport="streamable-http", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
