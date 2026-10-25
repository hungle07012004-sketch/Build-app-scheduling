import hashlib
import hmac
import secrets
import time
from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.config import DEMO_EMAIL, DEMO_PASSWORD
from backend.database.db import connect
from backend.errors import AppError
from backend.models.schemas import LoginRequest

router = APIRouter(prefix="/api/auth",tags=["auth"])
bearer = HTTPBearer(auto_error=False)

def password_hash(password,salt):
    return hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),200000).hex()

def init_user():
    with connect() as db:
        if not db.execute("SELECT email FROM users WHERE email=?",(DEMO_EMAIL,)).fetchone():
            salt = secrets.token_hex(16)
            db.execute("INSERT INTO users VALUES(?,?,?)",(DEMO_EMAIL,salt,password_hash(DEMO_PASSWORD,salt)))

def require_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise AppError("UNAUTHORIZED","Please sign in.",status=401)
    token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    with connect() as db:
        row = db.execute("SELECT email FROM sessions WHERE token_hash=? AND expires_at>?",(token_hash,time.time())).fetchone()
    if not row:
        raise AppError("UNAUTHORIZED","Session expired. Please sign in again.",status=401)
    return row["email"]

@router.post("/login")
def login(body: LoginRequest):
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE email=?",(body.email,)).fetchone()
        if not row or not hmac.compare_digest(row["password_hash"],password_hash(body.password,row["salt"])):
            raise AppError("INVALID_CREDENTIALS","Email or password is incorrect.",status=401)
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions VALUES(?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),body.email,time.time()+43200))
    return {"token":token,"email":body.email}

@router.post("/logout")
def logout(credentials: HTTPAuthorizationCredentials = Depends(bearer), user=Depends(require_user)):
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(credentials.credentials.encode()).hexdigest(),))
    return {"success":True}
