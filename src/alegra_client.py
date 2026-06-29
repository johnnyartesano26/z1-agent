import requests
import os
from typing import Dict, List, Optional
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class AlegraClient:
    def __init__(self, api_key: str = None, email: str = None, base_url: str = None):
        self.base_url = base_url or os.getenv("ALEGRA_API_URL", "https://api.alegra.com/api/v1/")
        self.auth = (email or os.getenv("ALEGRA_EMAIL"), api_key or os.getenv("ALEGRA_API_KEY"))
        self.session = requests.Session()
        self.session.auth = self.auth

    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error en request a Alegra: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Respuesta: {e.response.text}")
            return {"error": str(e), "status": getattr(e.response, 'status_code', None)}

    def get_invoices(self, page: int = 1, limit: int = 30) -> List[Dict]:
        endpoint = f"invoices?page={page}&limit={limit}"
        response = self._request("GET", endpoint)
        if isinstance(response, list):
            return response
        logger.error(f"Error al obtener facturas: {response}")
        return []

    def create_invoice(self, client_id: str, items: List[Dict], due_date: str) -> Dict:
        data = {
            "client": client_id,
            "items": items,
            "dueDate": due_date
        }
        return self._request("POST", "invoices", data)

    def get_clients(self, page: int = 1, limit: int = 30) -> List[Dict]:
        endpoint = f"contacts?page={page}&limit={limit}"
        response = self._request("GET", endpoint)
        if isinstance(response, list):
            return response
        logger.error(f"Error al obtener clientes: {response}")
        return []

    def search_client_by_name(self, name: str) -> Optional[Dict]:
        all_clients = []
        page = 1
        while True:
            clients = self.get_clients(page=page)
            if not clients:
                break
            all_clients.extend(clients)
            if len(clients) < 30:
                break
            page += 1
        for client in all_clients:
            if client.get("name", "").lower() == name.lower():
                return client
        return None

