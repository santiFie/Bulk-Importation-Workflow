"""
Root conftest.py — makes the project root importable by pytest.

Without this, pytest cannot resolve `from core.graph import ...` because
the project root is not on sys.path when tests are collected from a
subdirectory.  Placing conftest.py here causes pytest to add this directory
to sys.path automatically (pytest's "rootdir" / "importmode" logic).
"""
