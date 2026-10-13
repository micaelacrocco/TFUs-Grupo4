# app/security/gatekeeper.py
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import os

security_scheme = HTTPBearer()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"

def gatekeeper_verify(credentials: HTTPAuthorizationCredentials = Security(security_scheme)) -> dict:
    """
    Patrón Gatekeeper: Intercepta, valida la identidad (JWT) y extrae el contexto
    del usuario antes de permitir el acceso al perímetro interno de microservicios.
    """
    token = credentials.credentials
    try:
        # Validación criptográfica y de expiración del token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido: falta identificador de usuario (sub)"
            )
        return {"user_id": user_id, "roles": payload.get("roles", [])}
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token de sesión ha expirado"
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se pudo validar la credencial de autenticación"
        )