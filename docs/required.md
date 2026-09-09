# 추가 요청사항

- 검증 명령으로 지정된 `python test_sample.py`를 사용하려면 현재 `working/test_sample.py`로 이동된 파일을 루트로 복원해 주세요. 현재 위치에서는 `python -m working.test_sample`로 실행하면 `ALL PASS`하지만, `python working/test_sample.py`는 프로젝트 루트를 import 경로에 넣지 않아 실패합니다.
