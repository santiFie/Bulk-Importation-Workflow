import asyncio
import concurrent.futures
import contextlib
import logging
from typing import Any

from core.utils.config import config
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_nvidia import ChatNVIDIA
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_TIMEOUT_SEGUNDOS = 60.0


def _get_local_model() -> ChatOpenAI:
    import httpx

    orchestrator_api_key = config.ORCHESTRATOR_LOCAL_API_KEY

    if not orchestrator_api_key:
        raise RuntimeError("Missing ORCHESTRATOR_API_KEY_LOCAL environment variable")

    auth_headers = {"X-API-Key": orchestrator_api_key}
    timeout = 1800.0

    # HTTP sync client
    sync_httpx_client = httpx.Client(headers=auth_headers, timeout=timeout)

    # HTTP async client
    async_httpx_client = httpx.AsyncClient(headers=auth_headers, timeout=timeout)

    model = ChatOpenAI(
        model="qwen3:30b",
        temperature=0,
        base_url=config.ORCHESTRATOR_BASE_URL_LOCAL,
        api_key="dummy",
        http_client=sync_httpx_client,                  # used by .invoke()
        http_async_client=async_httpx_client,           # used by .ainvoke()
    )

    return model

def get_local_model_with_tools(tools):
    model = get_local_model()
    model.bind_tools(tools=tools)

    return model

# Modelos por defecto por proveedor cuando no se especifica ninguno.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_NVIDIA_MODEL = "openai/gpt-oss-120b"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-20b"


def _extraer_nombre_modelo(provider: Any) -> str:
    """
    Extrae el nombre del modelo de un proveedor LLM, manejando wrappers
    (p. ej. ``_ChatModelBinding`` generado por ``bind_tools`` que expone el
    nombre vía ``model_name`` en lugar de ``model``).
    """
    for attr in ("model", "model_name"):
        valor = getattr(provider, attr, None)
        if valor:
            return str(valor)
    return "?"


def _es_error_429(exc: BaseException) -> bool:
    """
    Determina si la excepción corresponde a un error de rate limit (HTTP 429).

    Detecta:
      - Excepciones cuyo mensaje contiene '429', 'rate limit', 'too many requests'
        o 'ratelimit'.
      - httpx.HTTPStatusError y similares que expongan ``.response.status_code``.
    """
    msg = str(exc).lower()
    if any(kw in msg for kw in ("rate limit", "429", "too many requests", "ratelimit")):
        return True
    with contextlib.suppress(AttributeError):
        if exc.response.status_code == 429:  # type: ignore[union-attr]
            return True
    return False


class FallbackModelWrapper:
    """
    Envuelve un proveedor LLM concreto para exponer las operaciones de un
    ``BaseChatModel`` (invoke/ainvoke) manteniendo la opción de rebind de
    herramientas. Es la pieza reutilizada por cada eslabón de la cadena.
    """

    def __init__(self, provider: BaseChatModel) -> None:
        self._provider = provider

    def _invoke_provider(self, input: Any, **kwargs: Any) -> Any:
        return self._provider.invoke(input, **kwargs)

    async def _ainvoke_provider(self, input: Any, **kwargs: Any) -> Any:
        return await self._provider.ainvoke(input, **kwargs)

    def bind_tools(self, tools: list, **kwargs: Any) -> "FallbackModelWrapper":
        self._provider = self._provider.bind_tools(tools=tools, **kwargs)
        return self


class FallbackLLM:
    """
    LLM con fallback en cascada para errores 429 (rate limit) y timeouts.

    Orden de proveedores:
      1. Groq       → DEFAULT_GROQ_MODEL
      2. NVIDIA     → DEFAULT_NVIDIA_MODEL
      3. OpenRouter → DEFAULT_OPENROUTER_MODEL

    Por cada llamada se intenta cada proveedor en orden. Si se recibe un error
    429 o un timeout (60 s) se pasa inmediatamente al siguiente sin backoff.
    Si todos los proveedores fallan, se propaga el último error.
    """

    def __init__(
        self,
        groq_model: str | None = None,
        nvidia_model: str | None = None,
        openrouter_model: str | None = None,
        temperature: float = 0,
        timeout: float = _TIMEOUT_SEGUNDOS,
    ) -> None:
        """Configura los nombres de modelo por proveedor y construye la cadena.

        Args:
            groq_model: Nombre del modelo para Groq. Usa el default si es None.
            nvidia_model: Nombre del modelo para Nvidia. Usa el default si es None.
            openrouter_model: Nombre del modelo para OpenRouter. Usa el default si es None.
            temperature: Temperatura común aplicada a todos los proveedores.
            timeout: Tiempo máximo (segundos) por llamada a cada proveedor.
        """
        self.groq_model = groq_model or DEFAULT_GROQ_MODEL
        self.nvidia_model = nvidia_model or DEFAULT_NVIDIA_MODEL
        self.openrouter_model = openrouter_model or DEFAULT_OPENROUTER_MODEL
        self.temperature = temperature
        self._timeout = timeout
        self._proveedores = self._construir_cadena()

    def _construir_cadena(self) -> list[FallbackModelWrapper]:
        """Construye la cadena de proveedores en orden de prioridad, omitiendo
        los que no tienen API key configurada."""
        proveedores: list[FallbackModelWrapper] = []

        if config.GROQ_API_KEY:
            proveedores.append(
                FallbackModelWrapper(
                    ChatGroq(
                        model=self.groq_model,
                        temperature=self.temperature,
                        max_retries=0,
                    )
                )
            )

        if config.NVIDIA_API_KEY:
            proveedores.append(
                FallbackModelWrapper(
                    ChatNVIDIA(
                        model=self.nvidia_model,
                        api_key=config.NVIDIA_API_KEY,
                        temperature=self.temperature,
                    )
                )
            )

        if config.OPEN_ROUTER_API_KEY:
            proveedores.append(
                FallbackModelWrapper(
                    ChatOpenAI(
                        model=self.openrouter_model,
                        api_key=config.OPEN_ROUTER_API_KEY,
                        base_url=config.OPEN_ROUTER_BASE_URL,
                        temperature=self.temperature,
                    )
                )
            )

        if not proveedores:
            raise RuntimeError(
                "No hay API key de LLM disponible. "
                "Configurar GROQ_API_KEY, NVIDIA_API_KEY o OPEN_ROUTER_API_KEY."
            )

        cadena = " → ".join(
            f"{type(p._provider).__name__}(modelo={_extraer_nombre_modelo(p._provider)})"
            for p in proveedores
        )
        logger.info("Cadena de fallback LLM construida: %s", cadena)
        return proveedores

    def resolve(self) -> "FallbackLLM":
        """Devuelve la instancia de fallback (compatibilidad con la API previa)."""
        return self

    def resolve_with_tools(self, tools: list) -> "FallbackLLM":
        """Vincula herramientas a todos los proveedores de la cadena."""
        self.bind_tools(tools)
        return self

    def bind_tools(self, tools: list, **kwargs: Any) -> "FallbackLLM":
        """Vincula herramientas a cada proveedor de la cadena."""
        for prov in self._proveedores:
            prov.bind_tools(tools, **kwargs)
        return self

    def with_structured_output(
        self, schema: Any, **kwargs: Any
    ) -> "FallbackStructuredOutput":
        """Devuelve un wrapper de salida estructurada con fallback en cascada."""
        return FallbackStructuredOutput(self, schema, **kwargs)

    def _intentar(self, metodo: str, input: Any, **kwargs: Any) -> Any:
        """Prueba cada proveedor en orden (sync). Sin backoff entre intentos."""
        ultimo_error: BaseException = RuntimeError("No hay proveedores disponibles.")
        for prov in self._proveedores:
            try:
                fn = getattr(prov, metodo)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    futuro = pool.submit(fn, input, **kwargs)
                    respuesta = futuro.result(timeout=self._timeout)
                logger.info(
                    "LLM ok | proveedor=%s | modelo=%s",
                    type(prov._provider).__name__,
                    _extraer_nombre_modelo(prov._provider),
                )
                return respuesta
            except (concurrent.futures.TimeoutError, TimeoutError) as exc:
                ultimo_error = exc
                logger.warning(
                    "Timeout (%ds) en %s(modelo=%s) — pasando al siguiente proveedor.",
                    self._timeout,
                    type(prov._provider).__name__,
                    _extraer_nombre_modelo(prov._provider),
                )
            except Exception as exc:
                if _es_error_429(exc):
                    ultimo_error = exc
                    logger.warning(
                        "Rate limit (429) en %s(modelo=%s) — pasando al siguiente proveedor.",
                        type(prov._provider).__name__,
                        _extraer_nombre_modelo(prov._provider),
                    )
                else:
                    raise
        logger.error("Todos los proveedores fallaron. Último error: %s", ultimo_error)
        raise RuntimeError("Todos los proveedores fallaron en la llamada LLM.") from ultimo_error

    async def _intentar_async(self, metodo: str, input: Any, **kwargs: Any) -> Any:
        """Prueba cada proveedor en orden (async). Sin backoff entre intentos."""
        ultimo_error: BaseException = RuntimeError("No hay proveedores disponibles.")
        for prov in self._proveedores:
            try:
                fn = getattr(prov, metodo)
                respuesta = await asyncio.wait_for(
                    fn(input, **kwargs), timeout=self._timeout
                )
                logger.info(
                    "LLM ok | proveedor=%s | modelo=%s",
                    type(prov._provider).__name__,
                    _extraer_nombre_modelo(prov._provider),
                )
                return respuesta
            except asyncio.TimeoutError as exc:
                ultimo_error = exc
                logger.warning(
                    "Timeout async (%ds) en %s(modelo=%s) — pasando al siguiente.",
                    self._timeout,
                    type(prov._provider).__name__,
                    _extraer_nombre_modelo(prov._provider),
                )
            except Exception as exc:
                if _es_error_429(exc):
                    ultimo_error = exc
                    logger.warning(
                        "Rate limit (429) async en %s(modelo=%s) — pasando al siguiente.",
                        type(prov._provider).__name__,
                        _extraer_nombre_modelo(prov._provider),
                    )
                else:
                    raise
        logger.error(
            "Todos los proveedores fallaron (async). Último error: %s", ultimo_error
        )
        raise RuntimeError("Todos los proveedores fallaron (async) en la llamada LLM.") from ultimo_error

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """Invoca el LLM con fallback en cascada (sync)."""
        return self._intentar("_invoke_provider", input, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """Invoca el LLM con fallback en cascada (async)."""
        return await self._intentar_async("_ainvoke_provider", input, **kwargs)


class FallbackStructuredOutput:
    """
    Envuelve la salida estructurada (``with_structured_output``) de un
    ``FallbackLLM`` para mantener el fallback en cascada.
    """

    def __init__(
        self, fallback: FallbackLLM, schema: Any, **bind_kwargs: Any
    ) -> None:
        self._fallback = fallback
        self._schema = schema
        self._bind_kwargs = bind_kwargs

        self._wrappers: list[FallbackModelWrapper] = []
        for prov in fallback._proveedores:
            wrapped = FallbackModelWrapper(prov._provider.with_structured_output(schema, **bind_kwargs))
            self._wrappers.append(wrapped)

    def _intentar(self, metodo: str, input: Any, **kwargs: Any) -> Any:
        ultimo_error: BaseException = RuntimeError("No hay proveedores disponibles.")
        for prov in self._wrappers:
            try:
                fn = getattr(prov, metodo)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    futuro = pool.submit(fn, input, **kwargs)
                    respuesta = futuro.result(timeout=self._fallback._timeout)
                return respuesta
            except (concurrent.futures.TimeoutError, TimeoutError) as exc:
                ultimo_error = exc
            except Exception as exc:
                if _es_error_429(exc):
                    ultimo_error = exc
                else:
                    raise
        raise RuntimeError(
            "Todos los proveedores fallaron en la salida estructurada."
        ) from ultimo_error

    async def _intentar_async(self, metodo: str, input: Any, **kwargs: Any) -> Any:
        ultimo_error: BaseException = RuntimeError("No hay proveedores disponibles.")
        for prov in self._wrappers:
            try:
                fn = getattr(prov, metodo)
                respuesta = await asyncio.wait_for(
                    fn(input, **kwargs), timeout=self._fallback._timeout
                )
                return respuesta
            except asyncio.TimeoutError as exc:
                ultimo_error = exc
            except Exception as exc:
                if _es_error_429(exc):
                    ultimo_error = exc
                else:
                    raise
        raise RuntimeError(
            "Todos los proveedores fallaron (async) en la salida estructurada."
        ) from ultimo_error

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._intentar("_invoke_provider", input, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return await self._intentar_async("_ainvoke_provider", input, **kwargs)


def get_model(use_local = False, provider="groq", model="llama-3.1-8b-instant"):
  """Returns the appropriate model instance based on the USE_LOCAL_MODEL flag."""
  if use_local:
      model = _get_local_model()
  else:
      if provider == "groq":
          model = ChatGroq(
            model=model,
            temperature=0,
          )
      elif provider == "openrouter":
        model = ChatOpenAI(
            model=model,
            api_key=config.OPEN_ROUTER_API_KEY,
            base_url=config.OPEN_ROUTER_BASE_URL,
            temperature=0,
        )
      elif provider == "nvidia":
        model=ChatNVIDIA(
          model=model,
          api_key=config.NVIDIA_API_KEY,
          temperature=0.01,
        )
      else:
          raise ValueError(f"Unknown provider: {provider}")
  return model