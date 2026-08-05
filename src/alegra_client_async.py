import asyncio
import os
from typing import Dict, List, Optional
from urllib.parse import quote
import logging

import aiohttp
from dotenv import load_dotenv
from .config import ALEGRA_API_URL, ALEGRA_EMAIL, ALEGRA_API_KEY, MAX_CONCURRENT_ALEGRA, debug_on_error

load_dotenv()

logger = logging.getLogger(__name__)


class AlegraClientAsyncError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class AlegraClientAsync:
    def __init__(self, api_key=None, email=None, base_url=None, max_concurrent=None):
        self.base_url = base_url or ALEGRA_API_URL
        email_val = email or ALEGRA_EMAIL
        api_key_val = api_key or ALEGRA_API_KEY

        assert email_val, "ALEGRA_EMAIL no configurado"
        assert api_key_val, "ALEGRA_API_KEY no configurado"
        assert self.base_url, "ALEGRA_API_URL no configurada"

        self.auth = aiohttp.BasicAuth(email_val, api_key_val)
        max_conc = max_concurrent if max_concurrent is not None else MAX_CONCURRENT_ALEGRA
        assert max_conc > 0, "max_concurrent debe ser > 0"

        self._semaphore = asyncio.Semaphore(max_conc)
        self._session: Optional[aiohttp.ClientSession] = None
        logger.info("AlegraClientAsync inicializado | max_concurrent=%d", max_conc)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=10)
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                auth=self.auth,
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": "Z1-Agent/2.0 (Madre Monte Async)"},
            )
        return self._session

    async def _request(self, method: str, endpoint: str,
                       data: Optional[Dict] = None,
                       retries: int = 3) -> Dict:
        assert method in ("GET", "POST", "PUT", "DELETE", "PATCH"), f"Método HTTP inválido: {method}"
        assert endpoint, "endpoint no puede estar vacío"

        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        async with self._semaphore:
            for attempt in range(retries):
                try:
                    async with session.request(method, url, json=data) as resp:
                        if resp.status == 429:
                            wait = 2 ** attempt
                            logger.warning("Rate limit (429) en %s %s | esperando %ds (intento %d/%d)",
                                           method, endpoint, wait, attempt + 1, retries)
                            await asyncio.sleep(wait)
                            continue

                        if resp.status >= 500 and attempt < retries - 1:
                            wait = attempt + 1
                            logger.warning("Error servidor %d en %s %s | reintento %d/%d en %ds",
                                           resp.status, method, endpoint, attempt + 1, retries, wait)
                            await asyncio.sleep(wait)
                            continue

                        resp.raise_for_status()
                        result = await resp.json()

                        if attempt > 0:
                            logger.info("Request exitoso en intento %d: %s %s", attempt + 1, method, endpoint)

                        return result

                except aiohttp.ClientResponseError as e:
                    if e.status == 429 and attempt < retries - 1:
                        wait = 2 ** attempt
                        logger.warning("Rate limit (429) — esperando %ds", wait)
                        await asyncio.sleep(wait)
                        continue
                    logger.error("Alegra HTTP %s en %s %s", e.status, method, endpoint)
                    if attempt < retries - 1 and e.status >= 500:
                        await asyncio.sleep(attempt + 1)
                        continue
                    raise AlegraClientAsyncError(f"HTTP {e.status} en {method} {endpoint}", status_code=e.status) from e

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt < retries - 1:
                        wait = attempt + 1
                        logger.warning("Reintento %d/%d para %s %s: %s", attempt + 1, retries, method, endpoint, e)
                        await asyncio.sleep(wait)
                        continue
                    logger.error("Error en %s %s tras %d intentos: %s", method, endpoint, retries, e)
                    raise AlegraClientAsyncError(str(e)) from e

        return {}

    async def get_invoices(self, page: int = 1, limit: int = 30) -> List[Dict]:
        assert page > 0, "page debe ser > 0"
        endpoint = f"invoices?page={page}&limit={limit}"
        try:
            response = await self._request("GET", endpoint)
            result = response if isinstance(response, list) else []
            logger.info("Facturas async: %d resultados | page=%d", len(result), page)
            return result
        except AlegraClientAsyncError as e:
            logger.error("Error obteniendo facturas: %s", e)
            return []

    async def get_invoice(self, invoice_id: str) -> Optional[Dict]:
        assert invoice_id, "invoice_id no puede estar vacío"
        try:
            response = await self._request("GET", f"invoices/{invoice_id}")
            if isinstance(response, dict) and "id" in response:
                logger.info("Factura %s encontrada (async)", invoice_id)
                return response
            return None
        except AlegraClientAsyncError as e:
            logger.error("Error obteniendo factura %s: %s", invoice_id, e)
            return None

    async def create_invoice(self, client_id: str, items: List[Dict],
                             due_date: str, date: Optional[str] = None) -> Dict:
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
        if date:
            data["date"] = date

        logger.info("Creando factura async | cliente=%s items=%d", client_id, len(items))
        with debug_on_error():
            result = await self._request("POST", "invoices", data)
            if result.get("id"):
                logger.info("Factura async %s creada | cliente=%s", result.get("id"), client_id)
            return result

    async def get_clients(self, page: int = 1, limit: int = 30) -> List[Dict]:
        assert page > 0, "page debe ser > 0"
        endpoint = f"contacts?page={page}&limit={limit}"
        try:
            response = await self._request("GET", endpoint)
            result = response if isinstance(response, list) else []
            logger.info("Clientes async: %d resultados", len(result))
            return result
        except AlegraClientAsyncError:
            return []

    async def search_client_by_name(self, name: str) -> Optional[Dict]:
        assert name and name.strip(), "nombre de cliente no puede estar vacío"
        name = name.strip()

        logger.debug("Buscando cliente async: '%s'", name)
        endpoint = f"contacts?name={quote(name)}&limit=10"
        try:
            response = await self._request("GET", endpoint)
            if isinstance(response, list):
                for client in response:
                    if client.get("name", "").strip().lower() == name.lower():
                        logger.info("Cliente '%s' encontrado (async) | id=%s", name, client.get("id"))
                        return client
                if response:
                    closest = response[0]
                    logger.info("Cliente '%s' → '%s' (async) | id=%s", name, closest.get("name"), closest.get("id"))
                    return closest
            logger.warning("Cliente '%s' no encontrado (async)", name)
            return None
        except AlegraClientAsyncError as e:
            logger.error("Error buscando cliente '%s': %s", name, e)
            return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.debug("Sesión HTTP async cerrada")
