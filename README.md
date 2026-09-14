# M&M AI Commerce (MVP)

Pipeline B2B para **M&M Insumos (Neuquén)** operado por agentes.
El humano **solo APRUEBA** dinero/legal. **ZERO SPEND**: SQLite, open source, sin APIs de pago.

## Pipeline

```
RADAR → PLIEGO → SOURCING → PRICING → RISK → VERIFIER → TELEGRAM
```

`Commander` coordina la máquina de estados por oportunidad.

## Agentes

| Agente | Rol |
|--------|-----|
| commander | Orquesta estados |
| radar | CODINEU público/fixtures, dedupe, FIT SCORE 0–100, hard-skip `16514` |
| pliego | Ítems SAFIPRO/PDF/texto; **NO inventa** (sin proxy título); `NO VERIFICADO`/`SIN_LINEAS` |
| sourcing | Proveedores (MercadoLibre público + allowlist stub); MATCH SCORE |
| pricing | `COST_TOTAL`; `PRECIO_OBJETIVO = COST × 1.90`; tax/flete `PENDING` |
| risk | `BAJO\|MEDIO\|ALTO\|CRITICO` |
| verifier | Bloqueos independientes; `BLOQUEAR` si inconsistencia |
| bid | Prepara paquete de oferta; **NUNCA auto-submit** |

Aprobaciones: `PENDIENTE|APROBADO|RECHAZADO|BLOQUEADO`.

## Categorías

- **Evitar al inicio:** salud, medicamentos, policía, construcción pesada, maquinaria, obras.
- **Priorizar:** IT, notebooks, PCs, impresoras, redes, electrónica, oficina, librería, herramientas livianas, mobiliario.

## Requisitos

- Python 3.12+
- Dependencias en `requirements.txt` (todas open source / gratis)

## Instalación

```bash
cd mm-ai-commerce
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

## Uso

```bash
# Crear tablas SQLite
python -m mm_commerce init-db

# Solo radar (fixtures / snapshot público)
python -m mm_commerce radar --once

# Pipeline completo una vez
python -m mm_commerce run --once

# Digest CLI — tarjetas estilo Telegram (sin token)
python -m mm_commerce digest --limit 10
python -m mm_commerce digest --json-out
```

### Telegram

Sin `TELEGRAM_BOT_TOKEN` / `TELEGRAM_USER_ID` el digest se imprime por CLI.
Con token: alerta a usuario allowlist con botones stub `APROBAR|RECHAZAR|VER…`.

## Datos (honestidad)

- **CODINEU:** parseo live GeneXus `GridContainerDataV` → fallback fixtures/`codineu-list.html`/`codineu_sample.json`.
- Login privado: `/home/box/.config/codineu/login.env` si existe; si no → `REQUIERE CREDENCIALES`.
- Portales energía (YPF, etc.): conectores **stub** `REQUIERE CREDENCIALES`.
- **Hard skip forever:** proceso CODINEU `16514`.

## Tests

```bash
pytest -q
```

## Docker (opcional)

```bash
docker compose run --rm mm-commerce
```

## Licencia / costo

Open source stack. Sin suscripciones. LLM local opcional (solo extracción de texto; **nunca inventa precios**).
