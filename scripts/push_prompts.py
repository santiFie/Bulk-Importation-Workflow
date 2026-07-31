"""
Publica los prompts locales (agent_prompts/*.md) hacia LangSmith Hub.

Los archivos .md en Git son siempre la fuente de verdad.
LangSmith Hub actúa únicamente como espejo para aprovechar el playground,
el versionado visual y el registro de experimentos.

Uso:
    # Publicar todos los prompts
    python scripts/push_prompts.py

    # Publicar un prompt específico
    python scripts/push_prompts.py --prompt crosswalk_agent

    # Ver qué se publicaría sin ejecutar (dry-run)
    python scripts/push_prompts.py --dry-run

Requisitos:
    - LANGCHAIN_API_KEY en el entorno o en .env
    - LANGSMITH_HUB_ORG en el .env:
        · Workspace personal → dejar vacío o escribir "personal"
        · Organización propia → escribir el handle exacto (ej. "mi-org")
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Asegura que el proyecto esté en el path para poder importar dotenv, etc.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

PROMPTS_DIR = _PROJECT_ROOT / "agent_prompts"


# Valor centinela que indica workspace personal (sin prefijo de org)
_PERSONAL_SENTINEL = {"personal", ""}


def _get_org() -> str | None:
    """
    Lee el handle de organización de LangSmith Hub desde LANGSMITH_HUB_ORG.

    Retorna:
        None  → workspace personal (los prompts se publican sin prefijo de org).
        str   → handle de org (los prompts se publican como "org/nombre").

    Nota: "Personal" es el nombre de pantalla que muestra la UI de LangSmith
    para el workspace personal del usuario. NO es un handle válido para la API;
    usarlo como prefijo causa el error "Cannot create a prompt for another tenant".
    Por eso, cualquier valor equivalente a "personal" (o vacío) se interpreta
    como workspace personal y se omite el prefijo.
    """
    raw = os.getenv("LANGSMITH_HUB_ORG", "").strip()
    if raw.lower() in _PERSONAL_SENTINEL:
        return None  # workspace personal: sin prefijo
    return raw


def push_to_langsmith(
    prompt_id: str, content: str, org: str | None, dry_run: bool
) -> None:
    """
    Sube un prompt a LangSmith Hub como ChatPromptTemplate con un mensaje de sistema.

    El contenido del .md se convierte en un ChatPromptTemplate con un único
    mensaje de sistema. Las variables {placeholder} presentes en el texto son
    detectadas automáticamente como input_variables del template.

    Args:
        prompt_id: Nombre del prompt (sin extensión, ej. "crosswalk_agent").
        content:   Texto completo del archivo .md.
        org:       Handle de org en LangSmith, o None para workspace personal.
        dry_run:   Si es True, imprime qué se haría sin ejecutar nada.
    """
    # Para workspace personal no se usa prefijo de org.
    full_id = f"{org}/{prompt_id}" if org else prompt_id
    workspace = org if org else "personal"

    if dry_run:
        print(f"  [dry-run] Se publicaría: {full_id} (workspace: {workspace})")
        return

    from langsmith import Client
    from langchain_core.prompts import ChatPromptTemplate

    # Envuelve el contenido .md como mensaje de sistema.
    template = ChatPromptTemplate.from_messages([("system", content)])

    # langchain.hub fue deprecado en langchain>=0.2.
    # La API correcta es langsmith.Client().push_prompt().
    # Se pasa la API key explícitamente porque el SDK busca LANGSMITH_API_KEY
    # pero este proyecto la almacena como LANGCHAIN_API_KEY.
    api_key = os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    client = Client(api_key=api_key)
    url = client.push_prompt(full_id, object=template, is_public=False)
    print(f"  ✅ Publicado: {url}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publica los prompts locales (.md) en LangSmith Hub."
    )
    parser.add_argument(
        "--prompt",
        default=None,
        metavar="NOMBRE",
        help="Publicar solo este prompt (nombre sin extensión .md). "
             "Si no se especifica, se publican todos.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué se publicaría sin realizar ningún cambio.",
    )
    args = parser.parse_args()

    org = _get_org()

    # Determinar qué archivos procesar
    if args.prompt:
        archivos = [PROMPTS_DIR / f"{args.prompt}.md"]
    else:
        archivos = sorted(PROMPTS_DIR.glob("*.md"))

    if not archivos:
        print(f"No se encontraron archivos .md en: {PROMPTS_DIR}")
        sys.exit(0)

    workspace_label = org if org else "personal"
    modo = "[dry-run] " if args.dry_run else ""
    print(f"{modo}Publicando {len(archivos)} prompt(s) en LangSmith Hub (workspace: {workspace_label})...")

    errores = 0
    for md_file in archivos:
        nombre = md_file.stem
        print(f"\n→ {nombre}")
        if not md_file.exists():
            print(f"  ❌ Archivo no encontrado: {md_file}")
            errores += 1
            continue
        try:
            contenido = md_file.read_text(encoding="utf-8")
            push_to_langsmith(nombre, contenido, org, dry_run=args.dry_run)
        except Exception as exc:
            print(f"  ❌ Error al publicar '{nombre}': {exc}")
            errores += 1

    print()
    if errores:
        print(f"⚠️  Finalizado con {errores} error(es).")
        sys.exit(1)
    else:
        print("✓ Todos los prompts publicados correctamente.")


if __name__ == "__main__":
    main()
