import bcrypt
import jwt
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status

load_dotenv()

JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'testsecretkey')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
JWT_TOKEN_EXPIRE_MINUTES = int(os.getenv('JWT_TOKEN_EXPIRE_MINUTES', 60))

def hash_password(plain_password: str) -> str:
    plain_password_bytes = plain_password.encode('utf-8')

    salt = bcrypt.gensalt()
    encrypted_password_bytes = bcrypt.hashpw(plain_password_bytes, salt)

    return encrypted_password_bytes.decode('utf-8')

def verify_password(plain_password: str, encrypted_password: str):
    plain_password_bytes = plain_password.encode('utf-8')
    encrypted_password_bytes = encrypted_password.encode('utf-8')

    return bcrypt.checkpw(plain_password_bytes, encrypted_password_bytes)

def create_jwt_token(username: str) -> str:
    data = {'sub': username, 'exp': datetime.now(timezone.utc) + timedelta(minutes=JWT_TOKEN_EXPIRE_MINUTES)}

    return jwt.encode(data, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_jwt_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )