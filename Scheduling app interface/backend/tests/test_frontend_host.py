from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.frontend import mount_frontend

def test_production_static_frontend_keeps_api_routes(tmp_path):
    (tmp_path/"index.html").write_text("<html>AL13 frontend</html>",encoding="utf-8")
    (tmp_path/"assets").mkdir()
    (tmp_path/"assets"/"app.js").write_text("fetch('/api/health')",encoding="utf-8")
    app=FastAPI()
    @app.get("/api/health")
    def health():
        return {"status":"ok"}
    mount_frontend(app,tmp_path)
    with TestClient(app) as client:
        assert client.get("/").status_code==200
        assert "AL13 frontend" in client.get("/").text
        assert client.get("/assets/app.js").status_code==200
        assert client.get("/api/health").json()=={"status":"ok"}
        assert client.get("/api/missing").status_code==404
