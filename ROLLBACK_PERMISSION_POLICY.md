# 권한 정책 배포 복구 기준 — 2026-09-19

## 확보한 자료

- 양쪽 저장소의 `rollback/permission-policy-predeploy-20260919` 태그: 배포 전 main.
- 백엔드 배포 전 커밋: `6293e983e1b2f769f8a470bf5d15fdacff770d2b`.
- 프론트 배포 전 커밋: `0379f77a` (전체 해시는 위 태그로 확인).
- 운영 백엔드 백업: `/home/ubuntu/sgcc-wiki-backend/db_backups/permission-policy-predeploy-20260918T172002Z/`.
- 백업 디렉터리에 SQLite backup API로 만든 `wiki.db`(integrity_check=ok), `backend.bundle`, `requirements-freeze.txt`, `manifest.json`을 저장했다.
- 배포 전 실제 프론트 AWS 번들: 로컬 `D:\09_GitHub\sgccwik\deployment-backups\permission-policy-20260919\frontend-predeploy-aws.zip`. 같은 디렉터리에 배포 대상 변수도 보관했다.

## 코드 복구 (기본 권장)

양쪽 저장소 main에서 이번 develop→main 머지 커밋을 `git revert -m 1 <merge-commit>`한 뒤 push한다. CI/CD가 이전 코드를 다시 배포한다. develop에서도 동일 변경을 되돌려 재배포 시 복구가 덮어써지지 않도록 한다. 과거 main 태그와 프론트 실배포 번들도 별도로 보존하므로 아티팩트 보관 기한에 의존하지 않는다.

긴급 백엔드 복구는 운영 서비스 중지 후 백업 manifest의 커밋을 checkout하고, 필요하면 백업의 requirements-freeze.txt로 의존성을 복원한 뒤 서비스 재시작·health 확인으로 수행한다. GitHub 자동 배포가 진행 중이면 먼저 중단해 긴급 복구를 덮어쓰지 않도록 한다. 영구적인 원격 복구는 revert로 기록한다.

이번 DB 변경은 rename/session_version 컬럼 및 revokedtoken 테이블 추가다. 기존 데이터와 기존 권한 목록을 삭제하거나 덮어쓰지 않는다. 이전 코드가 추가 컬럼을 무시할 수 있으므로 **코드 복구 시 DB는 우선 유지**한다. 이렇게 하면 배포 이후 새 글·사용자·편집을 보존한다.

## DB까지 복구해야 할 경우

서비스를 중지하고 현재 DB도 별도로 백업한 후, manifest의 db_path 위치로 백업 wiki.db를 복원한다. WAL/SHM 파일의 존재와 활성 연결을 확인하여 이전 DB의 WAL이 섞이지 않게 처리한 뒤 서비스를 시작하고 무결성·문서·사용자·health를 확인한다. **배포 전 백업으로 복구하면 백업 이후 데이터는 되돌아가므로 사용자에게 범위와 손실을 설명하고 승인받는다.**

## 주의

이전 코드로 복구하면 새로운 토큰 폐기·권한 정책은 적용되지 않는다. DB 백업은 운영 서버에 있으므로 별도 재해 복구가 필요한 경우 승인된 안전한 저장소에 추가 복사한다. 작업 브랜치와 복구 태그는 실제 동작 확인 전 삭제하지 않는다.
