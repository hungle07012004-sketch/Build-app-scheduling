import pytest
from backend.database import db
from backend.services import input_service, optimization_service
from backend.main import app
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path,monkeypatch):
    for module in (db,input_service,optimization_service):
        monkeypatch.setattr(module,"DATA_DIR",tmp_path)
    with TestClient(app) as client:
        response=client.post("/api/auth/login",json={"email":"planner@factory.com","password":"planner-demo"})
        assert response.status_code==200
        client.headers["Authorization"]="Bearer "+response.json()["token"]
        yield client
