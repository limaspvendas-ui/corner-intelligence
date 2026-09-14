"""Configuracao dos testes: garante import de src/ a partir da raiz."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))