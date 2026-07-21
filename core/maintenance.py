"""메일 발송, DB 백업, 관리자 계정 부트스트랩 같은 앱 부수 작업."""

import os
import smtplib
import sqlite3
from datetime import datetime
from email.message import EmailMessage
from sqlmodel import Session
from core.config import (
    BACKUP_DIR, DB_PATH, FRONTEND_URL, logger,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
)
from core.database import engine
from core.login_utils import create_email_verification_token, hash_password
from schemas.wiki_user import WikiUser

def send_email(to: str, subject: str, body: str):
    """이메일을 발송한다. SMTP가 설정돼 있으면 실제 전송, 아니면 로그로 대체한다.

    SMTP_HOST 환경변수가 없으면(개발/미설정) 실제 발송 대신 내용을 로그에 남긴다.
    비밀번호 재설정 링크가 여기로 흐르므로, 로그를 보면 흐름을 확인할 수 있다.
    나중에 SMTP_* 환경변수만 채우면 코드 변경 없이 실제 발송으로 전환된다.
    """
    if not SMTP_HOST:
        logger.info('email not configured; would send to %s [%s]:\n%s', to, subject, body)
        return

    msg = EmailMessage()
    msg['From'] = SMTP_FROM
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    logger.info('email sent to %s [%s]', to, subject)

def send_email_verification(username: str, email: str):
    """해당 이메일로 인증 링크를 발송한다."""
    token = create_email_verification_token(username, email)
    verify_link = f'{FRONTEND_URL}/verify-email?token={token}'
    send_email(
        email,
        'SGCC Wiki 이메일 인증',
        f'아래 링크로 이메일을 인증하세요 (24시간 내 유효):\n\n{verify_link}',
    )

def backup_database():
    """현재 SQLite DB를 db_backups/에 타임스탬프 파일로 스냅샷한다.

    단순 파일 복사(shutil.copy2)가 아니라 sqlite3.Connection.backup(SQLite Backup API)을
    쓴다. 쓰기 트랜잭션이 진행 중이어도 일관된 스냅샷을 뜨기 위함이며,
    단순 파일 복사로 회귀시키면 백업이 깨질 수 있다.

    lifespan에 등록된 자정(00:00) cron 스케줄러가 호출한다. 백업 파일명은
    `db_backup_YYYYMMDD_HHhMMmSSs.db` 형식.

    Raises:
        Exception: 백업 실패 시 로그를 남기고 예외를 그대로 재전파한다.
                   (원본/대상 커넥션은 finally에서 항상 닫힌다.)
    """
    today_str = datetime.now().strftime('%Y%m%d_%Hh%Mm%Ss')
    backup_path = f'{BACKUP_DIR}/db_backup_{today_str}.db'

    source = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(backup_path)
    try:
        with dest:
            source.backup(dest)
        logger.info('database backup created: %s', backup_path)
    except Exception:
        logger.exception('database backup failed: %s', backup_path)
        raise
    finally:
        source.close()
        dest.close()

def bootstrap_admin():
    """환경변수로 지정된 관리자 계정을 앱 시작 시 보장한다.

    ADMIN_USERNAME / ADMIN_PASSWORD 환경변수를 읽어:
      - 둘 중 하나라도 비어 있으면 아무 동작도 하지 않고 반환한다.
      - 해당 사용자가 없으면 permission='admin'으로 새로 생성한다.
      - 이미 있으면 permission을 'admin'으로 승격한다(이미 admin이면 그대로 둠).

    register API는 RESERVED_USERNAMES 때문에 'admin' 가입을 막으므로, 관리자 계정은
    이 부트스트랩 경로로만 만들어진다.
    """
    admin_username = os.getenv('ADMIN_USERNAME')
    admin_password = os.getenv('ADMIN_PASSWORD')
    if not admin_username or not admin_password:
        return

    with Session(engine) as session:
        user = session.get(WikiUser, admin_username)
        if user:
            if user.permission != 'admin':
                user.permission = 'admin'
                session.add(user)
                session.commit()
                logger.info('admin bootstrap: promoted existing user to admin: %s', admin_username)
        else:
            user = WikiUser(
                username=admin_username,
                password=hash_password(admin_password),
                permission='admin',
                bio='',
                email=None,
            )
            session.add(user)
            session.commit()
            logger.info('admin bootstrap: created admin user: %s', admin_username)
