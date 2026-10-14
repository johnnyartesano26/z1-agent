ste es el resultado de mezclar lo mejor de los dos. Mantiene la esencia técnica de tu README original, pero con el impacto visual y las métricas que faltaban:

markdown
# Z1 Agent — Automatización de Facturación e Inventario

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-API-green.svg)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Agente de automatización para **facturación e inventario** de una cervecería artesanal (Madre Monte), integrado con **Alegra** (contabilidad Colombia) y **Google Sheets**.

## 🎯 Impacto medible

| Métrica | Antes | Después | Mejora |
|---------|-------|---------|--------|
| Tiempo de facturación (por pedido) | ~15 min | <1 min | **-90%** |
| Procesamiento de 20 facturas | 50 s | 5 s | **-90%** |
| Clientes sincronizados en SQLite | manual | +300 | Automatizado |
| Productos gestionados | manual | +100 | Automatizado |
| Conciliaciones bancarias | manual | OCR + GitHub Actions cada 6h | Automatizado |

**Resultado:** Eliminación de errores de facturación manual y liberación de ~10 horas semanales de trabajo operativo.

## 🏗️ Arquitectura del pipeline

```mermaid
flowchart LR
    A[Google Sheets<br/>Remisiones] --> B[Python<br/>filtra pendientes]
    B --> C[Alegra API<br/>crea factura draft]
    C --> D[SQLite<br/>descuenta inventario]
    D --> E[Google Sheets<br/>marca Facturado]
    E --> F[Ledger local<br/>anti-duplicados]
    F --> G[Telegram Bot<br/>notificación]
🛠️ Stack técnico
Lenguaje: Python 3.9+

Backend: Flask (API REST en tiempo real)

APIs externas: Alegra API, Google Sheets API, Telegram Bot API

Base de datos: SQLite

Asincronía: asyncio, aiohttp

DevOps: GitHub Actions, Docker, cron

Seguridad: cifrado de credenciales con MADREMONTE_KEY, dry-run mode, ledger anti-duplicados

Testing: pytest

🚀 Características principales
✅ Dry-run mode por defecto (previsualiza antes de crear facturas)

✅ Ledger anti-duplicados (nunca factura dos veces la misma remisión)

✅ Reintentos con backoff exponencial para robustez ante fallos de API

✅ Manejo de rate limits con Semaphore

✅ Logging estructurado con profiling incluido

✅ Mapeo de productos con precios COP actualizados

✅ Múltiples tipos de empaque: botella 330ml, litro, domicilio

📋 Proceso oficial: "generar factura"
Script principal: src/facturar_remisiones_auto.py

Factura en Alegra las remisiones pendientes de un Google Sheet y descuenta inventario.

Flujo:

text
Google Sheet (Remisiones) → filas pendientes → crear factura Alegra (draft)
   → descontar inventario → marcar "Facturado" en el Sheet → registrar (anti-duplicados)
Qué factura: Filas con Facturar = Sí (col 22) y Facturado vacío (col 26 / columna AA).

📦 Instalación y uso
bash
# 1. Clonar
git clone https://github.com/johnnyartesano26/z1-agent.git
cd z1-agent

# 2. Instalar dependencias
python -m pip install -r requirements.txt

# 3. Configurar clave maestra (descifra credenciales .env.enc)
export MADREMONTE_KEY='<clave maestra>'

# 4. Dry-run (previsualización segura, NO crea nada)
python src/facturar_remisiones_auto.py

# 5. Crear facturas reales en Alegra + descontar inventario
python src/facturar_remisiones_auto.py --crear

# 6. Autorizar escritura al Sheet (solo la primera vez)
python src/facturar_remisiones_auto.py --auth-sheets
⚠️ Siempre correr el dry-run primero, revisar el preview y confirmar antes de --crear.

Efectos de --crear
Crea la factura en Alegra como draft (cliente resuelto por nombre vía API; respaldo CSV local).

Descuenta inventario (PTL = granel/litros en barril, PTB = botellas 330 ml; DOM01 no afecta stock).

Marca Sí en la columna AA (Facturado) de la fila del Sheet (OAuth, no bloqueante).

Registra la factura en un ledger anti-duplicados local.

Mapeo de productos (código → id Alegra / precio COP)
Código	Producto	id	Precio
PTB01	Golden Ale 330ml	64	8.500
PTB02	Irish Red 330ml	65	8.500
PTB03	APA 330ml	66	8.500
PTB04	IPA 330ml	67	9.500
PTB05	Stout 330ml	68	8.500
PTL01	Golden Ale x litro	69	18.000
PTL02	Irish Red x litro	70	18.000
PTL03	APA x litro	71	18.000
PTL04	IPA x litro	72	19.000
PTL05	Stout x litro	73	18.000
DOM01	Domicilio	58	variable
📸 Capturas
<!-- AÑADE AQUÍ: screenshot del output del dry-run y del dashboard -->
Pendiente: añadir capturas del dashboard y del output del script.

🔒 Seguridad y privacidad
Datos sensibles (clientes, credenciales) NO versionados en el repo.

Clave maestra MADREMONTE_KEY nunca guardada en archivos. Si se expone, rotarla y volver a cifrar.

Plantillas vacías disponibles en data/clientes.ejemplo.csv y .env.example.

Tokens OAuth (*.pickle, credentials*.json) excluidos vía .gitignore.

📬 Contacto
Johnny Guerrero — Data & Automation Engineer

LinkedIn: linkedin.com/in/johnny-guerrero-tech

GitHub: @johnnyartesano26

Email: johnnyartesano26@gmail.com

📄 Licencia
MIT — ver LICENSE
