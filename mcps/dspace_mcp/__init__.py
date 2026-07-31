import os
import sys

# Añadir el directorio 'src' al sys.path para que las importaciones internas (ej. config) funcionen correctamente
_src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "src"))
if _src_dir not in sys.path:
    sys.path.append(_src_dir)
