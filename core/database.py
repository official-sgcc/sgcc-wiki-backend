"""SQLite 엔진과 테이블 생성.

엔진 생성과 create_all이 임포트 시점에 실행되므로, 테스트는 DB_PATH 환경변수를
바꾼 뒤 이 모듈을 sys.modules에서 지워 다시 임포트해야 한다(tests/conftest.py 참조).
"""

from sqlmodel import create_engine, SQLModel
from core.config import DB_PATH

# create_all이 테이블을 알려면 모델 모듈이 먼저 임포트돼 metadata에 등록돼야 한다.
from schemas.wiki_doc import WikiDoc, WikiDocVersion
from schemas.wiki_user import WikiUser
from schemas.permissions import Permissions
from schemas.tags import WikiTag
from schemas.categories import WikiCategory

engine = create_engine(f'sqlite:///{DB_PATH}')
SQLModel.metadata.create_all(engine)


def migrate_legacy_document_schema() -> None:
    """기존 SQLite 문서 테이블에 신규 컬럼을 데이터 손실 없이 보완한다."""
    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql('PRAGMA table_info(wikidoc)')
        }
        if 'view_count' not in columns:
            connection.exec_driver_sql(
                'ALTER TABLE wikidoc '
                'ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0'
            )


migrate_legacy_document_schema()
