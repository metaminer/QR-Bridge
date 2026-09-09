# QR 프레임 패킷 포맷

`sender/encode.py`가 각 QR 프레임에 인코딩하는 바이너리 페이로드의 구조. 이 포맷의
직렬화/역직렬화(화이트닝 포함)는 **`common/qr_wire.py` 한 곳에서만** 구현하며,
`sender/`와 `receiver/`는 반드시 그 함수(`pack_packet`/`unpack_packet`)를 통해서만
QR 페이로드를 만들거나 읽는다. 두 곳에 같은 로직을 중복 구현하지 말 것 — 과거에
송신측만 화이트닝을 적용하고 수신측이 이를 반영하지 못해 전체 디코딩이 깨진 적이 있다.

## 바이너리 레이아웃 (QRT2, big-endian)

`">4sIII32sH"` (고정부) + 가변 `filename` + `">H"`(data_len) + 가변 `data`:

| 필드 | 타입 | 크기 | 설명 |
|------|------|------|------|
| magic | bytes | 4 | 고정값 `b"QRT2"` — 포맷 식별/버전 태그 |
| seq | uint32 | 4 | 패킷 시퀀스 번호 (`common.lt_wrapper.Packet["seq"]`) |
| seed | uint32 | 4 | LT 블록 선택 시드 (`common.lt_wrapper.Packet["seed"]`) |
| total_k | uint32 | 4 | 원본 블록(청크) 총 개수 (`common.lt_wrapper.Packet["total_k"]`) |
| file_hash | bytes | 32 | SHA-256 다이제스트 **raw bytes** (hex 문자열이 아님, 원본 파일 데이터 기준) |
| filename_len | uint16 | 2 | 뒤따르는 `filename`의 UTF-8 바이트 길이 |
| filename | bytes | filename_len | 원본 파일명(확장자 포함, 예: `"report.pdf"`) UTF-8. 경로 없이 basename만 |
| data_len | uint16 | 2 | 뒤따르는 `data`의 길이 (바이트) |
| data | bytes | data_len | XOR 결합된 청크 데이터 (`common.lt_wrapper.Packet["data"]`), 기본 최대 1024 |

고정 헤더(magic~filename_len) = 50 bytes. 기본 청크 크기(1024) 기준, 파일명이 짧으면
(예: 20바이트) 프레임당 전체 페이로드는 대략 1096 bytes로, QR ECC 레벨 M에서 여유
있게 인코딩된다(바이너리 모드 한계 ≈2.9KB).

**설계 이유**: 원본 파일명은 LT 페이로드(`LTEncoder`가 감싸는 `data`) 안이 아니라 QR
프레임 헤더 쪽에 둔다. 그래야:
1. `common/lt_wrapper.py`(LT 인코더/디코더)는 전혀 손대지 않아도 된다 — 파일명은
   순수 데이터 바이트 수를 늘리지 않는다.
2. `LTEncoder.file_hash`/`decoder._file_hash`는 여전히 **원본 파일 내용만**의
   SHA-256이라서, `receiver/verify.py`의 검증 의미(사용자가 원본 파일 해시와 직접
   비교 가능)가 그대로 유지된다.
3. 파일명이 **모든 프레임에 반복**되므로(패킷 0에만 있는 게 아니라), 어느 프레임 하나만
   살아남아도 파일명을 복원할 수 있다 — LT 데이터와 동일한 손실 내성을 가진다.

**버전 태그**: `QRT1`(파일명 필드 없음)은 더 이상 지원하지 않는다. `unpack_packet()`은
`magic != b"QRT2"`이면 `ValueError`를 던진다.

## XOR 화이트닝 (중요 — `common/qr_wire.py`가 자동으로 처리)

LT 청크는 파일 끝부분에서 0으로 패딩되고, 패킷은 여러 청크를 XOR 결합하므로 실제로
**같은 바이트가 길게 반복되는 페이로드**(특히 전부 0)가 자주 발생한다. 사용 중인
`qrcode` 라이브러리 버전은 이런 반복적인 바이너리 데이터를 Reed-Solomon 인코딩할 때
`ValueError: glog(0)`로 죽는 버그가 있다(버전을 아무리 올려도 재현됨 — 실측 확인됨).

이를 피하기 위해 `common/qr_wire.pack_packet()`은 **헤더+파일명+데이터를 합친 전체
페이로드**를 QR에 넣기 직전에 고정 키스트림과 XOR("화이트닝")한다. 키스트림은
`random.Random(0x51515151)`로 생성한 의사난수 바이트열이며, 페이로드 길이만큼 매번
동일하게 재현된다(`common/qr_wire._whitening_stream`).

XOR은 대합(involution)이므로 `common/qr_wire.unpack_packet()`은 QR에서 읽은 원시 바이트에
같은 `whiten()`을 한 번 더 적용해 화이트닝을 해제한 뒤 헤더를 파싱한다. 즉:

```python
from common.qr_wire import pack_packet, unpack_packet

wire_bytes = pack_packet(lt_packet, filename)     # 송신측: 직렬화 + 화이트닝 → QR에 인코딩
lt_packet2, filename2 = unpack_packet(wire_bytes)  # 수신측: 화이트닝 해제 + 파싱
```

`unpack_packet()`은 `(packet, filename)` 튜플을 반환한다 — `packet`은 그대로
`LTDecoder.add_packet(packet)`에 넘기고, `filename`은 (모든 프레임에서 동일하므로) 첫
번째로 수신된 값을 저장해두면 된다.

`sender/encode.py`와 `receiver/decode_video.py`는 각자 이 두 함수만 호출하고, 헤더
struct나 화이트닝 로직을 직접 구현하지 않는다.

## 송신측 동작 요약 (`sender/encode.py` + `sender/display.py`)

1. `sender/encode.encode_file()`이 파일을 읽어 `common.lt_wrapper.LTEncoder(data, chunk_size=1024)`로
   감싸고, `(encoder, packets, filename)`을 반환한다(`filename`은 `Path(path).name`).
2. 전송 프레임 수 = `max(total_k, ceil(total_k * redundancy))` (`redundancy` 기본 1.5).
3. `seq = 0..count-1` 각각에 대해 `encoder.packet(seq)` → `common.qr_wire.pack_packet(packet, filename)`로
   직렬화(화이트닝 포함) → `sender/encode.make_qr_image()`(`qrcode`, ECC=M)로 QR 이미지 생성.
4. `sender/display.SenderApp`(생성자 `filename=` 인자로 받음)이 정사각형 캔버스에 맞춰
   리사이즈(`NEAREST`, 블러 방지)한 프레임을 `--fps` 간격으로 순환 표시하며, 하단에
   진행률 오버레이(`frame i/count | loop n | fps`)를 표시한다. 마지막 프레임 이후에는
   처음부터 다시 반복하여 수신 측이 여러 패스에 걸쳐 손실된 프레임을 보충할 수 있게 한다.

## 수신측 동작 요약 (`receiver/decode_video.py` + `receiver/verify.py`)

1. OpenCV로 영상에서 프레임을 순회하며 `zxing-cpp`(`read_barcodes`)로 QR 원시 바이트를 읽는다.
2. `common.qr_wire.unpack_packet()`으로 화이트닝 해제 + 파싱 → `(packet, filename)`.
   `packet`은 `common.lt_wrapper.LTDecoder.add_packet()`에 전달하고, 처음 수신된
   `filename`을 기억해둔다.
3. `decoder.complete`가 되면 `decoder.result()`로 **원본 파일 바이트만**(파일명 없이)
   복원한다.
4. 출력 경로는 `receiver/decode_video.resolve_output_path()`가 결정한다:
   - `--output` 생략 → 현재 폴더에 원본 파일명으로 저장.
   - `--output`이 기존 폴더(또는 `/`, `\`로 끝남) → 그 폴더 안에 원본 파일명으로 저장.
   - `--output`이 파일 경로 → 그 경로를 그대로 사용(사용자가 이름을 명시적으로 지정한 것으로 간주).
5. `receiver/verify.verify_and_report()`가 `common.hash_verify`로 SHA-256(원본 파일 내용
   기준)을 비교해 통과/실패를 출력한다.
