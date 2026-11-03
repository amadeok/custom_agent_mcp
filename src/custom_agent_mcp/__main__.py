from __future__ import annotations

import multiprocessing

from .server import create_server
from .settings import load_settings


def main() -> None:
    multiprocessing.freeze_support()
    create_server(load_settings()).run(transport="stdio")


if __name__ == "__main__":
    main()