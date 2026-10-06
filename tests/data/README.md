# Datos de Prueba y Benchmarks Estáticos — `tests/data/`

[← Volver a tests/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/data/` almacena los archivos estáticos de prueba, conjuntos de datos reales anonimizados, configuraciones de mapeo y tablas de referencia institucionales utilizadas a lo largo de toda la suite de testing.

Para mantener una arquitectura limpia y trazable con el pipeline principal, los datos están estrictamente segmentados según el **subgrafo o dominio funcional** que los consume:

---

## 📂 Organización y Contenido por Subgrafo

```text
tests/data/
├── crosswalk_dedup/               ← Datos para mapeo conceptual, deduplicación y reconciliación
│   ├── configs/                   ← Configuraciones JSON de crosswalk validadas (ej. crosswalk_config_springer.json)
│   ├── dedup_benchmarks/          ← Datasets históricos y casos borde (empty.csv, oaidc_input.csv, result-14531-Romero.csv)
│   ├── generic_inputs/            ← CSVs de prueba ya normalizados al esquema genérico (pdf_ingest_output.csv)
│   ├── repository/                ← Exports representativos del repositorio SEDICI (export_10915_all.csv, sedici_sample.csv)
│   └── source_inputs/             ← Muestras de publicaciones de fuentes externas comerciales (springer_sample.csv)
├── enrichment/                    ← Datasets de prueba para módulos de enriquecimiento bibliográfico
│   └── openalex.csv               ← Registros con DOIs e ISSNs para pruebas de matching
├── export/                        ← Recursos para mapeo SEDICI y generación de SAF
│   ├── licencias-sedici.csv       ← Matriz institucional de equivalencia de licencias Creative Commons
│   └── reconciled_inputs/         ← CSVs reconciliados listos para empaquetado SAF e importación en DSpace
└── ingest/                        ← Datos para pruebas de ingesta y curación
    └── curation/                  ← Datasets de anomalías de OCR (curation_dataset.json)
```

---

## 🛡️ Políticas de Gobernanza y Privacidad

1. **Inmutabilidad de Benchmarks:** Los archivos ubicados en `tests/data/` se consideran inmutables. Modificar un archivo de benchmark altera los resultados históricos de la tesis; cualquier nuevo caso de prueba debe agregarse como un nuevo archivo o registrarse formalmente en el changelog de testing.
2. **Anonimización y Acceso Abierto:** Todos los registros corresponden a artículos de dominio público, revistas de acceso abierto de la UNLP o datos bibliográficos abiertos (OpenAlex / Crossref). No se almacenan datos personales no públicos.
3. **Límite de Tamaño:** Ningún archivo debe superar los 10 MB para evitar inflar el repositorio git de la tesis.

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 (archivos locales versionados).
- **Costo en Tokens:** **$0.00**.
