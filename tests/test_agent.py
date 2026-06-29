import pytest
from src.agent import Z1Agent
from src.alegra_client import AlegraClient
from src.inventory import InventoryManager

def test_z1_init():
    agent = Z1Agent("data/remisiones_ejemplo.xlsx", None, None)
    assert agent.sheet_path == "data/remisiones_ejemplo.xlsx"
    assert agent.PRODUCT_MAP is not None

def test_extract_code():
    agent = Z1Agent("data/remisiones_ejemplo.xlsx", None, None)
    assert agent._extract_code("PTB01 Golden Ale") == "PTB01"
    assert agent._extract_code("IPA") is None
    assert agent._extract_code("") is None
    assert agent._extract_code(None) is None

