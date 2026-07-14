import bcrypt
import jwt
import os
import re
import time
import pyotp
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status

load_dotenv()

JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')
if not JWT_SECRET_KEY:
    raise RuntimeError('JWT_SECRET_KEY environment variable must be set. Refusing to start with a default signing key.')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
JWT_TOKEN_EXPIRE_MINUTES = int(os.getenv('JWT_TOKEN_EXPIRE_MINUTES', 180))
PASSWORD_RESET_EXPIRE_MINUTES = int(os.getenv('PASSWORD_RESET_EXPIRE_MINUTES', 30))
MFA_TOKEN_EXPIRE_MINUTES = int(os.getenv('MFA_TOKEN_EXPIRE_MINUTES', 5))
EMAIL_VERIFY_EXPIRE_MINUTES = int(os.getenv('EMAIL_VERIFY_EXPIRE_MINUTES', 1440))
TOTP_ISSUER = os.getenv('TOTP_ISSUER', 'SGCC Wiki')

USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{3,32}$')
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

DUMMY_PASSWORD_HASH = '$2b$12$Iuk3jgqSiT6nHyHj.u6sXOWkTFl/udS.Zh7/QdHqqKwvCZJqmqD12'

def validate_username(username: str):
    if not USERNAME_PATTERN.match(username):
        raise HTTPException(
            status_code=400,
            detail='Username must be 3-32 characters and contain only letters, numbers, underscores, or hyphens.'
        )

def validate_email(email: str):
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail='Invalid email address.')

def validate_password(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail='Password must be at least 8 characters long.')
    if not re.search(r'[A-Za-z]', password):
        raise HTTPException(status_code=400, detail='Password must contain at least one letter.')
    if not re.search(r'\d', password):
        raise HTTPException(status_code=400, detail='Password must contain at least one digit.')

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
    data = {'sub': username, 'purpose': 'access', 'exp': datetime.now(timezone.utc) + timedelta(minutes=JWT_TOKEN_EXPIRE_MINUTES)}

    return jwt.encode(data, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_jwt_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get('sub')
        # purpose를 강제하지 않으면 같은 키로 서명된 mfa/email_verify 토큰이
        # 정식 세션 토큰으로 통과해 2FA가 우회된다.
        if username is None or payload.get('purpose') != 'access':
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid token'
            )
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Token has expired'
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid token'
        )

def create_mfa_token(username: str) -> str:
    data = {
        'sub': username,
        'purpose': 'mfa',
        'exp': datetime.now(timezone.utc) + timedelta(minutes=MFA_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(data, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_mfa_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('purpose') != 'mfa' or payload.get('sub') is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid MFA token')
        return payload['sub']
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='MFA token has expired')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid MFA token')

# Reset tokens are signed with the secret + current password hash so that a
# successful reset (which changes the hash) invalidates the token: single use.
def create_password_reset_token(username: str, password_hash: str) -> str:
    data = {
        'sub': username,
        'purpose': 'password_reset',
        'exp': datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
    }
    return jwt.encode(data, JWT_SECRET_KEY + password_hash, algorithm=JWT_ALGORITHM)

def read_reset_token_subject(token: str) -> str | None:
    """토큰을 서명 검증 없이 열어 sub만 읽는다. 사용자 조회 후 정식 검증 전에 쓴다."""
    try:
        payload = jwt.decode(token, options={'verify_signature': False})
    except jwt.InvalidTokenError:
        return None
    return payload.get('sub') if payload.get('purpose') == 'password_reset' else None

def verify_password_reset_token(token: str, password_hash: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY + password_hash, algorithms=[JWT_ALGORITHM])
        if payload.get('purpose') != 'password_reset' or payload.get('sub') is None:
            raise HTTPException(status_code=400, detail='Invalid or expired reset token.')
        return payload['sub']
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail='Invalid or expired reset token.')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail='Invalid or expired reset token.')

# The email claim is embedded so that changing the email later invalidates any
# outstanding verification link (the endpoint compares it to the current email).
def create_email_verification_token(username: str, email: str) -> str:
    data = {
        'sub': username,
        'email': email,
        'purpose': 'email_verify',
        'exp': datetime.now(timezone.utc) + timedelta(minutes=EMAIL_VERIFY_EXPIRE_MINUTES),
    }
    return jwt.encode(data, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def verify_email_verification_token(token: str) -> tuple[str, str]:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('purpose') != 'email_verify' or payload.get('sub') is None or payload.get('email') is None:
            raise HTTPException(status_code=400, detail='Invalid or expired verification token.')
        return payload['sub'], payload['email']
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail='Invalid or expired verification token.')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail='Invalid or expired verification token.')

def generate_totp_secret() -> str:
    return pyotp.random_base32()

def totp_provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)

def matched_totp_step(secret: str, code: str) -> int | None:
    """코드가 유효하면 매칭된 TOTP 타임스텝(정수)을, 아니면 None을 반환한다.

    반환된 스텝을 저장(WikiUser.totp_last_step)하고 다음 검증 때 그보다 큰 스텝만
    허용하면, 같은 코드를 유효 창 안에서 다시 쓰는 재사용(replay)을 막을 수 있다.
    verify()가 bool만 주면 어떤 창이 맞았는지 알 수 없어 single-use 판정이 불가능하다.

    valid_window=1은 서버와 인증앱 사이 ~30초 시계 오차를 허용한다.
    """
    totp = pyotp.TOTP(secret)
    now = time.time()
    for offset in range(-1, 2):
        at = now + offset * totp.interval
        if totp.verify(code, for_time=at):
            return int(at // totp.interval)
    return None