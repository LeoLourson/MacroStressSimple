import asyncio
import sys

import uvicorn


def loop_factory():
    # Асинхронным соединениям Psycopg в Windows нужен цикл событий на селекторах.
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def main():
    uvicorn.run(
        "msa.api:app",
        host="127.0.0.1",
        port=8091,
        workers=1,
        loop="msa.__main__:loop_factory",
    )


if __name__ == "__main__":
    main()
