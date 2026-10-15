# app/routers/auth_router.py
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import jwt
import os

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticación"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "investnow_secret_jwt_key_tfu4_gatekeeper")
ALGORITHM = "HS256"

# Base de datos simulada de usuarios en memoria (similar al catálogo)
USERS_DB = [
    {"user_id": 1, "username": "inversor1", "password": "password123", "roles": ["investor"]},
    {"user_id": 2, "username": "admin1", "password": "adminpassword", "roles": ["admin"]}
]

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/login")
def login(credentials: LoginRequest):
    """
    Simula el inicio de sesión: valida contra la base de datos en memoria 
    y emite un JWT firmado para que el cliente pueda usarlo en el Gatekeeper.
    """
    user = next((u for u in USERS_DB if u["username"] == credentials.username and u["password"] == credentials.password), None)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas"
        )
    
    # Generar el payload del JWT con el subject (sub) como el user_id
    payload = {
        "sub": str(user["user_id"]),
        "username": user["username"],
        "roles": user["roles"]
    }
    
    # Firmar el token
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "message": "Autenticación exitosa. Usar este token en el header Authorization: Bearer <token>"
    }