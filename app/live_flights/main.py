"""CLI entrypoint to launch the Python Real-Time Flight Data Service."""
import argparse
import sys
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Launch Real-Time Flight Data Service")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host")
    parser.add_argument("--port", type=int, default=8765, help="HTTP/WS port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code change")
    args = parser.parse_args()

    print(f"============================================================")
    print(f"🚀 Starting Airport 3D Real-Time Flight Service")
    print(f"📡 WebSocket endpoint: ws://127.0.0.1:{args.port}/ws/live")
    print(f"🌐 REST API:          http://127.0.0.1:{args.port}/api/flights/live")
    print(f"============================================================")

    uvicorn.run("app.live_flights.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
