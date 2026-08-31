from core.utils.config import config
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_nvidia import ChatNVIDIA
from langchain_core.language_models.chat_models import BaseChatModel


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


class FallbackLLM:
    """
    Centraliza la creación de modelos LLM con resolución por fallback.

    Siempre intenta los proveedores en este orden estricto:
        1. Groq
        2. Nvidia
        3. OpenRouter

    Si el proveedor correspondiente no tiene API key configurada, avanza al
    siguiente. Si ninguno tiene API key, lanza una excepción ``RuntimeError``.
    """

    def __init__(
        self,
        groq_model: str | None = None,
        nvidia_model: str | None = None,
        openrouter_model: str | None = None,
        temperature: float = 0,
    ) -> None:
        """Configura los nombres de modelo por proveedor.

        Args:
            groq_model: Nombre del modelo para Groq. Usa el default si es None.
            nvidia_model: Nombre del modelo para Nvidia. Usa el default si es None.
            openrouter_model: Nombre del modelo para OpenRouter. Usa el default si es None.
            temperature: Temperatura común aplicada a todos los proveedores.
        """
        self.groq_model = groq_model or DEFAULT_GROQ_MODEL
        self.nvidia_model = nvidia_model or DEFAULT_NVIDIA_MODEL
        self.openrouter_model = openrouter_model or DEFAULT_OPENROUTER_MODEL
        self.temperature = temperature

    def _build_groq(self) -> ChatGroq:
        """Construye la instancia de Groq."""
        return ChatGroq(
            model=self.groq_model,
            temperature=self.temperature,
        )

    def _build_nvidia(self) -> ChatNVIDIA:
        """Construye la instancia de Nvidia."""
        return ChatNVIDIA(
            model=self.nvidia_model,
            api_key=config.NVIDIA_API_KEY,
            temperature=self.temperature,
        )

    def _build_openrouter(self) -> ChatOpenAI:
        """Construye la instancia de OpenRouter vía ChatOpenAI."""
        return ChatOpenAI(
            model=self.openrouter_model,
            api_key=config.OPEN_ROUTER_API_KEY,
            base_url=config.OPEN_ROUTER_BASE_URL,
            temperature=self.temperature,
        )

    def resolve(self) -> BaseChatModel:
        """
        Devuelve la primera instancia de modelo disponible en orden:
        Groq → Nvidia → OpenRouter.

        Raises:
            RuntimeError: Si ninguna API key de LLM está configurada.
        """
        if config.GROQ_API_KEY:
            return self._build_groq()

        if config.NVIDIA_API_KEY:
            return self._build_nvidia()

        if config.OPEN_ROUTER_API_KEY:
            return self._build_openrouter()

        raise RuntimeError(
            "No hay API key de LLM disponible. "
            "Configurar GROQ_API_KEY, NVIDIA_API_KEY o OPEN_ROUTER_API_KEY."
        )

    def resolve_with_tools(self, tools: list) -> BaseChatModel:
        """
        Devuelve el modelo resuelto con las herramientas vinculadas
        (equivalente a ``.bind_tools(tools=tools)``).
        """
        return self.resolve().bind_tools(tools=tools)


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