# Pruebas Unitarias de Clientes de Enriquecimiento — `tests/unit/enrichers/`

[← Volver a tests/unit/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/enrichers/` comprende las pruebas unitarias para los adaptadores de integración con APIs bibliográficas internacionales y el nodo genérico de enriquecimiento (`core/clients/enrichers/` y `core/nodes/enrichment_nodes.py`):
- `CrossrefEnricher`: Resolución de metadatos de publicaciones científicas a partir de DOIs.
- `OpenAlexEnricher`: Búsqueda de literatura científica abierta mediante ISSN o coincidencia de títulos normalizados.
- `OpenLibraryEnricher`: Resolución de metadatos bibliográficos de libros a partir de códigos ISBN.
- `EnrichmentNodeGeneric`: Nodo que orquesta el completado in-place de campos vacíos en el CSV genérico.

Estas pruebas aseguran que los enriquecedores analicen correctamente los payloads JSON de respuesta, manejen fallas y límites de tasa (rate limits) de forma elegante y respeten el contrato de metadatos canónicos del pipeline sin depender de conexiones de red activas.

---

## 🔍 Qué se Evalúa

1. **Resolución de DOIs con Crossref (`test_crossref_enricher.py`):**
   - Normalización de DOIs (eliminación de prefijos `https://doi.org/`, espacios y caracteres espurios).
   - Mapeo de respuestas JSON al esquema interno (títulos, autores en formato estándar, año de publicación, nombre de la revista).
   - Manejo de respuestas HTTP 404 (DOI inexistente) y HTTP 429 (límite de cuota) mediante degradación airosa (*graceful fallback*).
2. **Búsqueda Bibliográfica en OpenAlex (`test_openalex_enricher.py`):**
   - Construcción de filtros de búsqueda por título exacto e ISSN de publicación periódica.
   - Detección de umbrales de confianza para evitar falsos positivos al enriquecer por título.
   - Extracción de datos de acceso abierto (*Open Access URL*, licencia).
3. **Validación y Consulta de ISBN en OpenLibrary (`test_openlibrary_enricher.py`):**
   - Normalización y validación de dígitos de control para ISBN-10 e ISBN-13.
   - Extracción de títulos de monografías, editoriales, lugares de edición y paginación.
4. **Comportamiento del Nodo LangGraph (`test_enrichment_node_generic.py`):**
   - Modificación *in-place* exclusivamente de campos vacíos o nulos en el archivo CSV genérico (no sobrescritura destructiva de metadatos originales provistos por la fuente).
   - Registro de procedencia del metadato (*provenance tracking*).

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar todas las pruebas de enrichers en modo aislado
pytest tests/unit/enrichers/ -v

# Ejecutar únicamente la suite de Crossref
pytest tests/unit/enrichers/test_crossref_enricher.py -v

# Ejecutar excluyendo tests que pudieran tener marcas de integración
pytest tests/unit/enrichers/ -m "not integration" -v
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 en modo unitario (todas las respuestas HTTP son provistas por mocks estáticos).
- **Tiempo Estimado de Ejecución:** < 3 segundos.
- **Costo en Tokens:** **$0.00**.
