#!/usr/bin/env python3
"""Development server runner for Shu RAG Backend.

This script sets up the development environment and starts the Shu API server.
"""

import os
import subprocess
import sys
from pathlib import Path

# Add the src directory to the path
project_root = Path(__file__).parent.parent  # .../backend
repo_root = project_root.parent  # repo root
src_path = project_root / "src"  # .../backend/src
sys.path.insert(0, str(src_path))  # allow local imports when script itself imports


def setup_dev_environment():
    """Set up development environment variables."""
    # Set default database URL if not configured
    if not os.environ.get("SHU_DATABASE_URL"):
        os.environ["SHU_DATABASE_URL"] = "postgresql://postgres:password@localhost:5432/shu_dev"

    # Set development-specific settings
    os.environ["SHU_ENVIRONMENT"] = "development"
    os.environ["SHU_DEBUG"] = "true"
    os.environ["SHU_LOG_LEVEL"] = "DEBUG"
    os.environ["SHU_RELOAD"] = "true"

    print("Development environment configured:")
    print(f"  Database URL: {os.environ.get('SHU_DATABASE_URL')}")
    print(f"  Debug Mode: {os.environ.get('SHU_DEBUG')}")
    print(f"  Log Level: {os.environ.get('SHU_LOG_LEVEL')}")


def start_dev_server():
    """Start the development server."""
    try:
        # Import settings to get host and port
        from shu.core.config import get_settings_instance

        settings = get_settings_instance()

        # Start the FastAPI server with auto-reload
        cmd_str = f"{sys.executable} -m uvicorn shu.main:app --app-dir {src_path} --reload --host {settings.api_host} --port {settings.api_port} --log-level debug"
        print(f"Running: {cmd_str}")
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "shu.main:app",
            "--app-dir",
            str(src_path),
            "--reload",
            "--host",
            settings.api_host,
            "--port",
            str(settings.api_port),
            "--log-level",
            "debug",
        ]

        print("Starting Shu development server...")
        print(f"Server will be available at: http://{settings.api_host}:{settings.api_port}")
        print(f"API documentation: http://{settings.api_host}:{settings.api_port}/docs")
        print("Press Ctrl+C to stop the server")

        # Run from repo root so relative paths (e.g., ./data/branding) resolve to <repo>/data
        subprocess.run(cmd, cwd=repo_root, check=False)

    except KeyboardInterrupt:
        print("\nShutting down development server...")
    except Exception as e:
        print(f"Error starting development server: {e}")
        sys.exit(1)


def main():
    """Main function."""
    setup_dev_environment()
    start_dev_server()


if __name__ == "__main__":
    main()
