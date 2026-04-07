import bcrypt

def hash_password(plain_password: str) -> str:
    plain_password_bytes = plain_password.encode('utf-8')

    salt = bcrypt.gensalt()
    encrypted_password_bytes = bcrypt.hashpw(plain_password_bytes, salt)

    return encrypted_password_bytes.decode('utf-8')

def verify_password(plain_password: str, encrypted_password: str):
    plain_password_bytes = plain_password.encode('utf-8')
    encrypted_password_bytes = encrypted_password.encode('utf-8')

    return bcrypt.checkpw(plain_password_bytes, encrypted_password_bytes)