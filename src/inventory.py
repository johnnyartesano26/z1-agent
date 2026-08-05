import csv
import os
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class InventoryError(Exception):
    pass


class StockInsufficientError(InventoryError):
    def __init__(self, codigo: str, disponible: float, solicitado: float):
        self.codigo = codigo
        self.disponible = disponible
        self.solicitado = solicitado
        super().__init__(f"Stock insuficiente para {codigo}: {disponible} disponible, {solicitado} solicitado")


class InventoryManager:
    VALID_TYPES = ("SALIDA", "ENTRADA", "AJUSTE")

    def __init__(self, inventory_csv_path: str):
        assert inventory_csv_path, "inventory_csv_path no puede estar vacío"
        self.inventory_csv_path = inventory_csv_path
        self._create_if_not_exists()
        logger.info("InventoryManager inicializado | csv=%s", inventory_csv_path)

    def _create_if_not_exists(self):
        need_headers = (
            not os.path.exists(self.inventory_csv_path)
            or os.path.getsize(self.inventory_csv_path) == 0
        )
        if need_headers:
            os.makedirs(os.path.dirname(self.inventory_csv_path), exist_ok=True)
            with open(self.inventory_csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "id_transaccion", "tipo", "fecha", "codigo_producto",
                    "cantidad", "stock_resultante", "registrado_por"
                ])
            logger.info("Archivo de inventario creado: %s", self.inventory_csv_path)

    def get_current_stock(self, style_code: str) -> float:
        assert style_code and isinstance(style_code, str), "style_code debe ser un string no vacío"
        stock = 0.0
        try:
            with open(self.inventory_csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("codigo_producto", "").strip() == style_code.strip():
                        stock = float(row.get("stock_resultante", 0))
            logger.debug("Stock actual de %s: %s", style_code, stock)
            return stock
        except FileNotFoundError:
            logger.warning("Archivo de inventario no encontrado: %s", self.inventory_csv_path)
            return 0.0
        except (csv.Error, ValueError, KeyError) as e:
            logger.error("Error al leer stock de %s: %s", style_code, e)
            return 0.0

    def get_all_stock(self) -> Dict[str, float]:
        stock = {}
        try:
            with open(self.inventory_csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row.get("codigo_producto", "").strip()
                    if code:
                        stock[code] = float(row.get("stock_resultante", 0))
            logger.debug("Stock completo: %d productos", len(stock))
            return stock
        except FileNotFoundError:
            logger.warning("Archivo de inventario no encontrado: %s", self.inventory_csv_path)
            return {}
        except (csv.Error, ValueError) as e:
            logger.error("Error al leer inventario completo: %s", e)
            return {}

    def deduct_stock(self, items_vendidos: Dict[str, float],
                     registrado_por: str = "Z1",
                     allow_negative: bool = False) -> Dict[str, float]:
        assert isinstance(items_vendidos, dict), "items_vendidos debe ser un diccionario"
        assert items_vendidos, "items_vendidos no puede estar vacío"
        for code, qty in items_vendidos.items():
            assert isinstance(code, str) and code.strip(), f"Código inválido: {code}"
            assert isinstance(qty, (int, float)) and qty > 0, f"Cantidad inválida para {code}: {qty}"

        today = datetime.now(timezone.utc).date().isoformat()
        new_rows = []
        updated_stock = {}

        current_stock = self.get_all_stock()

        for style_code, cantidad in items_vendidos.items():
            old_stock = current_stock.get(style_code, 0.0)
            new_stock = old_stock - cantidad

            if not allow_negative:
                assert new_stock >= 0, f"Stock insuficiente de {style_code}: {old_stock} disponible, {cantidad} solicitado"

            new_stock = max(0, new_stock)
            updated_stock[style_code] = new_stock
            new_rows.append({
                "id_transaccion": f"Z1_INV_{today}_{style_code}",
                "tipo": "SALIDA",
                "fecha": today,
                "codigo_producto": style_code,
                "cantidad": cantidad,
                "stock_resultante": new_stock,
                "registrado_por": registrado_por,
            })
            logger.info("Inventario: %s %s → %s (-%s)", style_code, old_stock, new_stock, cantidad)

        self._append_rows(new_rows)
        return dict(updated_stock)

    def add_stock(self, items_a_agregar: Dict[str, float],
                  registrado_por: str = "Z1",
                  origen: str = "") -> Dict[str, float]:
        assert isinstance(items_a_agregar, dict), "items_a_agregar debe ser un diccionario"
        assert items_a_agregar, "items_a_agregar no puede estar vacío"
        for code, qty in items_a_agregar.items():
            assert isinstance(code, str) and code.strip(), f"Código inválido: {code}"
            assert isinstance(qty, (int, float)) and qty > 0, f"Cantidad inválida para {code}: {qty}"

        today = datetime.now(timezone.utc).date().isoformat()
        new_rows = []
        updated_stock = {}

        current_stock = self.get_all_stock()

        for style_code, cantidad in items_a_agregar.items():
            old_stock = current_stock.get(style_code, 0.0)
            new_stock = old_stock + cantidad
            updated_stock[style_code] = new_stock
            new_rows.append({
                "id_transaccion": f"Z1_INV_{today}_{style_code}",
                "tipo": "ENTRADA",
                "fecha": today,
                "codigo_producto": style_code,
                "cantidad": cantidad,
                "stock_resultante": new_stock,
                "registrado_por": f"{registrado_por}{' | ' + origen if origen else ''}",
            })
            logger.info("Inventario: %s %s → %s (+%s) [%s]", style_code, old_stock, new_stock, cantidad, origen or "manual")

        self._append_rows(new_rows)
        return dict(updated_stock)

    def _append_rows(self, rows: List[Dict]):
        fieldnames = [
            "id_transaccion", "tipo", "fecha", "codigo_producto",
            "cantidad", "stock_resultante", "registrado_por"
        ]
        try:
            with open(self.inventory_csv_path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writerows(rows)
            logger.debug("%d filas agregadas al inventario", len(rows))
        except (IOError, OSError) as e:
            logger.error("Error al escribir en inventario %s: %s", self.inventory_csv_path, e)
            raise InventoryError(f"No se pudo escribir en el inventario: {e}") from e
