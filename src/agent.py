import time
import openpyxl
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import logging

from dotenv import load_dotenv
from .config import PRODUCT_MAP, debug_on_error, validate_items_list

load_dotenv()

logger = logging.getLogger(__name__)


class Z1AgentError(Exception):
    pass


class Z1Agent:
    PRODUCT_MAP = PRODUCT_MAP

    def __init__(self, sheet_path: str, alegra_client, inventory_manager):
        assert sheet_path, "sheet_path no puede estar vacío"
        self.sheet_path = sheet_path
        self.alegra = alegra_client
        self.inventory = inventory_manager
        logger.info("Z1Agent inicializado | sheet=%s", sheet_path)

    def _extract_code(self, value: str) -> Optional[str]:
        import re
        if not isinstance(value, str) or not value:
            return None
        match = re.search(r'PTB\d{2}|PTL\d{2}|DOM\d{2}', str(value).upper())
        return match.group(0) if match else None

    def _should_process(self, row: tuple, headers: list) -> Tuple[bool, Optional[str]]:
        try:
            facturar_index = headers.index("Facturar: Sí") if "Facturar: Sí" in headers else -1
            factura_index = headers.index("Factura") if "Factura" in headers else -1
        except ValueError:
            return False, None

        if facturar_index == -1:
            return False, None

        debe_facturar = row[facturar_index] if len(row) > facturar_index else False
        tiene_factura = row[factura_index] if factura_index > -1 and len(row) > factura_index else False

        debe_facturar_str = str(debe_facturar).strip() if debe_facturar else ""
        tiene_factura_str = str(tiene_factura).strip() if tiene_factura else ""

        if debe_facturar_str.lower() in ("sí", "si", "true", "1", "yes") and not tiene_factura_str:
            return True, None
        return False, None

    def run(self) -> Dict:
        start = time.time()
        facturas_creadas = 0
        productos_vendidos = defaultdict(float)
        errors = []
        warnings = []

        logger.info("=" * 50)
        logger.info("Z1Agent iniciando ejecución | sheet=%s", self.sheet_path)
        logger.info("=" * 50)

        if not os.path.exists(self.sheet_path):
            msg = f"Archivo no encontrado: {self.sheet_path}"
            logger.error(msg)
            return {"ok": False, "error": msg, "duracion_seg": round(time.time() - start, 2)}

        try:
            wb = openpyxl.load_workbook(self.sheet_path)
        except Exception as e:
            msg = f"Error al abrir el archivo {self.sheet_path}: {e}"
            logger.error(msg, exc_info=True)
            return {"ok": False, "error": msg, "duracion_seg": round(time.time() - start, 2)}

        try:
            ws = wb["Respuestas de formulario 1"]
        except KeyError:
            ws_name = wb.sheetnames[0] if wb.sheetnames else "Sheet1"
            logger.warning("Pestaña 'Respuestas de formulario 1' no encontrada, usando '%s'", ws_name)
            ws = wb[ws_name]

        headers = [str(c.value).strip() for c in next(ws.iter_rows(min_row=1, max_row=1)) if c.value]
        assert headers, "El sheet no tiene encabezados"

        col_cliente = headers.index("Nombre del cliente") if "Nombre del cliente" in headers else -1
        col_productos = [headers.index(col) for col in headers if "Producto" in col][:5]
        col_cantidades = [headers.index(col) for col in headers if "Cantidad" in col][:5]

        if col_cliente == -1:
            logger.error("Columna 'Nombre del cliente' no encontrada en headers: %s", headers)
            wb.close()
            return {"ok": False, "error": "Columna 'Nombre del cliente' no encontrada", "duracion_seg": round(time.time() - start, 2)}

        if not col_productos or not col_cantidades:
            logger.warning("Columnas de productos/cantidades no encontradas. Headers: %s", headers)

        rows_processed = 0
        rows_skipped = 0

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            should_process, skip_reason = self._should_process(row, headers)
            if not should_process:
                rows_skipped += 1
                continue

            rows_processed += 1

            cliente_nombre = row[col_cliente] if col_cliente > -1 and len(row) > col_cliente else None
            if not cliente_nombre or not str(cliente_nombre).strip():
                errors.append(f"Fila {row_idx}: Cliente vacío")
                logger.warning("Fila %d: Nombre de cliente vacío", row_idx)
                continue

            cliente_nombre = str(cliente_nombre).strip()
            logger.info("Procesando fila %d | cliente='%s'", row_idx, cliente_nombre)

            try:
                client = self.alegra.search_client_by_name(cliente_nombre)
            except Exception as e:
                errors.append(f"Fila {row_idx}: Error buscando cliente '{cliente_nombre}': {e}")
                logger.error("Error al buscar cliente '%s': %s", cliente_nombre, e, exc_info=True)
                continue

            if not client:
                errors.append(f"Fila {row_idx}: Cliente '{cliente_nombre}' no encontrado en Alegra")
                logger.warning("Cliente no encontrado: '%s'", cliente_nombre)
                continue

            items = []
            for prod_col, cant_col in zip(col_productos, col_cantidades):
                if prod_col > -1 and cant_col > -1 and len(row) > prod_col and len(row) > cant_col:
                    ref = self._extract_code(str(row[prod_col]) if row[prod_col] else "")
                    raw_cantidad = row[cant_col]
                    try:
                        cantidad = float(raw_cantidad) if raw_cantidad is not None and str(raw_cantidad).strip() else 0
                    except (ValueError, TypeError):
                        cantidad = 0

                    if ref and cantidad > 0 and ref in self.PRODUCT_MAP:
                        product = self.PRODUCT_MAP[ref]
                        items.append({
                            "id": product["id"],
                            "quantity": cantidad,
                            "price": product["price"],
                            "tax": [{"id": 4}] if ref != "DOM01" else [],
                        })
                        productos_vendidos[product.get("style", "")] += cantidad
                        logger.debug("  Item: %s x%d @ $%d", ref, cantidad, product["price"])

            if not items:
                warnings.append(f"Fila {row_idx}: '{cliente_nombre}' sin ítems válidos")
                logger.warning("Fila %d: '%s' sin ítems válidos para facturar", row_idx, cliente_nombre)
                continue

            assert items, f"No se generaron ítems para {cliente_nombre}"
            validate_items_list(items)

            due_date = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()

            try:
                with debug_on_error():
                    factura = self.alegra.create_invoice(str(client["id"]), items, due_date)
            except Exception as e:
                errors.append(f"Fila {row_idx}: Error creando factura para '{cliente_nombre}': {e}")
                logger.error("Error al crear factura para '%s': %s", cliente_nombre, e, exc_info=True)
                continue

            if isinstance(factura, dict) and "error" in factura:
                errors.append(f"Fila {row_idx}: Error API al facturar '{cliente_nombre}': {factura['error']}")
                logger.error("Error API al facturar '%s': %s", cliente_nombre, factura["error"])
                continue

            if isinstance(factura, dict) and factura.get("id"):
                facturas_creadas += 1
                factura_id = factura["id"]
                logger.info("Factura %s creada | cliente='%s' items=%d", factura_id, cliente_nombre, len(items))

                factura_index = headers.index("Factura") if "Factura" in headers else -1
                if factura_index > -1:
                    ws.cell(row=row_idx, column=factura_index + 1, value=f"Sí - Factura {factura_id}")
            else:
                errors.append(f"Fila {row_idx}: Respuesta inesperada al facturar '{cliente_nombre}'")
                logger.error("Respuesta inesperada al facturar '%s': %s", cliente_nombre, factura)

        stock_actualizado = {}
        if productos_vendidos:
            logger.info("Actualizando inventario: %s", dict(productos_vendidos))
            try:
                stock_actualizado = self.inventory.deduct_stock(dict(productos_vendidos))
            except AssertionError as e:
                logger.error("Error de validación en inventario: %s", e)
                errors.append(f"Error de inventario: {e}")
            except Exception as e:
                logger.error("Error al actualizar inventario: %s", e, exc_info=True)
                errors.append(f"Error de inventario: {e}")

        try:
            wb.save(self.sheet_path)
            logger.info("Sheet guardado: %s", self.sheet_path)
        except Exception as e:
            logger.error("Error al guardar sheet: %s", e, exc_info=True)
            errors.append(f"No se pudo guardar el sheet: {e}")

        wb.close()

        duracion = round(time.time() - start, 2)
        resultado = {
            "ok": len(errors) == 0,
            "facturas_creadas": facturas_creadas,
            "filas_procesadas": rows_processed,
            "filas_omitidas": rows_skipped,
            "productos_vendidos": dict(productos_vendidos),
            "stock_actualizado": stock_actualizado,
            "errors": errors,
            "warnings": warnings,
            "duracion_seg": duracion,
        }

        logger.info("=" * 50)
        logger.info("Z1Agent finalizado | facturas=%d errores=%d warnings=%d duración=%.1fs",
                    facturas_creadas, len(errors), len(warnings), duracion)
        logger.info("=" * 50)

        return resultado
