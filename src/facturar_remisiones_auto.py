#!/usr/bin/env python3
"""
facturar_remisiones_auto.py
Factura en Alegra las filas pendientes de la hoja "Remisiones Automatización 2026"
(Google Sheet público). Formato nuevo del formulario (nombres poéticos + PTB/PTL).

Uso:
    python facturar_remisiones_auto.py            # DRY-RUN (solo previsualiza)
    python facturar_remisiones_auto.py --crear    # Crea las facturas en Alegra (draft)

Requiere credenciales: exporta MADREMONTE_KEY con la clave maestra
(o ten el .env plano). Usa env_loader del proyecto.
"""

import os
import sys
import re
import argparse
import tempfile
from datetime import date, timedelta

import csv
import requests
import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from env_loader import load_credentials

SHEET_ID = "1hBicxCSwnZpreEPmru_ZScZQjuHPXcRQBiWatC8AC1Q"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
HOJA = "Respuestas de formulario 1"

# Escritura en Google Sheets (marcar "Facturado" en col AA)
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOGLE_OAUTH_CREDS = os.path.join(_REPO, "credentials_calendar.json")
TOKEN_SHEETS = os.path.join(_REPO, "token_sheets.pickle")
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
COL_FACTURADO = "AA"  # columna 27 = índice 26 (IDX_FACTURADO)

ALEGRA_BASE = "https://api.alegra.com/api/v1/"

# Columnas (0-based) del formato "Remisiones Automatización 2026"
IDX_CLIENTE = 2
IDX_FACTURAR = 22
IDX_FACTURADO = 26
IDX_DOMICILIO = 19
IDX_VALOR_DOM = 24        # "Si marco Otro en domicilio marque el Valor"
IDX_VALOR_DOM_ALT = 23    # "Valor del domicilio"
PARES_PRODUCTO = [(5, 6), (8, 9), (11, 12), (14, 15), (17, 18)]

# Mapeo producto → (id Alegra, precio unitario)
MAPEO = {
    "PTB01": ("64", 8500), "PTB02": ("65", 8500), "PTB03": ("66", 8500),
    "PTB04": ("67", 9500), "PTB05": ("68", 8500),
    "PTL01": ("69", 18000), "PTL02": ("70", 18000), "PTL03": ("71", 18000),
    "PTL04": ("72", 19000), "PTL05": ("73", 18000),
    "DOM01": ("58", 12000),
}
NOMBRE = {
    "PTB01": "Golden Ale 330ml", "PTB02": "Irish Red 330ml", "PTB03": "APA 330ml",
    "PTB04": "IPA 330ml", "PTB05": "Stout 330ml",
    "PTL01": "Golden Ale x litro", "PTL02": "Irish Red x litro", "PTL03": "APA x litro",
    "PTL04": "IPA x litro", "PTL05": "Stout x litro", "DOM01": "Domicilio",
}

CLIENTES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clientes.csv")
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facturas_creadas.json")
INVENTARIO_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "inventario_csv", "inventario_MM.csv")

# Estilo por código (para el nombre en el inventario)
ESTILO = {
    "PTB01": "BLONDE ALE", "PTB02": "IRISH RED ALE", "PTB03": "APA",
    "PTB04": "IPA", "PTB05": "STOUT",
    "PTL01": "BLONDE ALE", "PTL02": "IRISH RED ALE", "PTL03": "APA",
    "PTL04": "IPA", "PTL05": "STOUT",
}


def descargar_hoja():
    print("📥 Descargando hoja pública...")
    r = requests.get(EXPORT_URL, timeout=60)
    r.raise_for_status()
    tmp = os.path.join(tempfile.gettempdir(), "remisiones_auto_2026.xlsx")
    with open(tmp, "wb") as f:
        f.write(r.content)
    return tmp


def cargar_ledger():
    """Registro local de filas ya facturadas (anti-duplicados)."""
    import json
    if os.path.exists(LEDGER):
        try:
            with open(LEDGER, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def guardar_ledger(led):
    import json
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=2)


def firma_fila(row):
    """Firma única de la fila = marca temporal (col 0) + cliente."""
    ts = str(row[0]) if row and row[0] is not None else ""
    cli = str(row[IDX_CLIENTE]).strip() if len(row) > IDX_CLIENTE and row[IDX_CLIENTE] else ""
    return f"{ts}|{cli}"


def cargar_clientes_csv():
    import csv
    d = {}
    if os.path.exists(CLIENTES_CSV):
        with open(CLIENTES_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("nombre"):
                    d[row["nombre"].strip().lower()] = row["id"]
    return d


_sheets_service = None


def get_sheets_service(interactive=False):
    """
    Cliente autenticado de Google Sheets.
    Por defecto NO abre navegador: usa token existente (o lo refresca).
    Si no hay token válido y interactive=False → devuelve None (no bloquea).
    Con interactive=True (flag --auth-sheets) corre el flujo OAuth una vez.
    """
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service
    import pickle
    from google.auth.transport.requests import Request
    import googleapiclient.discovery

    creds = None
    if os.path.exists(TOKEN_SHEETS):
        with open(TOKEN_SHEETS, "rb") as f:
            creds = pickle.load(f)
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            return None  # sin navegador: no bloquear
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_OAUTH_CREDS, SHEETS_SCOPES)
        creds = flow.run_local_server(port=0)
    with open(TOKEN_SHEETS, "wb") as f:
        pickle.dump(creds, f)
    _sheets_service = googleapiclient.discovery.build("sheets", "v4", credentials=creds)
    return _sheets_service


def marcar_facturado_sheet(fila, interactive=False):
    """Escribe 'Sí' en la columna AA (Facturado) de la fila. No bloquea sin token."""
    try:
        servicio = get_sheets_service(interactive=interactive)
        if servicio is None:
            print(f"   ⚠️  Sheet sin autorizar (no hay token). Marca manualmente "
                  f"la fila {fila}, columna {COL_FACTURADO}. "
                  f"(Autoriza una vez con: --auth-sheets)")
            return False
        rango = f"'{HOJA}'!{COL_FACTURADO}{fila}"
        servicio.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=rango,
            valueInputOption="USER_ENTERED",
            body={"values": [["Sí"]]},
        ).execute()
        print(f"   📝 Sheet: 'Facturado' = Sí escrito en {COL_FACTURADO}{fila}.")
        return True
    except Exception as e:
        print(f"   ⚠️  No se pudo marcar 'Facturado' en el Sheet ({fila}): {e}")
        print(f"      → Márcalo manualmente en la fila {fila}, columna {COL_FACTURADO}.")
        return False


def sesion_alegra():
    s = requests.Session()
    s.auth = (os.getenv("ALEGRA_EMAIL"), os.getenv("ALEGRA_TOKEN"))
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json",
                      "User-Agent": "MadreMonte-Facturador/1.0"})
    return s


def resolver_cliente(sess, nombre, csv_cache):
    """Devuelve id de Alegra: primero API por nombre, si no CSV local."""
    try:
        resp = sess.get(ALEGRA_BASE + "contacts", params={"name": nombre, "limit": 1}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return str(data[0]["id"]), "API Alegra"
    except Exception as e:
        print(f"   ⚠️  Error API contacts: {e}")
    cid = csv_cache.get(nombre.lower())
    if cid:
        return str(cid), "CSV local"
    return None, None


def extraer_items(row):
    items = []
    for pc, cc in PARES_PRODUCTO:
        if pc >= len(row) or cc >= len(row):
            continue
        celda = str(row[pc]).strip() if row[pc] else ""
        m = re.search(r"(PTB\d{2}|PTL\d{2}|DOM01)", celda)
        if not m:
            continue
        ref = m.group(1)
        if ref not in MAPEO:
            continue
        try:
            cant = float(str(row[cc]).replace(",", "."))
        except (ValueError, TypeError):
            cant = 0
        if cant <= 0:
            continue
        pid, precio = MAPEO[ref]
        items.append({"ref": ref, "id": pid, "quantity": cant, "price": precio,
                      "tax": [{"id": 4}]})
    return items


def item_domicilio(row):
    if IDX_DOMICILIO >= len(row):
        return None
    dom = str(row[IDX_DOMICILIO]).strip().lower() if row[IDX_DOMICILIO] else ""
    if dom not in ("se incluye", "si", "sí", "true", "1"):
        return None
    precio = 12000
    for idx in (IDX_VALOR_DOM, IDX_VALOR_DOM_ALT):
        if idx < len(row) and row[idx] not in (None, "", "Otro"):
            try:
                val = float(str(row[idx]).replace(",", "."))
                if val < 100:
                    val *= 1000
                precio = val
                break
            except (ValueError, TypeError):
                pass
    return {"ref": "DOM01", "id": "58", "quantity": 1, "price": precio, "tax": []}


def previsualizar(cliente, origen_id, cliente_id, items):
    print("\n" + "=" * 55)
    print(f"  FACTURA (borrador) — {cliente}")
    print(f"  Cliente ID Alegra: {cliente_id} ({origen_id})")
    print("=" * 55)
    subtotal = 0
    for it in items:
        linea = it["quantity"] * it["price"]
        subtotal += linea
        print(f"  {NOMBRE.get(it['ref'], it['ref']):<22} "
              f"{it['quantity']:>7.2f} x ${it['price']:>8,.0f} = ${linea:>12,.0f}"
              f"{'  +IVA' if it['tax'] else ''}")
    print("-" * 55)
    print(f"  Subtotal (sin IVA): ${subtotal:,.0f}")
    print("  (IVA lo calcula Alegra según el impuesto id 4)")


def _ultimo_stock():
    """Lee el último stock_resultante por codigo_producto."""
    stock = {}
    if os.path.exists(INVENTARIO_CSV):
        with open(INVENTARIO_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    stock[row["codigo_producto"]] = float(row["stock_resultante"])
                except (ValueError, TypeError, KeyError):
                    pass
    return stock


def actualizar_inventario(items, factura_id=""):
    """Registra una SALIDA en inventario_MM.csv por cada item facturable (no domicilio)."""
    if not os.path.exists(INVENTARIO_CSV):
        print("   ⚠️  Inventario no encontrado; se omite descuento.")
        return
    hoy = date.today().isoformat()
    stock = _ultimo_stock()
    cols = ["id_transaccion", "tipo", "fecha", "codigo_producto", "nombre_producto",
            "categoria", "marca", "unidad_medida", "cantidad", "stock_resultante",
            "envasado", "destino_cliente", "id_factura_alegra", "estado_factura",
            "notas", "registrado_por", "fecha_registro"]
    nuevas = []
    for it in items:
        ref = it["ref"]
        if ref == "DOM01":
            continue
        es_litro = ref.startswith("PTL")
        estilo = ESTILO.get(ref, ref)
        anterior = stock.get(ref, 0)
        nuevo = anterior - it["quantity"]
        if nuevo < 0:
            print(f"   ⚠️  Stock {ref} quedaría negativo ({nuevo:.2f}); "
                  f"inventario local posiblemente desactualizado.")
        stock[ref] = nuevo
        nuevas.append({
            "id_transaccion": f"FACT_{hoy}_{ref}_{factura_id or 'DRAFT'}",
            "tipo": "SALIDA",
            "fecha": hoy,
            "codigo_producto": ref,
            "nombre_producto": (f"Litros {estilo} en barril" if es_litro
                                else f"BOTELLAS X 330 ML {estilo}"),
            "categoria": "CERVEZA_GRANEL" if es_litro else "BOTELLA_LLENA",
            "marca": "MADREMONTE",
            "unidad_medida": "Litros" if es_litro else "Unidad",
            "cantidad": it["quantity"],
            "stock_resultante": round(nuevo, 2),
            "envasado": "BARRIL" if es_litro else "BOTELLA_330ML",
            "destino_cliente": "FACTURADO",
            "id_factura_alegra": str(factura_id),
            "estado_factura": "FACTURADO",
            "notas": f"Descuento automático facturación - {hoy}",
            "registrado_por": "facturar_remisiones_auto",
            "fecha_registro": hoy,
        })
    if nuevas:
        with open(INVENTARIO_CSV, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writerows(nuevas)
        for n in nuevas:
            print(f"   📦 Inventario: -{n['cantidad']} "
                  f"{'L' if n['unidad_medida']=='Litros' else 'und'} {n['codigo_producto']} "
                  f"→ stock {n['stock_resultante']}")


def crear_factura(sess, cliente_id, items):
    payload = {
        "client": int(cliente_id),
        "date": date.today().isoformat(),
        "dueDate": (date.today() + timedelta(days=30)).isoformat(),
        "items": [{"id": it["id"], "quantity": it["quantity"],
                   "price": it["price"], "tax": it["tax"]} for it in items],
        "paymentForm": "CASH",
        "paymentMethod": "CASH",
        "status": "draft",
    }
    resp = sess.post(ALEGRA_BASE + "invoices", json=payload, timeout=30)
    if resp.status_code in (200, 201):
        return resp.json(), None
    return None, f"HTTP {resp.status_code}: {resp.text[:300]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crear", action="store_true", help="Crea las facturas en Alegra")
    ap.add_argument("--auth-sheets", action="store_true",
                    help="Autoriza (una vez, con navegador) la escritura en Google Sheets")
    args = ap.parse_args()

    if args.auth_sheets:
        print("🔐 Autorizando acceso de escritura a Google Sheets...")
        if get_sheets_service(interactive=True):
            print("✅ Token de Sheets guardado. Ya puedes marcar 'Facturado' automáticamente.")
        else:
            print("❌ No se pudo autorizar.")
        return

    load_credentials()
    if not os.getenv("ALEGRA_EMAIL") or not os.getenv("ALEGRA_TOKEN"):
        print("❌ Sin credenciales de Alegra. Exporta MADREMONTE_KEY y reintenta.")
        sys.exit(1)

    ruta = descargar_hoja()
    wb = openpyxl.load_workbook(ruta, data_only=True)
    ws = wb[HOJA]
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    pendientes = []
    for i, r in enumerate(rows, start=2):
        if len(r) <= IDX_FACTURAR:
            continue
        fac = str(r[IDX_FACTURAR]).strip().lower() if r[IDX_FACTURAR] else ""
        fado = str(r[IDX_FACTURADO]).strip() if len(r) > IDX_FACTURADO and r[IDX_FACTURADO] else ""
        if fac in ("si", "sí", "true", "1") and not fado:
            pendientes.append((i, r))

    print(f"\n🧾 Filas pendientes de facturar: {len(pendientes)}")
    if not pendientes:
        print("Nada por hacer.")
        return

    sess = sesion_alegra()
    csv_cache = cargar_clientes_csv()
    ledger = cargar_ledger()

    for fila, r in pendientes:
        cliente = str(r[IDX_CLIENTE]).strip() if r[IDX_CLIENTE] else ""

        firma = firma_fila(r)
        if firma in ledger:
            print(f"⏭️  Fila {fila} ({cliente}): ya facturada localmente "
                  f"(factura {ledger[firma]}). Se omite para evitar duplicado.")
            continue

        items = extraer_items(r)
        dom = item_domicilio(r)
        if dom:
            items.append(dom)
        if not items:
            print(f"⚠️  Fila {fila} ({cliente}): sin productos válidos. Se omite.")
            continue

        cliente_id, origen = resolver_cliente(sess, cliente, csv_cache)
        if not cliente_id:
            print(f"❌ Fila {fila}: cliente '{cliente}' no encontrado en Alegra/CSV. Se omite.")
            continue

        previsualizar(cliente, origen, cliente_id, items)

        if args.crear:
            factura, err = crear_factura(sess, cliente_id, items)
            if err:
                print(f"   ❌ Error creando factura: {err}")
            else:
                fid = factura.get("id")
                print(f"   ✅ Factura creada: ID {fid} | "
                      f"Total ${factura.get('total', 0):,.0f}")
                ledger[firma] = fid
                guardar_ledger(ledger)
                actualizar_inventario(items, fid)
                marcar_facturado_sheet(fila)
        else:
            print("   (dry-run: no se creó nada ni se tocó inventario.)")
            print("   Inventario que se descontaría:")
            for it in items:
                if it["ref"] != "DOM01":
                    u = "L" if it["ref"].startswith("PTL") else "und"
                    print(f"     -{it['quantity']} {u} {it['ref']} ({ESTILO.get(it['ref'], '')})")

    print("\nListo.")


if __name__ == "__main__":
    main()
