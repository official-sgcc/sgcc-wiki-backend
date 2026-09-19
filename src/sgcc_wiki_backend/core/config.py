"""환경변수·로깅·rate limiter 등 앱 전역 설정.

load_dotenv()는 다른 모듈이 os.getenv를 호출하기 전에 실행돼야 하므로,
이 모듈을 가장 먼저 임포트되는 자리에 둔다.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address

load_dotenv()

LOG_DIR = './logs'
os.makedirs(LOG_DIR, exist_ok=True)

log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
file_handler = RotatingFileHandler(f'{LOG_DIR}/app.log', maxBytes=5_000_000, backupCount=5)
file_handler.setFormatter(log_formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[stream_handler, file_handler])
logger = logging.getLogger('sgcc-wiki')

BACKUP_DIR = './db_backups'
DB_PATH = os.getenv('DB_PATH', 'wiki.db')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')
RESERVED_USERNAMES = {'guest', 'admin', 'system', 'bot', 'anonymous'}

SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
SMTP_FROM = os.getenv('SMTP_FROM', 'no-reply@sgcc-wiki.local')

RESEND_API_KEY = os.getenv('RESEND_API_KEY')
DEFAULT_EMAIL_FROM = 'no-reply@sgcc-wiki.local'
EMAIL_FROM = os.getenv('EMAIL_FROM') or SMTP_FROM
# 명시하지 않으면 채워진 자격증명으로 추론한다: resend > smtp > log(발송 대신 로그).
EMAIL_PROVIDER = os.getenv('EMAIL_PROVIDER') or ('resend' if RESEND_API_KEY else 'smtp' if SMTP_HOST else 'log')
# 빈 문자열(플레이스홀더 .env 줄)은 미설정으로 취급해 기본값으로 되돌린다.
EMAIL_DAILY_LIMIT = int((os.getenv('EMAIL_DAILY_LIMIT') or '').strip() or 90)
EMAIL_COOLDOWN_SECONDS = int((os.getenv('EMAIL_COOLDOWN_SECONDS') or '').strip() or 60)

if EMAIL_PROVIDER not in ('log', 'smtp', 'resend'):
    raise RuntimeError(f'EMAIL_PROVIDER must be one of log/smtp/resend, got {EMAIL_PROVIDER!r}.')
if EMAIL_PROVIDER == 'resend' and not RESEND_API_KEY:
    raise RuntimeError('EMAIL_PROVIDER=resend requires RESEND_API_KEY.')
if EMAIL_PROVIDER == 'smtp' and not SMTP_HOST:
    raise RuntimeError('EMAIL_PROVIDER=smtp requires SMTP_HOST.')
# log 이외의 provider는 실제로 배달 가능한 발신 주소가 필요하다. 플레이스홀더로 두면
# Resend는 미인증 도메인으로 거부하고 SMTP도 배달 불가라 사용자는 200을 받지만 메일은 안 온다.
if EMAIL_PROVIDER != 'log' and EMAIL_FROM == DEFAULT_EMAIL_FROM:
    raise RuntimeError('EMAIL_FROM must be a real sender address when EMAIL_PROVIDER is not log.')

os.makedirs(BACKUP_DIR, exist_ok=True)

limiter = Limiter(key_func=get_remote_address)
