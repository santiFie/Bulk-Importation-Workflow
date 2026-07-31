"""
RAG context retriever for the Planner — local API backend.

Implementa los mismos métodos que ``rag_context.py`` pero delegando la
búsqueda semántica al orquestador RAG local (``ORCHESTRATOR_BASE_URL``)
en lugar de usar FAISS + HuggingFace localmente.

Flujo de autenticación
----------------------
1. POST ``/auth/login`` con ``email`` + ``password``  →  recibe JWT Bearer token.
2. POST ``/rag/search`` con ``Authorization: Bearer <token>``  →  resultados.

El JWT se cachea por instancia de ``LocalRagRetriever`` para reutilizarlo
durante el ciclo de vida del proceso.  Si el token expira (respuesta 401) se
solicita uno nuevo de forma transparente.

Estructura de la respuesta del endpoint /rag/search
----------------------------------------------------
{
  "pgvector": {
    "hits": [
      {
        "chunk_id": str,
        "source_filename": str,
        "chunk_index": int,
        "content": str,
        "score": float,
        "embedding": null | list[float]
      }
    ]
  },
  "milvus": { "hits": [...] },
  "collection_display_name": str,
  ...
}

Uso rápido
----------
    from core.utils.local_rag_context import retrieve_planner_context_local
    context = await retrieve_planner_context_local("¿Qué agentes hay disponibles?")
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

from core.utils.config import config

logger = logging.getLogger(__name__)

# ─── Configuración de endpoints ───────────────────────────────────────────────
_RAG_SEARCH_PATH = "/rag/search"
_AUTH_LOGIN_PATH = "/auth/login"

# ─── Parámetros de embedding / colección ──────────────────────────────────────
EMBEDDING_MODEL: str = "mxbai-embed-large:latest"
DEFAULT_COLLECTION_SLUG: str | None = "Markdown Agentes"
DEFAULT_COLLECTION_ID: uuid.UUID | None = None

# ─── Parámetros de recuperación ───────────────────────────────────────────────
AGENT_TOP_K: int = 4      # resultados a solicitar al índice de agentes
PLAYBOOK_TOP_K: int = 3   # resultados a solicitar al índice de playbooks
TARGET: str = "pgvector"      # "pgvector" | "milvus" | "both"

# ─── Credenciales ─────────────────────────────────────────────────────────────
_LOGIN_EMAIL: str = "santifierro@gmail.com"



# ══════════════════════════════════════════════════════════════════════════════
#  Cliente HTTP con autenticación JWT
# ══════════════════════════════════════════════════════════════════════════════

class _JwtSession:
    """
    Wrapper sobre ``httpx.AsyncClient`` que gestiona el token JWT de forma
    transparente: lo solicita al crear la sesión y lo renueva si expira (401).
    """

    def __init__(self, base_url: str, email: str, password: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._timeout = timeout
        self._token: str | None = None

    async def _fetch_token(self, client: httpx.AsyncClient) -> str | None:
        """POST /auth/login → JWT Bearer token."""
        url = f"{self._base_url}{_AUTH_LOGIN_PATH}"
        payload = {"email": self._email, "password": self._password}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token") or data.get("token")
                if token:
                    logger.debug("JWT token obtenido exitosamente.")
                    return token
                logger.error("Login 200 pero sin 'access_token'/'token' en respuesta: %s", data)
            else:
                logger.error("Error al obtener JWT. Status: %d — %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("Excepción al solicitar JWT: %s", exc, exc_info=True)
        return None

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        retry_on_401: bool = True,
    ) -> httpx.Response | None:
        """POST autenticado con JWT; renueva el token si recibe 401."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if self._token is None:
                self._token = await self._fetch_token(client)
                if self._token is None:
                    return None

            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            url = f"{self._base_url}{path}"
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 401 and retry_on_401:
                    logger.warning("JWT expirado (401). Renovando token...")
                    self._token = await self._fetch_token(client)
                    if self._token is None:
                        return None
                    headers["Authorization"] = f"Bearer {self._token}"
                    resp = await client.post(url, json=payload, headers=headers)
                return resp
            except httpx.ConnectError as exc:
                logger.error("No se pudo conectar a %s: %s", url, exc)
            except httpx.TimeoutException:
                logger.error("Timeout al conectar con %s (%.1fs)", url, self._timeout)
            except Exception as exc:
                logger.error("Error inesperado en POST %s: %s", url, exc, exc_info=True)
        return None


# ─── Sesión singleton (reutiliza el token durante el proceso) ─────────────────
_session: _JwtSession | None = None


def _get_session() -> _JwtSession:
    """Devuelve (o construye) la sesión JWT singleton para la API local."""
    global _session
    if _session is None:
        base_url = config.ORCHESTRATOR_BASE_URL.rstrip("/")
        password = config.ORCHESTRATOR_PASSWORD
        if not base_url:
            raise RuntimeError(
                "ORCHESTRATOR_BASE_URL no está configurado en .env. "
                "Ejemplo: ORCHESTRATOR_BASE_URL=http://192.168.2.114:4001/v1"
            )
        if not password:
            raise RuntimeError(
                "ORCHESTRATOR_PASSWORD no está configurado en .env. "
                "Es necesario para obtener el JWT de la API local."
            )
        _session = _JwtSession(base_url, _LOGIN_EMAIL, password)
    return _session


# ══════════════════════════════════════════════════════════════════════════════
#  Función de búsqueda RAG
# ══════════════════════════════════════════════════════════════════════════════

async def _rag_search(
    query: str,
    *,
    top_k: int = 5,
    collection_id: uuid.UUID | None = DEFAULT_COLLECTION_ID,
    collection_slug: str | None = DEFAULT_COLLECTION_SLUG,
    model: str = EMBEDDING_MODEL,
    target: str = TARGET,
    include_embedding: bool = False,
) -> dict[str, Any] | None:
    """
    Ejecuta una búsqueda RAG contra la API local y devuelve el JSON de
    respuesta tal cual lo envía el servidor, o ``None`` si ocurre algún error.

    Parámetros
    ----------
    query:
        Texto de la consulta.
    top_k:
        Número máximo de resultados a devolver.
    collection_id:
        UUID de la colección (tiene prioridad sobre ``collection_slug``).
    collection_slug:
        Slug legible de la colección.
    model:
        Nombre del modelo de embedding registrado en el orquestador.
    target:
        Backend a consultar: ``"pgvector"``, ``"milvus"`` o ``"both"``.
    include_embedding:
        Si ``True``, el servidor devuelve el vector de la consulta.
    """
    payload: dict[str, Any] = {
        "query": query,
        "top_k": top_k,
        "model": model,
        "target": target,
        "include_embedding": include_embedding,
    }

    if collection_id is not None:
        payload["collection_id"] = str(collection_id)
    elif collection_slug is not None:
        payload["collection_slug"] = collection_slug

    session = _get_session()
    resp = await session.post(_RAG_SEARCH_PATH, payload)
    if resp is None:
        return None

    if resp.status_code == 200:
        return resp.json()

    logger.error(
        "RAG search falló con status %d: %s",
        resp.status_code,
        resp.text[:300],
    )
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Formateo de resultados
# ══════════════════════════════════════════════════════════════════════════════

def _extract_hits(body: dict[str, Any], target: str) -> list[dict[str, Any]]:
    """
    Extrae la lista de hits del cuerpo de respuesta según el ``target``
    especificado.

    Para ``target="both"`` fusiona los hits de pgvector y milvus
    deduplicando por ``chunk_id`` y priorizando el mayor ``score``.
    """
    if target == "pgvector":
        return body.get("pgvector", {}).get("hits", [])
    if target == "milvus":
        return body.get("milvus", {}).get("hits", [])

    # "both": merge + deduplication por chunk_id (mayor score gana)
    seen: dict[str, dict[str, Any]] = {}
    for backend in ("pgvector", "milvus"):
        for hit in body.get(backend, {}).get("hits", []):
            cid = hit.get("chunk_id", "")
            if cid not in seen or hit.get("score", 0) > seen[cid].get("score", 0):
                seen[cid] = hit

    # Ordenar de mayor a menor score
    return sorted(seen.values(), key=lambda h: h.get("score", 0), reverse=True)


def _format_hits(hits: list[dict[str, Any]], collection_name: str) -> list[str]:
    """
    Formatea hits como secciones Markdown con fuente y puntuación.

    Parámetros
    ----------
    hits:
        Lista de objetos hit devueltos por la API.
    collection_name:
        Nombre de la colección (usado como etiqueta de fuente).
    """
    sections: list[str] = []
    for hit in hits:
        source = hit.get("source_filename", "unknown")
        chunk_idx = hit.get("chunk_index", "?")
        score = hit.get("score", 0.0)
        content = hit.get("content", "").strip()
        label = f"[{collection_name} / {source} chunk={chunk_idx}] score={score:.4f}"
        sections.append(f"### {label}\n{content}")
    return sections


# ══════════════════════════════════════════════════════════════════════════════
#  Interfaz pública — equivalente a retrieve_planner_context de rag_context.py
# ══════════════════════════════════════════════════════════════════════════════

async def retrieve_planner_context_local(
    query: str,
    agent_k: int = AGENT_TOP_K,
    playbook_k: int = PLAYBOOK_TOP_K,
    *,
    collection_id: uuid.UUID | None = DEFAULT_COLLECTION_ID,
    collection_slug: str | None = DEFAULT_COLLECTION_SLUG,
    model: str = EMBEDDING_MODEL,
    target: str = TARGET,
) -> str:
    """
    Equivalente asíncrono de ``retrieve_planner_context()`` que utiliza la
    API RAG local en lugar de FAISS local.

    Realiza **una sola llamada** a la API con ``top_k = agent_k + playbook_k``
    y distribuye los resultados como «contexto combinado» en el mismo formato
    que produce el retriever FAISS.

    Parámetros
    ----------
    query:
        Consulta del Planner.
    agent_k:
        Número de chunks reservados para contexto de agentes.
    playbook_k:
        Número de chunks reservados para contexto de playbooks.
    collection_id:
        UUID de la colección RAG a consultar.
    collection_slug:
        Slug de la colección RAG (usado si no se provee ``collection_id``).
    model:
        Modelo de embedding registrado en el orquestador.
    target:
        Backend de vectores: ``"pgvector"``, ``"milvus"`` o ``"both"``.

    Retorna
    -------
    str
        Bloque Markdown listo para inyectar en el prompt del Planner, o
        cadena vacía si no se recuperó ningún contexto.
    """
    top_k = agent_k + playbook_k

    try:
        body = await _rag_search(
            query,
            top_k=top_k,
            collection_id=collection_id,
            collection_slug=collection_slug,
            model=model,
            target=target,
            include_embedding=False,
        )
    except Exception as exc:
        logger.error("retrieve_planner_context_local falló: %s", exc, exc_info=True)
        return ""

    if not body:
        logger.warning("No se recibió respuesta válida del RAG local para: %s", query[:80])
        return ""

    collection_name = body.get("collection_display_name") or collection_slug or "RAG"
    hits = _extract_hits(body, target)

    if not hits:
        logger.warning("No se encontraron hits en el RAG local para: %s", query[:80])
        return ""

    sections = _format_hits(hits, collection_name)

    parts: list[str] = ["## Relevant Context (retrieved from local RAG API)\n"]
    parts.append(
        f"### 🤖 Agent & Playbook Context *(collection: {collection_name})*\n\n"
        + "\n\n".join(sections)
    )

    return "\n\n".join(parts) + "\n"


def retrieve_planner_context_local_sync(
    query: str,
    agent_k: int = AGENT_TOP_K,
    playbook_k: int = PLAYBOOK_TOP_K,
    **kwargs: Any,
) -> str:
    """
    Versión síncrona de ``retrieve_planner_context_local``.

    Útil para integraciones que no corren en un event-loop asíncrono.
    Internamente crea o reutiliza un loop para ejecutar la corutina.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Estamos dentro de un loop (ej. Jupyter / LangGraph async)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    retrieve_planner_context_local(query, agent_k, playbook_k, **kwargs),
                )
                return future.result()
        else:
            return loop.run_until_complete(
                retrieve_planner_context_local(query, agent_k, playbook_k, **kwargs)
            )
    except Exception as exc:
        logger.error("retrieve_planner_context_local_sync falló: %s", exc, exc_info=True)
        return ""


# ══════════════════════════════════════════════════════════════════════════════
#  CLI rápido para pruebas manuales
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import sys
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[_logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(
        description="Ejecutar el Local RAG Context Retriever desde la terminal."
    )
    parser.add_argument("query", type=str, help="Consulta para el Planner.")
    parser.add_argument(
        "--agent_k", type=int, default=AGENT_TOP_K,
        help=f"Chunks de agentes a recuperar (por defecto: {AGENT_TOP_K})",
    )
    parser.add_argument(
        "--playbook_k", type=int, default=PLAYBOOK_TOP_K,
        help=f"Chunks de playbooks a recuperar (por defecto: {PLAYBOOK_TOP_K})",
    )
    parser.add_argument(
        "--target", type=str, default=TARGET,
        choices=["pgvector", "milvus", "both"],
        help=f"Backend de vectores (por defecto: {TARGET})",
    )
    parser.add_argument(
        "--collection", type=str, default=DEFAULT_COLLECTION_SLUG,
        help=f"Slug de la colección RAG (por defecto: '{DEFAULT_COLLECTION_SLUG}')",
    )

    args = parser.parse_args()

    print("\n" + "=" * 50)
    print(f"🔍 Consulta : '{args.query}'")
    print(f"📊 Config   → agent_k={args.agent_k} | playbook_k={args.playbook_k} | target={args.target}")
    print(f"📁 Colección: {args.collection}")
    print("=" * 50 + "\n")

    result = asyncio.run(
        retrieve_planner_context_local(
            args.query,
            agent_k=args.agent_k,
            playbook_k=args.playbook_k,
            collection_slug=args.collection,
            target=args.target,
        )
    )

    if result:
        print(result)
    else:
        print("⚠️  No se encontró contexto relevante en la API RAG local.")
