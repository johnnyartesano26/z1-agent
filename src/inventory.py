import csv
import os
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class InventoryManager:
    def __init__(self, inventory_csv_path: str):
        self.inventory_csv_path = inventory_csv_path
        self._create_if_not_exists()

    def _create_if_not_exists(self):
        if not os.path.exists(self.inventory_csv_path):
            os.makedirs(os.path.dirname(self.inventory_csv_path), exist_ok=True)
            with open(self.inventory_csv_path, 'w', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["id_transaccion", "tipo", "fecha", "codigo_producto", "cantidad", "stock_resultante", "registrado_por"])

    def get_current_stock(self, style_code: str) -> float:
        stock = 0.0
        try:
            with open(self.inventory_csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("codigo_producto") == style_code:
                        stock = float(row.get("stock_resultante", 0))
                return stock
        except (FileNotFoundError, csv.Error) as e:
            logger.error(f"Error al leer stock: {e}")
            return 0.0

    def deduct_stock(self, items_vendidos: Dict[str, float]) -> Dict[str, float]:
        today = datetime.now().date().isoformat()
        new_rows = []
        updated_stock = defaultdict(float)

        current_stock = {}
        try:
            with open(self.inventory_csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row.get("codigo_producto")
                    if code:
                        current_stock[code] = float(row.get("stock_resultante", 0))
        except (FileNotFoundError, csv.Error):
            pass

        for style_code, cantidad in items_vendidos.items():
            old_stock = current_stock.get(style_code, 0.0)
            new_stock = max(0, old_stock - cantidad)
            updated_stock[style_code] = new_stock
            new_rows.append({
                "id_transaccion": f"Z1_INV_{today}_{style_code}",
                "tipo": "SALIDA",
                "fecha": today,
                "codigo_producto": style_code,
                "cantidad": cantidad,
                "stock_resultante": new_stock,
                "registrado_por": "Z1"
            })
            logger.info(f"📦 Inventario: {style_code} → {old_stock} → {new_stock}")

        with open(self.inventory_csv_path, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=["id_transaccion", "tipo", "fecha", "codigo_producto", "cantidad", "stock_resultante", "registrado_por"])
            writer.writerows(new_rows)

        return dict(updated_stock)

