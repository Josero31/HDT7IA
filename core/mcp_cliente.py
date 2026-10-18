"""
Cliente MCP compartido por todos los agentes.

Hay UNA sola conexión Streamable HTTP al servidor MCP. Cada especialista ve
únicamente sus herramientas gracias a un filtro dinámico que decide según el
agente que pide la lista (`ToolFilterContext.agent`). Así se conserva la
separación de responsabilidades de la HT5 aunque todas las tools vivan en el
mismo servidor.
"""

from __future__ import annotations

import os

from agents.mcp import MCPServerStreamableHttp, ToolFilterContext

from . import config  # noqa: F401  (carga .env)
from .operaciones import HERRAMIENTAS_POR_ESPECIALISTA

MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp")

# nombre del agente -> clave del especialista
_AGENTE_A_ESPECIALIDAD = {
    "Especialista FAQs": "faqs",
    "Especialista Clima": "clima",
    "Oficial de Seguridad": "seguridad",
    "Especialista Agenda": "agenda",
}

_servidor: MCPServerStreamableHttp | None = None


def _filtro_por_agente(ctx: ToolFilterContext, tool) -> bool:
    clave = _AGENTE_A_ESPECIALIDAD.get(ctx.agent.name)
    if clave is None:  # agente desconocido: no ve nada
        return False
    return tool.name in HERRAMIENTAS_POR_ESPECIALISTA[clave]


def servidor_mcp() -> MCPServerStreamableHttp:
    """Devuelve (creando si hace falta) el cliente MCP compartido. Hay que conectarlo con `conectar()`."""
    global _servidor
    if _servidor is None:
        _servidor = MCPServerStreamableHttp(
            name="parachute-tools",
            params={"url": MCP_URL, "timeout": 30, "sse_read_timeout": 300},
            cache_tools_list=True,
            client_session_timeout_seconds=60,
            tool_filter=_filtro_por_agente,
        )
    return _servidor


async def conectar() -> MCPServerStreamableHttp:
    servidor = servidor_mcp()
    try:
        await servidor.connect()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"No se pudo conectar al servidor MCP en {MCP_URL}. "
            "¿Está corriendo `python -m mcp_server.servidor`?"
        ) from exc
    return servidor


async def desconectar() -> None:
    global _servidor
    if _servidor is not None:
        await _servidor.cleanup()
        _servidor = None
