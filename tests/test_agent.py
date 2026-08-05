import pytest
import tempfile
import os

from src.config import (
    setup_logging, PRODUCT_MAP, ESTILOS,
    validate_client_data, validate_items_list, validate_product_ref,
)
from src.agent import Z1Agent
from src.inventory import InventoryManager, InventoryError
from src.alegra_client import AlegraClientError, AlegraConnectionError


class TestConfig:
    def test_setup_logging_creates_logger(self):
        logger = setup_logging("test-logger")
        assert logger is not None
        assert logger.name == "test-logger"

    def test_product_map_not_empty(self):
        assert len(PRODUCT_MAP) > 0
        assert "PTB01" in PRODUCT_MAP
        assert PRODUCT_MAP["PTB01"]["price"] == 8500

    def test_estilos_not_empty(self):
        assert len(ESTILOS) == 5
        assert "GOLDEN ALE" in ESTILOS

    def test_validate_product_ref_valid(self):
        assert validate_product_ref("PTB01") is True
        assert validate_product_ref("PTL01") is True
        assert validate_product_ref("DOM01") is True

    def test_validate_product_ref_invalid(self):
        assert validate_product_ref("XYZ99") is False
        assert validate_product_ref("") is False
        assert validate_product_ref(None) is False

    def test_validate_items_list_valid(self):
        items = [{"id": "64", "quantity": 5, "price": 8500}]
        validate_items_list(items)

    def test_validate_items_list_empty_raises(self):
        with pytest.raises(AssertionError):
            validate_items_list([])

    def test_validate_items_list_missing_fields_raises(self):
        with pytest.raises(AssertionError):
            validate_items_list([{"id": "64"}])

    def test_validate_items_list_zero_quantity_raises(self):
        with pytest.raises(AssertionError):
            validate_items_list([{"id": "64", "quantity": 0, "price": 8500}])

    def test_validate_client_data_valid(self):
        datos = {"cliente": "Empresa XYZ", "items": [{"id": "64", "quantity": 1, "price": 100}]}
        validate_client_data(datos)

    def test_validate_client_data_empty_raises(self):
        with pytest.raises(AssertionError):
            validate_client_data({"cliente": "", "items": []})


class TestZ1Agent:
    def test_init(self):
        agent = Z1Agent("data/remisiones_ejemplo.xlsx", None, None)
        assert agent.sheet_path == "data/remisiones_ejemplo.xlsx"
        assert agent.PRODUCT_MAP is not None
        assert len(agent.PRODUCT_MAP) > 0

    def test_extract_code_ptb(self):
        agent = Z1Agent("data/test.xlsx", None, None)
        assert agent._extract_code("PTB01 Golden Ale") == "PTB01"
        assert agent._extract_code("Quiero PTB05 por favor") == "PTB05"
        assert agent._extract_code("ptb03") == "PTB03"

    def test_extract_code_ptl(self):
        agent = Z1Agent("data/test.xlsx", None, None)
        assert agent._extract_code("PTL01 Golden Ale Litro") == "PTL01"

    def test_extract_code_dom(self):
        agent = Z1Agent("data/test.xlsx", None, None)
        assert agent._extract_code("DOM01 domicilio") == "DOM01"

    def test_extract_code_invalid(self):
        agent = Z1Agent("data/test.xlsx", None, None)
        assert agent._extract_code("IPA Artesanal") is None
        assert agent._extract_code("") is None
        assert agent._extract_code(None) is None
        assert agent._extract_code(12345) is None


class TestInventoryManager:
    def test_init_creates_file(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = tf.name
        try:
            inv = InventoryManager(path)
            assert os.path.exists(path)
        finally:
            os.unlink(path)

    def test_get_current_stock_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = tf.name
        try:
            inv = InventoryManager(path)
            stock = inv.get_current_stock("GOLDEN ALE")
            assert stock == 0.0
        finally:
            os.unlink(path)

    def test_add_and_get_stock(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = tf.name
        try:
            inv = InventoryManager(path)
            result = inv.add_stock({"GOLDEN ALE": 10.0})
            assert result["GOLDEN ALE"] == 10.0
            stock = inv.get_current_stock("GOLDEN ALE")
            assert stock == 10.0
        finally:
            os.unlink(path)

    def test_deduct_stock_insufficient_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = tf.name
        try:
            inv = InventoryManager(path)
            inv.add_stock({"GOLDEN ALE": 5.0})
            with pytest.raises(AssertionError):
                inv.deduct_stock({"GOLDEN ALE": 10.0})
        finally:
            os.unlink(path)

    def test_deduct_stock_allow_negative(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = tf.name
        try:
            inv = InventoryManager(path)
            inv.add_stock({"GOLDEN ALE": 5.0})
            result = inv.deduct_stock({"GOLDEN ALE": 10.0}, allow_negative=True)
            assert result["GOLDEN ALE"] == 0.0
        finally:
            os.unlink(path)

    def test_get_all_stock(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            path = tf.name
        try:
            inv = InventoryManager(path)
            inv.add_stock({"IPA": 20.0, "APA": 15.0})
            all_stock = inv.get_all_stock()
            assert all_stock["IPA"] == 20.0
            assert all_stock["APA"] == 15.0
        finally:
            os.unlink(path)


class TestAlegraClientErrorTypes:
    def test_connection_error(self):
        err = AlegraConnectionError("Timeout")
        assert str(err) == "Timeout"
        assert isinstance(err, Exception)

    def test_client_error(self):
        err = AlegraClientError("Bad request", status_code=400, response_text="error")
        assert err.status_code == 400
        assert err.response_text == "error"



