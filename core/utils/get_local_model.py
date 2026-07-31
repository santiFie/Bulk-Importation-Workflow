from core.utils.config import config
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq
from langchain_nvidia import ChatNVIDIA


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