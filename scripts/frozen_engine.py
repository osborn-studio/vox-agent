"""PyInstaller entry point for the self-contained Vox Agent engine."""

from __future__ import annotations

import argparse


# The bundled engine serves only this machine (T29): its personal-edition
# routes trust loopback callers, so it must never listen on a network address.
LOOPBACK_HOSTS = ("127.0.0.1", "::1")


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be a number") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be 1-65535")
    return port


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the bundled Vox Agent engine")
    parser.add_argument("--host", default="127.0.0.1", choices=LOOPBACK_HOSTS)
    parser.add_argument("--port", type=_port, required=True)
    parser.add_argument("--log-level", default="info")
    return parser.parse_args(argv)


def main() -> None:
    import uvicorn

    from engine.server import app

    args = parse_args()
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            access_log=False,
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
