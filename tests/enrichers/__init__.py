"""
# Solo unitarios
pytest tests/enrichers/ -v -k "not integration"

# Con integración (APIs reales)
pytest tests/enrichers/ -v -k integration

# Tests parametrizados manuales
pytest tests/enrichers/ -v -k "test_manual"
"""