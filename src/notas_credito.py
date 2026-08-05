import os
import sys
import json
import pickle
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from .alegra_client import AlegraClient, AlegraClientError, AlegraConnectionError
from .config import (
    ID_PRODUCTO_A_ESTILO, ESTILOS,
    GOOGLE_CREDS_PATH, GOOGLE_CREDS_JSON,
    SHEET_INVENTARIO, DRIVE_NC_FOLDER,
    debug_on_error,
)

load_dotenv()

logger = logging.getLogger(__name__)

GDRIVE_TOKEN = os.path.expanduser("~/.opencode/gdrive_token.pickle")
DRIVE_CREDENTIALS = os.getenv(
    "GOOGLE_CREDS_PATH",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..",
        "Documents", "Automatizacion", "AUTOMATIZACIONES CODIGO",
        "client_secret_opencode.json",
    ),
)

LEDGER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "Documents", "Madre Monte", "MadreMonte_Contexto",
    "inventario", "registro_de_inventario", "ledger_inventario.json",
)
LEDGER_PATH = os.path.abspath(LEDGER_PATH)


class NotaCreditoError(Exception):
    pass


def _get_drive_service():
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(GDRIVE_TOKEN):
        try:
            with open(GDRIVE_TOKEN, "rb") as f:
                creds = pickle.load(f)
        except (pickle.UnpicklingError, EOFError) as e:
            logger.warning("Token de Drive corrupto, se regenerará: %s", e)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refrescando token de Drive...")
            creds.refresh(Request())
        else:
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(
                    DRIVE_CREDENTIALS,
                    ["https://www.googleapis.com/auth/drive.file"],
                )
                creds = flow.run_local_server(port=8088)
            except Exception as e:
                logger.error("Error en autenticación de Drive: %s", e, exc_info=True)
                raise NotaCreditoError(f"Error de autenticación Google Drive: {e}") from e

        os.makedirs(os.path.dirname(GDRIVE_TOKEN), exist_ok=True)
        with open(GDRIVE_TOKEN, "wb") as f:
            pickle.dump(creds, f)
        logger.info("Token de Drive guardado en %s", GDRIVE_TOKEN)

    return build("drive", "v3", credentials=creds)


def _get_google_sheets_svc():
    import googleapiclient.discovery
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GRequest

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        if GOOGLE_CREDS_JSON:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(GOOGLE_CREDS_JSON), scopes=scopes
            )
        else:
            creds = service_account.Credentials.from_service_account_file(
                GOOGLE_CREDS_PATH, scopes=scopes
            )
        creds.refresh(GRequest())
    except Exception as e:
        logger.error("Error creando credenciales de Google Sheets: %s", e, exc_info=True)
        raise NotaCreditoError(f"Error de credenciales Google Sheets: {e}") from e

    return googleapiclient.discovery.build("sheets", "v4", credentials=creds)


def _get_or_create_folder(service, parent_id: str, name: str) -> str:
    assert parent_id, "parent_id no puede estar vacío"
    assert name and name.strip(), "name no puede estar vacío"

    query = (
        f"name='{name}' and '{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    try:
        results = service.files().list(q=query, fields="files(id,name)").execute()
        folders = results.get("files", [])
        if folders:
            logger.debug("Carpeta existente '%s' | id=%s", name, folders[0]["id"])
            return folders[0]["id"]

        folder_meta = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = service.files().create(body=folder_meta, fields="id").execute()
        logger.info("Carpeta creada '%s' | id=%s", name, folder["id"])
        return folder["id"]
    except Exception as e:
        logger.error("Error gestionando carpeta '%s': %s", name, e)
        raise NotaCreditoError(f"Error creando carpeta en Drive: {e}") from e


def subir_a_drive(
    file_path: str,
    client_name: str,
    invoice_number: str,
    nc_number: str,
    date_str: str,
) -> str:
    assert file_path and os.path.exists(file_path), f"Archivo no encontrado: {file_path}"
    assert client_name and client_name.strip(), "client_name no puede estar vacío"
    assert invoice_number, "invoice_number no puede estar vacío"
    assert nc_number, "nc_number no puede estar vacío"

    logger.info("Subiendo archivo a Drive: %s", file_path)

    try:
        service = _get_drive_service()
        nc_folder_id = _get_or_create_folder(service, DRIVE_NC_FOLDER, "Notas credito")
        client_folder_id = _get_or_create_folder(service, nc_folder_id, client_name)
    except NotaCreditoError:
        raise
    except Exception as e:
        logger.error("Error accediendo a Drive: %s", e, exc_info=True)
        raise NotaCreditoError(f"Error accediendo a Drive: {e}") from e

    ext = os.path.splitext(file_path)[1] or ".pdf"
    filename = f"{date_str}_FE-{invoice_number}_NC-{nc_number}{ext}"

    try:
        from googleapiclient.http import MediaFileUpload
        import mimetypes

        mime = mimetypes.guess_type(file_path)[0] or "application/pdf"
        media = MediaFileUpload(file_path, mimetype=mime)
        metadata = {"name": filename, "parents": [client_folder_id]}

        existing = service.files().list(
            q=f"name='{filename}' and '{client_folder_id}' in parents and trashed=false",
            fields="files(id)",
        ).execute().get("files", [])

        if existing:
            service.files().update(fileId=existing[0]["id"], media_body=media).execute()
            logger.info("Archivo actualizado en Drive: %s", filename)
        else:
            service.files().create(body=metadata, media_body=media).execute()
            logger.info("Archivo subido a Drive: %s", filename)

        link = f"https://drive.google.com/drive/folders/{DRIVE_NC_FOLDER}"
        return link

    except Exception as e:
        logger.error("Error subiendo archivo a Drive: %s", e, exc_info=True)
        raise NotaCreditoError(f"Error subiendo archivo: {e}") from e


def obtener_items_factura(invoice_number: str) -> Tuple[Optional[Dict], List[Dict]]:
    assert invoice_number, "invoice_number no puede estar vacío"

    client = AlegraClient()
    try:
        numeric_id = invoice_number.upper().replace("FE-", "").replace("FE", "").strip()
        int_id = int(numeric_id)
    except ValueError:
        logger.error("Número de factura inválido: %s", invoice_number)
        return None, []

    logger.info("Consultando factura %s (id=%d) para nota crédito...", invoice_number, int_id)

    try:
        factura = client.get_invoice(str(int_id))
    except Exception as e:
        logger.error("Error consultando factura %s: %s", invoice_number, e, exc_info=True)
        return None, []

    if not factura:
        logger.warning("Factura %s no encontrada en Alegra", invoice_number)
        return None, []

    items = factura.get("items", [])
    productos = []
    for it in items:
        pid = it.get("id")
        qty = float(it.get("quantity", 0))
        name = it.get("name", "")
        desc = it.get("description", "")
        prod = {
            "id_producto": pid,
            "nombre": name or desc,
            "cantidad": qty,
            "precio": float(it.get("price", 0)),
        }
        if pid is not None and int(pid) in ID_PRODUCTO_A_ESTILO:
            estilo, tipo = ID_PRODUCTO_A_ESTILO[int(pid)]
            prod["estilo"] = estilo
            prod["tipo"] = tipo
        productos.append(prod)

    logger.info("Factura %s: %d items, total $%s", invoice_number, len(items), factura.get("total"))
    return factura, productos


def restaurar_inventario(
    items: List[Dict],
    invoice_number: str,
    nc_number: str,
    date_str: str,
) -> Dict[str, float]:
    assert items, "items no puede estar vacío"
    assert invoice_number, "invoice_number no puede estar vacío"
    assert nc_number, "nc_number no puede estar vacío"

    hoy = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    restaurado = {}
    items_stock = [it for it in items if it.get("estilo") and it.get("tipo")]

    if not items_stock:
        logger.info("Sin items de stock para restaurar")
        return restaurado

    logger.info("Restaurando inventario para %d items stock...", len(items_stock))

    try:
        _restaurar_en_google_sheets(items_stock, nc_number)
    except NotaCreditoError:
        raise
    except Exception as e:
        logger.warning("No se pudo restaurar en Google Sheets: %s", e)

    movimientos = []
    for it in items_stock:
        estilo = it["estilo"]
        tipo = it["tipo"]
        cantidad = it["cantidad"]

        key = f"{estilo}_{tipo}"
        if key not in restaurado:
            restaurado[key] = 0.0
        restaurado[key] += cantidad

        movimientos.append({
            "id": f"NC_{nc_number}_INV_{hoy}_{estilo.replace(' ', '_')}_{tipo}",
            "fecha": hoy,
            "tipo": "ENTRADA",
            "origen": f"NOTA_CREDITO NC-{nc_number} (anula FE-{invoice_number})",
            "estilo": estilo,
            "capa": f"{'botella_unidades' if tipo == 'botella' else 'barril_litros'}",
            "cantidad": cantidad,
            "nota": f"Restauración por NC-{nc_number}",
        })

    if movimientos:
        _agregar_a_ledger(movimientos)
        logger.info("Inventario restaurado: %s", dict(restaurado))

    return restaurado


def _restaurar_en_google_sheets(items: List[Dict], nc_number: str):
    if not items:
        return

    svc = _get_google_sheets_svc()
    sheet_id = os.getenv("SHEET_INVENTARIO", SHEET_INVENTARIO)
    assert sheet_id, "SHEET_INVENTARIO no configurado"

    try:
        rows = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="A:ZZ"
        ).execute().get("values", [])
    except Exception as e:
        logger.error("Error leyendo Google Sheets: %s", e, exc_info=True)
        raise NotaCreditoError(f"Error leyendo Google Sheets: {e}") from e

    last = None
    row_num = None
    for i in range(len(rows) - 1, -1, -1):
        if any(c.strip() for c in rows[i]):
            last = rows[i]
            row_num = i + 1
            break

    if not last:
        logger.warning("No se encontró fila de inventario en Google Sheets")
        raise NotaCreditoError("No se encontró fila de inventario")

    col_bot = {e: None for e in ESTILOS}
    col_lit = {e: None for e in ESTILOS}
    for j, cell in enumerate(last):
        up = cell.strip().upper()
        est = next((e for e in ESTILOS if e in up), None)
        if not est:
            continue
        vcol = j - 1
        if "BOTELLA" in up:
            col_bot[est] = vcol
        elif "LITRO" in up:
            col_lit[est] = vcol

    def _col_letter(idx):
        s = ""
        idx += 1
        while idx > 0:
            idx, r = divmod(idx - 1, 26)
            s = chr(65 + r) + s
        return s

    tab_info = svc.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets.properties.title"
    ).execute()
    tab_name = tab_info["sheets"][0]["properties"]["title"]

    updates = []
    for it in items:
        estilo = it.get("estilo")
        tipo = it.get("tipo")
        cantidad = it.get("cantidad", 0)
        if not estilo or not tipo or cantidad <= 0:
            continue
        col = col_bot[estilo] if tipo == "botella" else col_lit[estilo]
        if col is None:
            logger.warning("No se encontró columna para %s (%s) en el sheet", estilo, tipo)
            continue
        current = float(last[col].strip()) if col < len(last) and last[col].strip() else 0
        new_val = current + cantidad
        rng = f"'{tab_name}'!{_col_letter(col)}{row_num}"
        updates.append({"range": rng, "values": [[new_val]]})
        logger.info("  %s (%s): %s → %s (+%s)", estilo, tipo, current, new_val, cantidad)

    if updates:
        try:
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": updates},
            ).execute()
            logger.info("Inventario restaurado en Google Sheets (NC-%s)", nc_number)
        except Exception as e:
            logger.error("Error actualizando Google Sheets: %s", e, exc_info=True)
            raise NotaCreditoError(f"Error actualizando Google Sheets: {e}") from e


def _agregar_a_ledger(movimientos: List[Dict]):
    assert movimientos, "movimientos no puede estar vacío"
    ledger = {"version": "2.0", "actualizado": datetime.now(timezone.utc).isoformat()}

    if os.path.exists(LEDGER_PATH):
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except json.JSONDecodeError:
            logger.warning("Ledger corrupto, se creará uno nuevo")
        except FileNotFoundError:
            pass

    existing = ledger.get("movimientos", [])
    for mov in movimientos:
        existing.append(mov)

    ledger["movimientos"] = existing
    ledger["actualizado"] = datetime.now(timezone.utc).isoformat()

    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
    logger.info("%d movimientos agregados al ledger (%s)", len(movimientos), LEDGER_PATH)


def procesar_nota_credito(
    invoice_number: str,
    nc_number: str,
    client_name: str,
    date_str: str,
    file_path: str = "",
    observations: str = "",
    responsable: str = "",
) -> Dict:
    assert invoice_number, "invoice_number requerido"
    assert nc_number, "nc_number requerido"
    assert client_name and client_name.strip(), "client_name requerido"
    assert date_str, "date_str requerido"

    logger.info("=" * 50)
    logger.info("PROCESANDO NOTA CRÉDITO NC-%s | Factura=%s | Cliente=%s | Fecha=%s",
                nc_number, invoice_number, client_name, date_str)
    logger.info("=" * 50)

    resultado = {
        "ok": False,
        "factura": invoice_number,
        "nc": nc_number,
        "cliente": client_name,
        "fecha": date_str,
        "responsable": responsable,
        "observaciones": observations,
        "drive_link": None,
        "items_factura": [],
        "factura_total": 0,
        "inventario_restaurado": {},
    }

    factura, items = obtener_items_factura(invoice_number)
    if not factura:
        resultado["error"] = f"Factura {invoice_number} no encontrada en Alegra"
        logger.error(resultado["error"])
        return resultado

    resultado["items_factura"] = [
        {
            "nombre": it["nombre"],
            "cantidad": it["cantidad"],
            "precio": it["precio"],
            "estilo": it.get("estilo", ""),
            "tipo": it.get("tipo", ""),
        }
        for it in items
    ]
    resultado["factura_total"] = float(factura.get("total", 0))
    logger.info("Factura %s: %d items, total $%s", invoice_number, len(items), resultado["factura_total"])

    if items:
        try:
            resultado["inventario_restaurado"] = restaurar_inventario(
                items, invoice_number, nc_number, date_str
            )
        except NotaCreditoError as e:
            logger.error("Error restaurando inventario: %s", e)
            resultado["inventario_error"] = str(e)
        except Exception as e:
            logger.error("Error inesperado restaurando inventario: %s", e, exc_info=True)
            resultado["inventario_error"] = str(e)

    if file_path and os.path.exists(file_path):
        try:
            resultado["drive_link"] = subir_a_drive(
                file_path, client_name, invoice_number, nc_number, date_str
            )
        except NotaCreditoError as e:
            logger.error("Error subiendo a Drive: %s", e)
            resultado["drive_error"] = str(e)
        except Exception as e:
            logger.error("Error inesperado subiendo a Drive: %s", e, exc_info=True)
            resultado["drive_error"] = str(e)
    elif file_path:
        logger.warning("Archivo no encontrado: %s", file_path)

    resultado["ok"] = "error" not in resultado and not resultado.get("inventario_error") and not resultado.get("drive_error")

    logger.info("Nota crédito procesada: %s", "OK" if resultado["ok"] else "ERROR")
    return resultado


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Procesar Nota Crédito Madre Monte")
    parser.add_argument("--factura", required=True, help="Número de factura (ej: FE-1234)")
    parser.add_argument("--nc", required=True, help="Número de nota crédito (ej: NC-001)")
    parser.add_argument("--cliente", required=True, help="Nombre del cliente")
    parser.add_argument("--fecha", required=True, help="Fecha (YYYY-MM-DD)")
    parser.add_argument("--archivo", help="Ruta al archivo PDF/imagen de la NC")
    parser.add_argument("--obs", default="", help="Observaciones")
    parser.add_argument("--responsable", default="", help="Responsable")
    args = parser.parse_args()

    resultado = procesar_nota_credito(
        invoice_number=args.factura,
        nc_number=args.nc,
        client_name=args.cliente,
        date_str=args.fecha,
        file_path=args.archivo,
        observations=args.obs,
        responsable=args.responsable,
    )

    print("\n" + "=" * 60)
    print("RESULTADO NOTA CREDITO")
    print("=" * 60)
    print(f"Factura: {resultado['factura']}")
    print(f"Total factura: ${resultado['factura_total']:,.0f}")
    print(f"Items: {len(resultado['items_factura'])}")
    for it in resultado["items_factura"]:
        print(f"  - {it['nombre']} x{it['cantidad']} @ ${it['precio']:,.0f}")
    print(f"Inventario restaurado: {resultado.get('inventario_restaurado') or 'N/A'}")
    print(f"Drive: {resultado.get('drive_link') or 'No subido'}")
    print(f"Estado: {'OK' if resultado['ok'] else 'ERROR: ' + resultado.get('error', '')}")
