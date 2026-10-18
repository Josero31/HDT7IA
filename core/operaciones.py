"""
Lógica de las herramientas como funciones Python planas.

Antes (HT5/HT6) cada herramienta vivía directamente decorada con
`@function_tool`. Para la HT7 se separa la lógica del mecanismo de exposición:

  - `mcp_server/servidor.py` registra ESTAS funciones como tools del servidor MCP
    (lo que usan los agentes en ejecución normal).
  - `core/tools.py` las envuelve con `function_tool` (modo local, usado por los
    evals de la HT6 para instrumentar las herramientas en proceso).

Todas devuelven un string JSON para que el LLM reciba exactamente lo mismo en
ambos modos.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from . import knowledge, safety, scheduling, weather
from .config import DIAS_MAX_PRONOSTICO, HORARIOS_DISPONIBLES
from .domain import ErrorDeNegocio


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _error(mensaje: str) -> str:
    return _json({"ok": False, "error": mensaje})


# --------------------------------------------------------------------------
# Conocimiento (FAQs)
# --------------------------------------------------------------------------
def consultar_faqs(pregunta: str) -> str:
    """Busca en la base de conocimiento de FAQs de Parachute S.A.

    Args:
        pregunta: Pregunta del usuario tal como la formuló.
    """
    return knowledge.buscar(pregunta)


# --------------------------------------------------------------------------
# Tiempo / calendario
# --------------------------------------------------------------------------
def fecha_actual() -> str:
    """Devuelve la fecha de hoy y la última fecha que Open-Meteo puede pronosticar."""
    hoy = date.today()
    return _json(
        {
            "ok": True,
            "hoy": hoy.isoformat(),
            "maxima_pronosticable": (hoy + timedelta(days=DIAS_MAX_PRONOSTICO)).isoformat(),
            "dias_de_pronostico": DIAS_MAX_PRONOSTICO,
        }
    )


# --------------------------------------------------------------------------
# Clima (Open-Meteo)
# --------------------------------------------------------------------------
def consultar_clima(fecha: str) -> str:
    """Consulta Open-Meteo en la zona de aterrizaje y devuelve las variables del día.

    Devuelve temperatura (temperature_2m), precipitación (precipitation),
    cobertura de nubes (cloud_cover), viento en superficie (wind_speed_10m)
    y ráfagas (wind_gusts_10m). Rechaza fechas a más de 16 días.

    Args:
        fecha: Fecha deseada en formato YYYY-MM-DD (también acepta 'hoy' o 'mañana').
    """
    try:
        clima = weather.obtener_clima(fecha)
    except ErrorDeNegocio as exc:
        return _error(str(exc))
    return _json({"ok": True, "clima": clima.to_dict()})


# --------------------------------------------------------------------------
# Reglas de seguridad
# --------------------------------------------------------------------------
def evaluar_condiciones(
    viento_superficie_10m: float,
    rafagas_10m: float,
    precipitacion: float,
    cobertura_nubes: float,
    fecha: str,
) -> str:
    """Aplica los criterios de seguridad de salto a valores meteorológicos ya obtenidos.

    Args:
        viento_superficie_10m: Viento en superficie en km/h.
        rafagas_10m: Ráfagas máximas en km/h.
        precipitacion: Precipitación en mm.
        cobertura_nubes: Cobertura de nubes en porcentaje.
        fecha: Fecha evaluada en formato YYYY-MM-DD.
    """
    veredicto = safety.evaluar_valores(
        viento_superficie_10m=viento_superficie_10m,
        rafagas_10m=rafagas_10m,
        precipitacion=precipitacion,
        cobertura_nubes=cobertura_nubes,
        fecha=fecha,
    )
    return _json({"ok": True, "veredicto": veredicto.to_dict()})


def evaluar_fecha(fecha: str) -> str:
    """Consulta el clima de una fecha y devuelve en un solo paso el veredicto de seguridad.

    Args:
        fecha: Fecha deseada en formato YYYY-MM-DD (también acepta 'hoy' o 'mañana').
    """
    try:
        clima = weather.obtener_clima(fecha)
    except ErrorDeNegocio as exc:
        return _error(str(exc))
    veredicto = safety.evaluar(clima)
    return _json({"ok": True, "clima": clima.to_dict(), "veredicto": veredicto.to_dict()})


# --------------------------------------------------------------------------
# Agenda
# --------------------------------------------------------------------------
def consultar_horarios(fecha: str) -> str:
    """Devuelve los horarios de salto todavía disponibles para una fecha.

    Args:
        fecha: Fecha en formato YYYY-MM-DD.
    """
    return _json(
        {
            "ok": True,
            "fecha": fecha,
            "horarios_del_dia": HORARIOS_DISPONIBLES,
            "disponibles": scheduling.horarios_libres(fecha),
        }
    )


def agendar_cita(nombre: str, telefono: str, fecha: str, hora: str, nivel_clima: str) -> str:
    """Registra una cita de salto. Falla si el veredicto meteorológico es PROHIBIDO.

    Args:
        nombre: Nombre completo del cliente.
        telefono: Teléfono de contacto.
        fecha: Fecha de la cita en formato YYYY-MM-DD.
        hora: Horario elegido, por ejemplo 08:30.
        nivel_clima: Veredicto obtenido previamente: IDEAL, MARGINAL o PROHIBIDO.
    """
    try:
        cita = scheduling.agendar(nombre, telefono, fecha, hora, nivel_clima)
    except ErrorDeNegocio as exc:
        return _error(str(exc))
    return _json({"ok": True, "cita": cita.to_dict()})


def listar_citas(fecha: str = "") -> str:
    """Lista las citas registradas.

    Args:
        fecha: Fecha YYYY-MM-DD para filtrar, o cadena vacía para listar todas.
    """
    return _json({"ok": True, "citas": scheduling.listar(fecha)})


def cancelar_cita(id_cita: str) -> str:
    """Cancela una cita existente por su identificador.

    Args:
        id_cita: Identificador de 8 caracteres entregado al agendar.
    """
    if scheduling.cancelar(id_cita):
        return _json({"ok": True, "mensaje": f"Cita {id_cita.upper()} cancelada."})
    return _error(f"No existe una cita con id {id_cita}.")


# Catálogo completo (nombre -> función) que publica el servidor MCP.
CATALOGO = [
    consultar_faqs,
    fecha_actual,
    consultar_clima,
    evaluar_condiciones,
    evaluar_fecha,
    consultar_horarios,
    agendar_cita,
    listar_citas,
    cancelar_cita,
]

# Qué herramientas puede ver cada especialista (se usa en ambos modos).
HERRAMIENTAS_POR_ESPECIALISTA: dict[str, list[str]] = {
    "faqs": ["consultar_faqs"],
    "clima": ["fecha_actual", "consultar_clima"],
    "seguridad": ["evaluar_condiciones", "evaluar_fecha"],
    "agenda": ["fecha_actual", "consultar_horarios", "agendar_cita", "listar_citas", "cancelar_cita"],
}
