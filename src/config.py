import os
import sys
import logging
import logging.handlers
from typing import Dict, Optional
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LOG_DIR = Path(os.getenv("Z1_LOG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "logs")))
LOG_LEVEL = os.getenv("Z1_LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = int(os.getenv("Z1_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("Z1_LOG_BACKUP_COUNT", "5"))
DEBUG_MODE = os.getenv("Z1_DEBUG", "").lower() in ("1", "true", "yes", "on")
PROFILE_MODE = os.getenv("Z1_PROFILE", "").lower() in ("1", "true", "yes", "on")

ALEGRA_API_URL = os.getenv("ALEGRA_API_URL", "https://api.alegra.com/api/v1/")
ALEGRA_EMAIL = os.getenv("ALEGRA_EMAIL", "")
ALEGRA_API_KEY = os.getenv("ALEGRA_API_KEY", "")
ALEGRA_TOKEN = os.getenv("ALEGRA_TOKEN", ALEGRA_API_KEY)
MAX_CONCURRENT_ALEGRA = int(os.getenv("MAX_CONCURRENT_ALEGRA", "5"))

GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS_PATH", "credenciales_google.json")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
SHEET_REMISIONES = os.getenv("SHEET_REMISIONES", "")
SHEET_INVENTARIO = os.getenv("SHEET_INVENTARIO", "")
DRIVE_NC_FOLDER = os.getenv("DRIVE_NC_FOLDER", "")

PRODUCT_MAP = {
    "PTB01": {"id": "64", "name": "Golden Ale 330ml", "price": 8500, "style": "GOLDEN ALE"},
    "PTB02": {"id": "65", "name": "Irish Red Ale 330ml", "price": 8500, "style": "IRISH RED ALE"},
    "PTB03": {"id": "66", "name": "APA 330ml", "price": 8500, "style": "APA"},
    "PTB04": {"id": "67", "name": "IPA 330ml", "price": 9500, "style": "IPA"},
    "PTB05": {"id": "68", "name": "Stout 330ml", "price": 8500, "style": "STOUT"},
    "PTL01": {"id": "69", "name": "Golden Ale Litro", "price": 18000, "style": "GOLDEN ALE"},
    "PTL02": {"id": "70", "name": "Irish Red Ale Litro", "price": 18000, "style": "IRISH RED ALE"},
    "PTL03": {"id": "71", "name": "APA Litro", "price": 18000, "style": "APA"},
    "PTL04": {"id": "72", "name": "IPA Litro", "price": 19000, "style": "IPA"},
    "PTL05": {"id": "73", "name": "Stout Litro", "price": 18000, "style": "STOUT"},
    "DOM01": {"id": "58", "name": "Domicilio", "price": 12000, "style": ""},
}

ESTILOS = ["GOLDEN ALE", "IRISH RED ALE", "APA", "IPA", "STOUT"]

ESTILO_DE_PTB = {
    "PTB01": "GOLDEN ALE", "PTB02": "IRISH RED ALE", "PTB03": "APA",
    "PTB04": "IPA", "PTB05": "STOUT",
    "PTL01": "GOLDEN ALE", "PTL02": "IRISH RED ALE", "PTL03": "APA",
    "PTL04": "IPA", "PTL05": "STOUT",
}

ID_PRODUCTO_A_ESTILO = {
    64: ("GOLDEN ALE", "botella"), 65: ("IRISH RED ALE", "botella"),
    66: ("APA", "botella"), 67: ("IPA", "botella"), 68: ("STOUT", "botella"),
    69: ("GOLDEN ALE", "litro"), 70: ("IRISH RED ALE", "litro"),
    71: ("APA", "litro"), 72: ("IPA", "litro"), 73: ("STOUT", "litro"),
}


def setup_logging(name: Optional[str] = None) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name or "z1-agent")
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logger.setLevel(level)

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.handlers.RotatingFileHandler(
            LOG_DIR / "z1-agent.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        error_handler = logging.handlers.RotatingFileHandler(
            LOG_DIR / "z1-agent-error.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)

        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_fmt = logging.Formatter("[%(levelname)s] %(name)s | %(message)s")
        console_handler.setFormatter(console_fmt)
        logger.addHandler(console_handler)

    return logger


assert isinstance(PRODUCT_MAP, dict), "PRODUCT_MAP debe ser un diccionario"
assert len(PRODUCT_MAP) > 0, "PRODUCT_MAP no puede estar vacío"
assert isinstance(ESTILOS, list) and len(ESTILOS) > 0, "ESTILOS debe ser una lista no vacía"


def validate_product_ref(ref: str) -> bool:
    return isinstance(ref, str) and ref.upper() in PRODUCT_MAP


def validate_client_data(datos: Dict) -> None:
    assert datos.get("cliente"), "Falta el nombre del cliente"
    assert isinstance(datos["cliente"], str) and len(datos["cliente"].strip()) > 0, "Nombre del cliente inválido"
    items = datos.get("items")
    assert items, "No hay items en los datos del cliente"
    assert isinstance(items, list), "Items debe ser una lista"


def validate_items_list(items: list) -> None:
    assert items, "La lista de items no puede estar vacía"
    for i, it in enumerate(items):
        assert isinstance(it, dict), f"Item {i} debe ser un diccionario"
        assert "id" in it, f"Item {i} no tiene 'id'"
        assert "quantity" in it, f"Item {i} no tiene 'quantity'"
        assert float(it["quantity"]) > 0, f"Item {i} quantity debe ser > 0"
        assert "price" in it, f"Item {i} no tiene 'price'"
        assert float(it["price"]) >= 0, f"Item {i} price debe ser >= 0"


@contextmanager
def debug_on_error():
    try:
        yield
    except Exception:
        if DEBUG_MODE:
            import pdb
            pdb.post_mortem()
        raise


@contextmanager
def profile_if_enabled(sort_by: str = "cumtime"):
    if PROFILE_MODE:
        import cProfile
        import pstats
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            yield
        finally:
            profiler.disable()
            stats = pstats.Stats(profiler)
            stats.sort_stats(sort_by)
            stats.print_stats(30)
    else:
        yield


def timeit(func):
    import functools
    import time

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not PROFILE_MODE:
            return func(*args, **kwargs)
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        logger = logging.getLogger(func.__module__)
        logger.debug(f"[perf] {func.__name__} tomó {elapsed:.4f}s")
        return result

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        if not PROFILE_MODE:
            return await func(*args, **kwargs)
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        logger = logging.getLogger(func.__module__)
        logger.debug(f"[perf] {func.__name__} tomó {elapsed:.4f}s")
        return result

    import inspect
    if inspect.iscoroutinefunction(func):
        return async_wrapper
    return wrapper


_logger = setup_logging()
_logger.info("Configuración Z1-Agent cargada | LOG_LEVEL=%s | DEBUG=%s | PROFILE=%s", LOG_LEVEL, DEBUG_MODE, PROFILE_MODE)
