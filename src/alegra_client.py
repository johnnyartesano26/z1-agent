import time
import requests
import os
from typing import Dict, List, Optional
from urllib.parse import quote
import logging

from dotenv import load_dotenv
from .config import (
    ALEGRA_API_URL,
    ALEGRA_EMAIL,
    ALEGRA_API_KEY,
    debug_on_error,
    setup_logging,
    PRODUCT_MAP,
)

load_dotenv()

logger = logging.getLogger(__name__)


class AlegraConnectionError(Exception):
    pass


class AlegraRateLimitError(Exception):
    pass


class AlegraClientError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class AlegraClient:
    def __init__(self, api_key: str = None, email: str = None, base_url: str = None,
                 max_retries: int = 3, retry_delay: float = 1.0):
        self.base_url = base_url or ALEGRA_API_URL
        self.auth = (
            email or ALEGRA_EMAIL,
            api_key or ALEGRA_API_KEY,
        )
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        assert self.auth[0], "ALEGRA_EMAIL no configurado"
        assert self.auth[1], "ALEGRA_API_KEY no configurado"
        assert self.base_url, "ALEGRA_API_URL no configurada"

        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            "User-Agent": "Z1-Agent/2.0 (Madre Monte Facturacion)",
            "Accept": "application/json",
        })

        logger.info("AlegraClient inicializado | base_url=%s | max_retries=%d", self.base_url, self.max_retries)

    def close(self):
        try:
            self.session.close()
            logger.debug("Sesión HTTP cerrada")
        except Exception as e:
            logger.warning("Error al cerrar sesión HTTP: %s", e)

    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        url = f"{self.base_url}{endpoint}"
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug("Request %s %s (intento %d/%d)", method, url, attempt, self.max_retries)
                response = self.session.request(method, url, json=data, timeout=30)

                if response.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("Rate limit (429) en %s %s | esperando %ds", method, endpoint, wait)
                    time.sleep(wait)
                    continue

                if response.status_code >= 500:
                    logger.warning("Error servidor %d en %s %s (intento %d/%d)",
                                   response.status_code, method, endpoint, attempt, self.max_retries)
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * attempt)
                        continue
                    raise AlegraClientError(
                        f"Error del servidor {response.status_code}",
                        status_code=response.status_code,
                        response_text=response.text,
                    )

                response.raise_for_status()
                result = response.json()

                if attempt > 1:
                    logger.info("Request exitoso en intento %d: %s %s", attempt, method, endpoint)

                return result

            except requests.exceptions.Timeout as e:
                last_error = e
                logger.warning("Timeout en %s %s (intento %d/%d): %s",
                               method, endpoint, attempt, self.max_retries, e)
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
                    continue
                raise AlegraConnectionError(f"Timeout tras {self.max_retries} intentos: {e}") from e

            except requests.exceptions.ConnectionError as e:
                last_error = e
                logger.warning("Error de conexión en %s %s (intento %d/%d): %s",
                               method, endpoint, attempt, self.max_retries, e)
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
                    continue
                raise AlegraConnectionError(f"Error de conexión tras {self.max_retries} intentos: {e}") from e

            except requests.exceptions.HTTPError as e:
                logger.error("HTTP %s en %s %s: %s",
                             e.response.status_code if e.response else '?', method, endpoint, e)
                if hasattr(e, 'response') and e.response is not None:
                    logger.error("Respuesta: %s", e.response.text[:500])
                return {"error": str(e), "status": getattr(e.response, 'status_code', None)}

            except requests.exceptions.RequestException as e:
                last_error = e
                logger.error("Error de request en %s %s: %s", method, endpoint, e)
                return {"error": str(e), "status": None}

        raise AlegraConnectionError(f"Falló tras {self.max_retries} intentos: {last_error}")

    def get_invoices(self, page: int = 1, limit: int = 30, query: Optional[str] = None) -> List[Dict]:
        assert page > 0, "page debe ser > 0"
        assert 1 <= limit <= 50, "limit debe estar entre 1 y 50"

        endpoint = f"invoices?page={page}&limit={limit}"
        if query:
            endpoint += f"&query={quote(query)}"

        logger.info("Consultando facturas | page=%d limit=%d", page, limit)
        with debug_on_error():
            response = self._request("GET", endpoint)
            if isinstance(response, list):
                logger.info("Facturas obtenidas: %d resultados", len(response))
                return response
            logger.error("Formato inesperado al obtener facturas: %s", type(response))
            return []

    def get_invoice(self, invoice_id: str) -> Optional[Dict]:
        assert invoice_id, "invoice_id no puede estar vacío"
        invoice_id = str(invoice_id).strip()

        logger.info("Consultando factura %s", invoice_id)
        try:
            response = self._request("GET", f"invoices/{invoice_id}")
            if isinstance(response, dict) and "id" in response:
                logger.info("Factura %s encontrada | total=%s cliente=%s",
                            invoice_id, response.get("total"), response.get("client"))
                return response
            logger.warning("Factura %s no encontrada o formato inesperado", invoice_id)
            return None
        except (AlegraConnectionError, AlegraClientError) as e:
            logger.error("Error al obtener factura %s: %s", invoice_id, e)
            return None

    def create_invoice(self, client_id: str, items: List[Dict], due_date: str) -> Dict:
        assert client_id, "client_id no puede estar vacío"
        assert isinstance(items, list) and items, "items debe ser una lista no vacía"
        for i, it in enumerate(items):
            assert "id" in it, f"Item {i} no tiene 'id'"
            assert "quantity" in it, f"Item {i} no tiene 'quantity'"
            assert float(it["quantity"]) > 0, f"Item {i} quantity debe ser > 0"

        data = {
            "client": client_id,
            "items": items,
            "dueDate": due_date,
            "status": "draft",
        }

        logger.info("Creando factura | cliente=%s items=%d due=%s", client_id, len(items), due_date)
        try:
            response = self._request("POST", "invoices", data)
            if isinstance(response, dict) and "id" in response:
                logger.info("Factura %s creada | cliente=%s", response.get("id"), client_id)
            else:
                logger.error("Error al crear factura: %s", response)
            return response
        except (AlegraConnectionError, AlegraClientError) as e:
            logger.error("Error al crear factura para %s: %s", client_id, e)
            return {"error": str(e), "status": getattr(e, 'status_code', None)}

    def get_clients(self, page: int = 1, limit: int = 30) -> List[Dict]:
        assert page > 0, "page debe ser > 0"
        assert 1 <= limit <= 50, "limit debe estar entre 1 y 50"

        endpoint = f"contacts?page={page}&limit={limit}"

        logger.info("Consultando clientes | page=%d limit=%d", page, limit)
        with debug_on_error():
            response = self._request("GET", endpoint)
            if isinstance(response, list):
                logger.info("Clientes obtenidos: %d resultados", len(response))
                return response
            logger.error("Formato inesperado al obtener clientes: %s", type(response))
            return []

    def search_client_by_name(self, name: str) -> Optional[Dict]:
        assert name and name.strip(), "nombre de cliente no puede estar vacío"
        name = name.strip()

        logger.info("Buscando cliente: '%s'", name)
        endpoint = f"contacts?name={quote(name)}&limit=10"

        try:
            response = self._request("GET", endpoint)
            if not isinstance(response, list):
                logger.warning("Respuesta inesperada al buscar cliente '%s'", name)
                return None

            for client in response:
                if client.get("name", "").strip().lower() == name.lower():
                    logger.info("Cliente '%s' encontrado | id=%s coincidencia exacta", name, client.get("id"))
                    return client

            if response:
                closest = response[0]
                logger.info("Cliente '%s' no encontrado exacto, usando '%s' | id=%s",
                            name, closest.get("name"), closest.get("id"))
                return closest

            logger.warning("Cliente '%s' no encontrado en Alegra", name)
            return None

        except (AlegraConnectionError, AlegraClientError) as e:
            logger.error("Error al buscar cliente '%s': %s", name, e)
            return None

    def create_credit_note(self, invoice_id: str, items: List[Dict],
                           observations: str = "", date: Optional[str] = None) -> Dict:
        assert invoice_id, "invoice_id no puede estar vacío"
        assert isinstance(items, list) and items, "items debe ser una lista no vacía"

        data = {
            "invoice": invoice_id,
            "items": items,
            "observations": observations,
        }
        if date:
            data["date"] = date

        logger.info("Creando nota crédito | factura=%s items=%d", invoice_id, len(items))
        try:
            response = self._request("POST", "credit-notes", data)
            if isinstance(response, dict) and "id" in response:
                logger.info("Nota crédito %s creada | factura=%s", response.get("id"), invoice_id)
            return response
        except (AlegraConnectionError, AlegraClientError) as e:
            logger.error("Error al crear nota crédito para factura %s: %s", invoice_id, e)
            return {"error": str(e), "status": getattr(e, 'status_code', None)}

    def get_credit_notes(self, page: int = 1, limit: int = 30) -> List[Dict]:
        assert page > 0, "page debe ser > 0"
        endpoint = f"credit-notes?page={page}&limit={limit}"

        logger.info("Consultando notas crédito | page=%d limit=%d", page, limit)
        try:
            response = self._request("GET", endpoint)
            if isinstance(response, list):
                return response
            return []
        except (AlegraConnectionError, AlegraClientError):
            return []
