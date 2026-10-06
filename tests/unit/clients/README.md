# Pruebas Unitarias de Clientes — `tests/unit/clients/`

[← Volver a tests/unit/](../README.md)

---

## 🎯 Propósito y Finalidad

El directorio `tests/unit/clients/` concentra las pruebas unitarias para los adaptadores de comunicación HTTP y clientes de microservicios de backend (`core/clients/`):
- `BaseClient` (`core/clients/base_client.py`): Cliente base con mecanismos de autenticación y reintento.
- `CrosswalkClient` (`core/clients/crosswalk_client.py`): Cliente del servicio web de crosswalk y mapeo de metadatos.
- `DeduplicatorClient` (`core/clients/deduplicator_client.py`): Cliente del servicio web de detección de duplicados con polling de tareas asíncronas.

Estas pruebas aseguran que los clientes mantengan **resiliencia de red**, gestionen adecuadamente el ciclo de vida de autenticación JWT y cumplan los contratos de interfaz con los microservicios, sin interactuar con servidores reales.

---

## 🔍 Qué se Evalúa

1. **Gestión de Autenticación y Ciclo de Vida de JWT:**
   - Adquisición inicial de token mediante credenciales de servicio.
   - Detección automática de tokens expirados o códigos HTTP 401 Unauthorized y renovación transparente (re-auth).
   - Inyección correcta de cabeceras `Authorization: Bearer <token>`.
2. **Resiliencia HTTP y Estrategia de Reintentos:**
   - Manejo de fallos transitorios de red (`ConnectTimeout`, `ReadTimeout`, `ConnectionError`).
   - Aplicación de backoff exponencial con jitter para evitar tormentas de peticiones.
   - Aislamiento y mapeo de excepciones HTTP 5xx a errores de dominio tipados (`CrosswalkApiError`, `DeduplicatorApiError`).
3. **Mecanismos de Polling Asíncrono:**
   - Verificación del bucle de sondeo para tareas de deduplicación de larga duración (`check_task_status`).
   - Control de límites de tiempo de espera (`timeout`) y cancelación controlada.
4. **Serialización y Deserialización de Payloads:**
   - Envío de archivos en formato `multipart/form-data` (CSVs de entrada y configuraciones JSON).
   - Parseo seguro de respuestas en streaming o binarias (CSVs reconciliados devueltos por el backend).

---

## 🚀 Cómo Ejecutar los Tests

```bash
# Ejecutar la suite de clientes
pytest tests/unit/clients/ -v

# Ejecutar con salida detallada y captura de logs de reintento
pytest tests/unit/clients/ -v -s --log-cli-level=DEBUG
```

---

## 📦 Dependencias y Costos

- **Dependencias de Red:** 0 peticiones de red salientes. Toda llamada HTTP se intercepta mediante `unittest.mock` o librerías de emulación como `responses`.
- **Servicios Externos:** No requiere levantar los contenedores de backend (`deduplicator_crosswalk_web`).
- **Tiempo Estimado:** < 2 segundos.
- **Costo en Tokens:** **$0.00**.
