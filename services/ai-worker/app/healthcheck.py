"""Healthcheck para o Docker, executado como `python -m app.healthcheck`.

Usa a própria imagem em vez de depender de curl instalado — uma
dependência a menos no container, e funciona igual em dev e produção.
Sai com 0 se pronto, 1 caso contrário.
"""

import sys
import urllib.error
import urllib.request


def main() -> int:
    url = "http://127.0.0.1:8001/internal/readyz"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:  # noqa: S310
            return 0 if resp.status == 200 else 1
    except (urllib.error.URLError, TimeoutError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
