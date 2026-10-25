"""Serve the compiled UI on the API origin for a local production/demo deployment."""
from pathlib import Path
from fastapi.staticfiles import StaticFiles

def mount_frontend(app, directory: Path):
    if not (directory / "index.html").is_file():
        raise RuntimeError("Production frontend is missing. Run npm run build before setting SERVE_FRONTEND=1.")
    # API routes are registered first. The UI uses in-memory page navigation at '/'.
    app.mount("/",StaticFiles(directory=str(directory),html=True),name="frontend")
