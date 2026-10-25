import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="FRM helyi API + előre lefordított Angular felület")
    parser.add_argument("--port", type=int, default=int(os.getenv("FRM_API_PORT", "8000")))
    parser.add_argument("--data-dir", type=Path, help="Feladatok és AI-cache helyi mappája")
    parser.add_argument("--config", type=Path, help="Megbízható szerveroldali generátorkonfiguráció, például Oracle exporter")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("A port 1..65535 közötti érték kell legyen.")
    os.environ["FRM_API_PORT"] = str(args.port)
    if args.data_dir:
        os.environ["FRM_WORK_DIR"] = str(args.data_dir.resolve())
    if args.config:
        os.environ["FRM_SERVER_CONFIG"] = str(args.config.resolve())
    try:
        import uvicorn
        from .app import create_app
        from .settings import Settings
    except ImportError:
        parser.exit(1, "A webes függőségek hiányoznak. Telepítés: python -m pip install -r requirements-web.txt\n")
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    print(f"FRM helyi backend és felület: http://localhost:{args.port}", flush=True)
    print('Engedélyezett frontend originek: '+', '.join(settings.cors_origins), flush=True)
    print('Engedélyezett egyedi fejlécek: '+', '.join(settings.cors_headers), flush=True)
    print('CORS credentials: '+str(settings.cors_allow_credentials).lower(), flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
