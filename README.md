# HDT7IA — Protocolos Agénticos (MCP + A2A)

Hoja de trabajo #7 — CC3116. Continúa el agente de **Parachute S.A.** (HT5/HT6):

1. **MCP** — todas las herramientas de los agentes se abstraen en **un solo servidor MCP** con transporte **Streamable HTTP** (`mcp_server/servidor.py`).
2. **A2A** — el Multi Agent System (arquitectura centralizada) se expone como **un solo agente A2A** con 4 skills (`a2a_server/servidor.py`).
3. **Cliente A2A** — la interfaz de terminal ahora es un cliente A2A (`cliente.py`).

## Arquitectura

```
 cliente.py (terminal)
     │  A2A · JSON-RPC + streaming   (Agent Card en /.well-known/agent-card.json)
     ▼
 a2a_server/servidor.py  :9999   ← "Agente Parachute S.A." (skills: faqs, clima, seguridad, agenda)
     │  OpenAI Agents SDK
     ▼
 Orquestador (centralizada.py)
     ├─ Especialista FAQs ───────┐
     ├─ Especialista Clima ──────┤   MCP · Streamable HTTP
     ├─ Oficial de Seguridad ────┼──────────────────────────▶ mcp_server/servidor.py :8000/mcp
     └─ Especialista Agenda ─────┘                              tools · resources · prompts
```

### Servidor MCP (`mcp_server/servidor.py`)

| Tipo | Elementos |
|---|---|
| **Tools** (9) | `consultar_faqs`, `fecha_actual`, `consultar_clima`, `evaluar_condiciones`, `evaluar_fecha`, `consultar_horarios`, `agendar_cita`, `listar_citas`, `cancelar_cita` |
| **Resources** | `parachute://faqs` (base de FAQs), `parachute://operacion` (zona, horarios, ventana de pronóstico) |
| **Prompts** | `agendar_salto`, `dia_apto` |

- Hecho con el SDK oficial de Python (`mcp` v2, clase `MCPServer`), transporte `streamable-http` en `http://127.0.0.1:8000/mcp`.
- Cada ejecución de herramienta se registra (número, argumentos, duración, `ok` y resumen del resultado) en consola y en `logs/mcp_server.log`.
- La lógica está en `core/operaciones.py` (funciones planas). El servidor MCP las publica; ya no hay `function_tool` en los agentes.

**Lado cliente** (`core/mcp_cliente.py`): una sola conexión `MCPServerStreamableHttp` del OpenAI Agents SDK compartida por los 4 especialistas. Un **filtro dinámico por agente** hace que cada especialista vea solo sus herramientas (p. ej. FAQs solo ve `consultar_faqs`), igual que en la HT5.

### Servidor A2A (`a2a_server/servidor.py`)

- SDK oficial `a2a-sdk` (protocolo A2A 1.0), binding JSON-RPC, `streaming=True`.
- `ParachuteAgentExecutor` envuelve al orquestador centralizado:
  - conserva la conversación por `context_id` (multi-turno);
  - publica actualizaciones de estado (`working`) cada vez que el orquestador consulta a un especialista;
  - entrega la respuesta como **artifact** y marca la tarea `completed` (o `failed` si algo falla).
- Logs en consola y `logs/a2a_server.log`: mensaje recibido, especialistas invocados con su latencia y respuesta enviada.

### Cliente A2A (`cliente.py`)

- Descubre al agente con `A2ACardResolver`, muestra su Agent Card y skills, y crea el cliente con `create_client`.
- Envía cada mensaje con `send_message`, mostrando en vivo el progreso (streaming) y la respuesta final.
- Reutiliza el `context_id`, así el agente recuerda la conversación. `salir`, `bye` o Ctrl-C para terminar.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # y coloca tu API_KEY (no se sube: está en .gitignore)
```

## Ejecución (3 terminales, desde la raíz del repo)

```bash
# Terminal 1 — servidor MCP (logs por cada herramienta ejecutada)
python -m mcp_server.servidor

# Terminal 2 — servidor A2A (necesita el MCP arriba)
python -m a2a_server.servidor

# Terminal 3 — interfaz de terminal (cliente A2A)
python cliente.py
#   opcional: python cliente.py --sin-streaming   (usa message/send)
```

Ver el Agent Card: `curl http://127.0.0.1:9999/.well-known/agent-card.json`

### Preguntas sugeridas para el video

Video demostrativo: [Ver en YouTube](https://youtu.be/ZND3n1aYuAA)

1. `¿Cuánto cuesta un salto tándem y qué incluye?` → FAQs (`consultar_faqs` en el log MCP)
2. `¿Es seguro saltar el próximo sábado?` → Clima + Seguridad (`consultar_clima` / `evaluar_fecha`)
3. `Agéndame ese día a las 08:30, soy Ana López, tel 5555-1234` → Agenda (`consultar_horarios`, `agendar_cita`); demuestra que recuerda el contexto
4. `Cancela esa cita` → `cancelar_cita`

## Otros modos

- Las arquitecturas de la HT5 siguen funcionando directo en terminal (sin A2A), ahora con las tools vía MCP: `python centralizada.py`, `python jerarquica.py`, `python descentralizada.py` (requieren el servidor MCP).
- Los evals de promptfoo de la HT6 (`evals/`) usan `TOOLS_BACKEND=local` automáticamente, porque instrumentan las herramientas en proceso.

## Estructura

```
mcp_server/servidor.py   Servidor MCP (Streamable HTTP): tools, resources, prompts + logs
a2a_server/servidor.py   Servidor A2A: Agent Card, skills, AgentExecutor + logs
cliente.py               Interfaz de terminal = cliente A2A
centralizada.py          Orquestador (el MAS que envuelve el servidor A2A)
jerarquica.py / descentralizada.py   Arquitecturas alternativas de la HT5
core/operaciones.py      Lógica de las herramientas (la usa el servidor MCP)
core/mcp_cliente.py      Conexión MCP compartida + filtro de herramientas por especialista
core/agents_base.py      Especialistas (conectados al MCP)
core/tools.py            function_tool en modo local (evals)
core/…                   clima, seguridad, agenda, FAQs, prompts, config
evals/                   Evals de promptfoo (HT6)
```
