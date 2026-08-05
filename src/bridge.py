from flask import Flask, jsonify, request
import openpyxl
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _safe_read_excel(file_path: str, sheet_name: str, min_row: int, max_row: int, has_header: bool = False):
    if not os.path.exists(file_path):
        logger.warning("Archivo no encontrado: %s", file_path)
        return None, "Archivo no encontrado"

    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
    except Exception as e:
        logger.error("Error al abrir %s: %s", file_path, e)
        return None, f"Error al abrir el archivo: {e}"

    try:
        ws = wb[sheet_name]
    except KeyError:
        available = wb.sheetnames
        wb.close()
        logger.warning("Pestaña '%s' no encontrada en %s. Disponibles: %s", sheet_name, file_path, available)
        return None, f"Pestaña '{sheet_name}' no encontrada. Disponibles: {available}"

    rows = []
    for row in ws.iter_rows(min_row=min_row, max_row=max_row, values_only=True):
        rows.append(row)

    wb.close()
    return rows, None


def _extract_inventory_data(rows):
    data = []
    for row in rows:
        if row and len(row) > 5 and row[0] and row[4]:
            data.append({
                "estilo": str(row[0]),
                "litros_total": row[4],
                "cop": row[5] if len(row) > 5 else None,
            })
    return data


def _extract_fermentador_data(rows):
    data = []
    for row in rows:
        if row and len(row) > 4 and row[0]:
            data.append({
                "fermentador": str(row[0]),
                "litros": row[2] if len(row) > 2 else None,
                "estilo": str(row[4]) if len(row) > 4 and row[4] else None,
                "alarma": str(row[5]) if len(row) > 5 and row[5] else None,
            })
    return data


def _extract_ventas_data(rows):
    data = []
    for row in rows:
        if row and len(row) > 2 and row[0]:
            data.append({
                "mes": str(row[0]),
                "facturas": row[1] if len(row) > 1 else None,
                "total": row[2] if len(row) > 2 else None,
            })
    return data


def create_app(inventory_excel_path: str = "data/inventario_ejemplo.xlsx",
               sales_excel_path: str = "data/ventas_ejemplo.xlsx"):
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    @app.errorhandler(404)
    def not_found(e):
        logger.warning("404 Not Found: %s", request.path)
        return jsonify({"error": "Recurso no encontrado", "path": request.path}), 404

    @app.errorhandler(500)
    def internal_error(e):
        logger.error("500 Internal Error en %s: %s", request.path, e, exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500

    @app.errorhandler(Exception)
    def handle_exception(e):
        logger.error("Error no manejado en %s: %s", request.path, e, exc_info=True)
        return jsonify({"error": "Error del servidor", "detail": str(e)}), 500

    @app.before_request
    def log_request():
        logger.info("→ %s %s [%s]", request.method, request.path, request.remote_addr)

    @app.after_request
    def log_response(response):
        logger.info("← %s %s → %d", request.method, request.path, response.status_code)
        return response

    @app.route("/health")
    def health():
        return jsonify({
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "2.0",
        })

    @app.route("/inventario")
    def inventario():
        rows, error = _safe_read_excel(inventory_excel_path, "Resumen", min_row=4, max_row=9)
        if error:
            return jsonify({"error": error}), 404
        data = _extract_inventory_data(rows)
        logger.info("Inventario consultado: %d estilos", len(data))
        return jsonify(data)

    @app.route("/inventario/fermentadores")
    def fermentadores():
        rows, error = _safe_read_excel(inventory_excel_path, "Fermentadores", min_row=4, max_row=10)
        if error:
            return jsonify({"error": error}), 404
        data = _extract_fermentador_data(rows)
        logger.info("Fermentadores consultados: %d registros", len(data))
        return jsonify(data)

    @app.route("/ventas/resumen")
    def ventas_resumen():
        rows, error = _safe_read_excel(sales_excel_path, "RESUMEN", min_row=4, max_row=14)
        if error:
            return jsonify({"error": error}), 404
        data = _extract_ventas_data(rows)
        logger.info("Ventas consultadas: %d meses", len(data))
        return jsonify(data)

    @app.route("/factura/<invoice_id>")
    def factura(invoice_id):
        assert invoice_id, "Se requiere ID de factura"
        from .alegra_client import AlegraClient
        client = AlegraClient()
        try:
            factura = client.get_invoice(invoice_id)
            if not factura:
                return jsonify({"error": f"Factura {invoice_id} no encontrada"}), 404
            return jsonify(factura)
        except Exception as e:
            logger.error("Error consultando factura %s: %s", invoice_id, e)
            return jsonify({"error": str(e)}), 500
        finally:
            client.close()

    logger.info("Bridge Flask creado | inventario=%s ventas=%s", inventory_excel_path, sales_excel_path)
    return app
