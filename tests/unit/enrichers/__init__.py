"""
# Solo unitarios
pytest tests/unit/enrichers/ -v -k "not integration"

# Con integración (APIs reales)
pytest tests/unit/enrichers/ -v -k integration

# Tests parametrizados manuales
pytest tests/unit/enrichers/ -v -k "test_manual"
"""