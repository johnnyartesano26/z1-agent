import asyncio
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
import logging

from dotenv import load_dotenv

from .alegra_client_async import AlegraClientAsync, AlegraClientAsyncError
from .config import (
    ALEGRA_API_URL, ALEGRA_EMAIL, ALEGRA_TOKEN,
    PRODUCT_MAP, ESTILOS, ESTILO_DE_PTB,
    MAX_CONCURRENT_ALEGRA, GOOGLE_CREDS_PATH, GOOGLE_CREDS_JSON,
    SHEET_REMISIONES, SHEET_INVENTARIO,
    debug_on_error, profile_if_enabled, timeit,
)

load_dotenv()

logger = logging.getLogger(__name__)

DRY = "--dry-run" in sys.argv

C_CLIENTE, C_DOM, C_FACTURAR, C_VALOR_DOM, C_DOM_ALT, C_FACTURADO = 2, 19, 22, 24, 23, 26
PARES_PROD = [(5, 6), (8, 9), (11, 12), (14, 15), (17, 18)]


def col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx > 0:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _f(v, d=0) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return d


async def leer_hojas(svc):
    loop = asyncio.get_running_loop()
    logger.info("Leyendo hojas de cálculo...")

    def _leer_remisiones():
        from googleapiclient.discovery import build
        result = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_REMISIONES, range="A:ZZ"
        ).execute()
        rows = result.get("values", [])
        return rows[1:] if len(rows) > 1 else [], rows[0] if rows else []

    def _leer_inventario():
        rows = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_INVENTARIO, range="A:ZZ"
        ).execute().get("values", [])
        stock = {e: {"bot": 0.0, "litros": 0.0} for e in ESTILOS}
        cols = {e: {"bot": None, "litros": None} for e in ESTILOS}
        row_num = None
        last = None

        for i in range(len(rows) - 1, -1, -1):
            if any(c.strip() for c in rows[i]):
                last = rows[i]
                row_num = i + 1
                break

        if last:
            for j, cell in enumerate(last):
                up = cell.strip().upper()
                if not up or j == 0:
                    continue
                est = next((e for e in ESTILOS if e in up), None)
                if not est:
                    continue
                vcol = j - 1
                if "BOTELLA" in up:
                    stock[est]["bot"] = _f(last[vcol]) if vcol < len(last) else 0
                    cols[est]["bot"] = vcol
                elif "LITRO" in up:
                    stock[est]["litros"] = _f(last[vcol]) if vcol < len(last) else 0
                    cols[est]["litros"] = vcol

        def _tab_name():
            m = svc.spreadsheets().get(
                spreadsheetId=SHEET_INVENTARIO, fields="sheets.properties.title"
            ).execute()
            return m["sheets"][0]["properties"]["title"]

        return stock, cols, row_num, _tab_name()

    data, headers = await loop.run_in_executor(None, _leer_remisiones)
    stock, inv_cols, inv_row, inv_tab = await loop.run_in_executor(None, _leer_inventario)

    assert headers, "No se encontraron encabezados en la hoja de remisiones"
    logger.info("Remisiones: %d filas de datos", len(data))
    logger.info("Inventario leído | pestaña=%s fila=%d", inv_tab, inv_row)

    return data, headers, stock, inv_cols, inv_row, inv_tab


def extraer_items(row: list) -> List[Dict]:
    items = []
    for pc, cc in PARES_PROD:
        if pc >= len(row) or cc >= len(row):
            continue
        cel = row[pc].strip() if row[pc] else ""
        ref = next((r for r in PRODUCT_MAP if r in cel), None)
        if not ref:
            continue
        cant = _f(row[cc].strip(), 0) if row[cc] else 0
        if cant > 0:
            pid = PRODUCT_MAP[ref]["id"]
            precio = PRODUCT_MAP[ref]["price"]
            items.append({
                "ref": ref,
                "id": pid,
                "quantity": cant,
                "price": precio,
                "tax": [{"id": 4}] if pid != "58" else [],
            })
    return items


def obtener_filas_procesables(data: list) -> List[Dict]:
    filas = []
    for i, row in enumerate(data, start=2):
        if C_FACTURAR >= len(row) or row[C_FACTURAR].strip().lower() not in ("si", "sí", "true", "1"):
            continue
        if C_FACTURADO < len(row) and row[C_FACTURADO].strip():
            continue
        cliente = row[C_CLIENTE].strip() if C_CLIENTE < len(row) else ""
        if not cliente:
            continue

        items = extraer_items(row)

        if C_DOM < len(row) and row[C_DOM].strip().lower() in ("se incluye", "si", "sí", "true", "1"):
            pdom = 12000
            for cidx in (C_VALOR_DOM, C_DOM_ALT):
                if cidx < len(row) and row[cidx].strip():
                    v = _f(row[cidx].strip(), 0)
                    if v:
                        pdom = v * 1000 if v < 100 else v
                        break
            items.append({"ref": "DOM01", "id": "58", "quantity": 1, "price": pdom, "tax": []})

        if not items:
            continue
        filas.append({"row_num": i, "cliente": cliente, "items": items, "row_data": row})

    logger.info("Filas procesables: %d (de %d totales)", len(filas), len(data))
    return filas


def verificar_stock_batch(filas: list, stock: dict) -> Tuple[list, list, dict]:
    assert isinstance(stock, dict), "stock debe ser un diccionario"
    total_necesario = {e: {"bot": 0.0, "litros": 0.0} for e in ESTILOS}
    sin_stock = []
    validas = []

    for f in filas:
        needed = {e: {"bot": 0.0, "litros": 0.0} for e in ESTILOS}
        ok = True

        for it in f["items"]:
            if it["ref"] == "DOM01":
                continue
            est = ESTILO_DE_PTB.get(it["ref"])
            if not est:
                continue
            tipo = "bot" if it["ref"].startswith("PTB") else "litros"
            needed[est][tipo] += it["quantity"]

        for est in ESTILOS:
            new_bot = total_necesario[est]["bot"] + needed[est]["bot"]
            new_lit = total_necesario[est]["litros"] + needed[est]["litros"]
            if new_bot > stock[est]["bot"] or new_lit > stock[est]["litros"]:
                disp_bot = max(0, stock[est]["bot"] - total_necesario[est]["bot"])
                disp_lit = max(0, stock[est]["litros"] - total_necesario[est]["litros"])
                logger.warning(
                    "Stock insuficiente: Fila %d '%s' → %s necesita %.0f bot/%.1fL (disp: %.0f/%.1f)",
                    f["row_num"], f["cliente"], est, needed[est]["bot"], needed[est]["litros"],
                    disp_bot, disp_lit
                )
                ok = False

        if ok:
            validas.append(f)
            for est in ESTILOS:
                for tipo in ("bot", "litros"):
                    total_necesario[est][tipo] += needed[est][tipo]
        else:
            sin_stock.append(f)

    logger.info("Stock verificado: %d OK | %d sin stock", len(validas), len(sin_stock))
    return validas, sin_stock, total_necesario


@timeit
async def buscar_clientes_paralelo(alegra: AlegraClientAsync, filas: list) -> Dict[str, Optional[str]]:
    clientes_unicos = list(set(f["cliente"] for f in filas))
    assert clientes_unicos, "No hay clientes para buscar"
    cache = {}

    async def _buscar(nombre):
        result = await alegra.search_client_by_name(nombre)
        return nombre, result.get("id") if result else None

    logger.info("Buscando %d clientes en paralelo...", len(clientes_unicos))
    tasks = [_buscar(n) for n in clientes_unicos]
    resultados = await asyncio.gather(*tasks)

    for nombre, cid in resultados:
        cache[nombre] = cid
        if not cid:
            logger.warning("Cliente no encontrado en Alegra: %s", nombre)

    return cache


@timeit
async def crear_facturas_paralelo(alegra: AlegraClientAsync, filas: list,
                                   client_cache: dict) -> Tuple[list, list, list]:
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vence = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    result_map = {}

    async def _crear(fila):
        cid = client_cache.get(fila["cliente"])
        if not cid:
            return fila["row_num"], None, "sin_cliente"
        try:
            items_alegra = [
                {"id": it["id"], "quantity": it["quantity"], "price": it["price"], "tax": it["tax"]}
                for it in fila["items"]
            ]
            resp = await alegra.create_invoice(cid, items_alegra, due_date=vence, date=hoy)
            if resp and resp.get("id"):
                return fila["row_num"], resp, None
            return fila["row_num"], None, f"error_api: {resp}"
        except AlegraClientAsyncError as e:
            return fila["row_num"], None, str(e)
        except Exception as e:
            logger.error("Error inesperado en factura para %s: %s", fila["cliente"], e, exc_info=True)
            return fila["row_num"], None, str(e)

    logger.info("Creando %d facturas en paralelo (máx %d concurrentes)...",
                len(filas), MAX_CONCURRENT_ALEGRA)
    tasks = [_crear(f) for f in filas]
    resultados = await asyncio.gather(*tasks)

    creadas = []
    sin_cliente = []
    errores = []

    for row_num, fac, error in resultados:
        fila = next(f for f in filas if f["row_num"] == row_num)
        if fac:
            resumen = ", ".join(f"{it['ref']}x{int(it['quantity'])}" for it in fila["items"])
            logger.info("Factura %s | %s (%s)", fac["id"], fila["cliente"], resumen)
            creadas.append({
                "row_num": row_num, "factura": fac,
                "cliente": fila["cliente"], "items": fila["items"],
            })
        elif error == "sin_cliente":
            sin_cliente.append(fila)
        else:
            logger.error("Error factura %s: %s", fila["cliente"], error)
            errores.append(fila)

    return creadas, sin_cliente, errores


async def actualizar_sheets(svc, creadas, total_descontado, stock, inv_cols, inv_row, inv_tab):
    loop = asyncio.get_running_loop()
    updates = []

    for c in creadas:
        updates.append({
            "range": f"AA{c['row_num']}",
            "values": [[f"Facturado {c['factura']['id']}"]],
        })

    for est in ESTILOS:
        for tipo in ("bot", "litros"):
            d = total_descontado[est][tipo]
            if d and inv_cols[est][tipo] is not None:
                rng = f"'{inv_tab}'!{col_letter(inv_cols[est][tipo])}{inv_row}"
                updates.append({"range": rng, "values": [[stock[est][tipo]]]})

    if not updates:
        logger.info("Sin actualizaciones pendientes en Sheets")
        return

    if DRY:
        logger.info("[DRY] Actualizaciones que se aplicarían (%d):", len(updates))
        for u in updates:
            logger.info("  %s → %s", u["range"], u["values"])
        return

    def _batch_remisiones():
        rem_updates = [u for u in updates if "AA" in u["range"]]
        if rem_updates:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=SHEET_REMISIONES,
                body={"valueInputOption": "USER_ENTERED", "data": rem_updates},
            ).execute()
            logger.info("Remisiones actualizadas: %d celdas", len(rem_updates))

    def _batch_inventario():
        inv_updates = [u for u in updates if "AA" not in u["range"]]
        if inv_updates:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=SHEET_INVENTARIO,
                body={"valueInputOption": "USER_ENTERED", "data": inv_updates},
            ).execute()
            logger.info("Inventario actualizado: %d celdas", len(inv_updates))

    await loop.run_in_executor(None, _batch_remisiones)
    await loop.run_in_executor(None, _batch_inventario)


def _get_google_service():
    import googleapiclient.discovery
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if GOOGLE_CREDS_JSON:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON), scopes=scopes
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDS_PATH, scopes=scopes
        )
    creds.refresh(Request())
    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


async def main():
    if not ALEGRA_EMAIL or not ALEGRA_TOKEN:
        logger.error("Faltan ALEGRA_EMAIL / ALEGRA_TOKEN")
        sys.exit(1)

    assert SHEET_REMISIONES, "SHEET_REMISIONES no configurado"
    assert SHEET_INVENTARIO, "SHEET_INVENTARIO no configurado"

    logger.info("=" * 56)
    logger.info("FACTURACIÓN ASÍNCRONA — Madre Monte %s", "(DRY-RUN)" if DRY else "")
    logger.info("Máx concurrentes Alegra: %d | Profile: %s | Debug: %s",
                MAX_CONCURRENT_ALEGRA, os.getenv("Z1_PROFILE", "no"), os.getenv("Z1_DEBUG", "no"))
    logger.info("=" * 56)

    svc = _get_google_service()

    data, headers, stock, inv_cols, inv_row, inv_tab = await leer_hojas(svc)

    logger.info("Inventario actual:")
    for e in ESTILOS:
        logger.info("  %s: %.0f bot, %.1f L", e, stock[e]["bot"], stock[e]["litros"])

    filas = obtener_filas_procesables(data)
    if not filas:
        logger.info("Sin filas pendientes para facturar.")
        return

    validas, sin_stock, total_descontado = verificar_stock_batch(filas, stock)

    if not validas:
        logger.info("Nada que facturar tras verificar stock.")
        return

    alegra = AlegraClientAsync(max_concurrent=MAX_CONCURRENT_ALEGRA)

    try:
        client_cache = await buscar_clientes_paralelo(alegra, validas)
        con_cliente = [f for f in validas if client_cache.get(f["cliente"])]
        sin_cliente_count = len(validas) - len(con_cliente)

        if sin_cliente_count:
            logger.warning("%d clientes no encontrados, se omitirán", sin_cliente_count)

        if not con_cliente:
            logger.info("Sin clientes válidos para facturar.")
            return

        creadas, _, errores = await crear_facturas_paralelo(alegra, con_cliente, client_cache)

        for est in ESTILOS:
            for tipo in ("bot", "litros"):
                stock[est][tipo] -= total_descontado[est][tipo]

        if not DRY and creadas:
            logger.info("Actualizando Google Sheets...")
            await actualizar_sheets(svc, creadas, total_descontado, stock, inv_cols, inv_row, inv_tab)

    finally:
        await alegra.close()

    resumen = f"{len(creadas)} emitidas | {len(sin_stock)} sin stock | {sin_cliente_count} sin cliente"
    logger.info("=" * 56)
    logger.info("RESUMEN: %s", resumen)
    logger.info("=" * 56)

    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        try:
            with open(gh, "a") as f:
                f.write(f"nuevas={len(creadas)}\n")
                f.write(f"resumen={resumen}\n")
        except IOError as e:
            logger.warning("No se pudo escribir GITHUB_OUTPUT: %s", e)


if __name__ == "__main__":
    with profile_if_enabled():
        asyncio.run(main())
