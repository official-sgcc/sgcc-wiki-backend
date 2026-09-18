# 문서 권한 DB 확인

운영 DB를 읽기 전용 확인한 결과 permissions에는 wiki_doc_title/update/move/delete만 존재한다. 사용자 요청에 따라 comment 필드는 복구하지 않고 관련 컬럼 추가·시작 차단을 제거했다. 코멘트 기능과 권한은 필요할 때 구현한다. 운영 DB는 변경하지 않았다.
