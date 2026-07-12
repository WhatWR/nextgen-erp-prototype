from __future__ import annotations

import argparse

from order_intake.server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NextGen ERP order-intake prototype API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8200, type=int)
    parser.add_argument("--db")
    parser.add_argument("--export-dir")
    args = parser.parse_args()
    serve(args.host, args.port, args.db, args.export_dir)


if __name__ == "__main__":
    main()
