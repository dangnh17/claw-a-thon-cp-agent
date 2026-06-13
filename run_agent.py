#!/usr/bin/env python3
"""
Entry point — starts FastAPI HTTP server.

Usage:
    python run_agent.py
    python run_agent.py --port 8080
"""
import argparse
import logging
import os

import uvicorn

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    parser = argparse.ArgumentParser(description="CP Agent")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    from agent.app import create_app
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    app = create_app(output_dir=output_dir)

    logging.info(f"Server → http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
