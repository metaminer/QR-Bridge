"""QR Stream Transfer - receiver entry point.

Reads a video file (mp4/mov) captured by a smartphone camera, decodes QR
codes from each frame using zxing-cpp, feeds the recovered LT packets into
``common.lt_wrapper.LTDecoder``, reconstructs the original file, and verifies
integrity via ``receiver/verify.py``.

CLI:
    python receiver/decode_video.py --video <path> [--output <path or dir>]
    (or: python -m receiver.decode_video --video <path> [--output <path or dir>])

The original filename (and extension) travels inside every QR frame (see
docs/packet_spec.md), so --output is optional: if omitted, the file is
restored under its original name in the current directory. If --output
names an existing directory (or ends with a path separator), the original
name is used inside that directory. Otherwise --output is used verbatim.
"""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import struct
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.lt_wrapper import LTDecoder
from common.qr_wire import unpack_packet
from receiver.verify import verify_and_report


ProgressCallback = Callable[[int, Optional[int], int, int], None]
LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class DecodeResult:
    output_path: Path
    original_filename: str
    packets_received: int
    target_packets: int
    frames_processed: int
    frames_with_qr: int
    expected_hash: str
    actual_hash: str
    hash_ok: bool
    elapsed: float


def resolve_output_path(output_arg: Optional[Path | str], original_filename: str) -> Path:
    """Decide where to write the restored file.

    - No --output: use the original filename in the current directory.
    - --output names an existing directory (or ends with a path separator):
      use the original filename inside that directory.
    - --output names a file path: use it verbatim (explicit override).
    """
    if not original_filename:
        original_filename = "restored.bin"
    if output_arg is None:
        return Path(original_filename)
    output_path = Path(output_arg)
    looks_like_dir = str(output_arg).endswith(("/", "\\"))
    if output_path.is_dir() or looks_like_dir:
        return output_path / original_filename
    return output_path


def decode_qr_from_frame(frame) -> list[bytes]:
    """Find and decode all QR codes in a video frame.

    Uses zxing-cpp (native C++, same library swapped in on the sender side
    for rendering) rather than pyzbar/ZBar: benchmarked on real captured
    frames plus synthetic blur/rotation/downscale degradation, it matched or
    beat pyzbar on every case (notably +5/152 frames on a downscaled/low-res
    simulation) and decoded ~4x faster. Accepts the raw BGR frame directly —
    no color conversion needed. Uses ``.bytes`` (not ``.text``) so raw binary
    payloads are returned unmodified regardless of text-mode transcoding.
    """
    try:
        import zxingcpp
    except ImportError as error:
        raise RuntimeError("QR 디코딩에 zxing-cpp 패키지가 필요합니다") from error
    results = zxingcpp.read_barcodes(frame, formats=zxingcpp.BarcodeFormat.QRCode)
    return [bytes(result.bytes) for result in results]


def _decode_frame_worker(frame) -> list[bytes]:
    """Worker entry point for the decode thread pool (module level so the
    function also pickles, should a ProcessPoolExecutor ever be substituted).
    """
    return decode_qr_from_frame(frame)


def decode_video(
    video_path: Path | str,
    output_path: Optional[Path | str] = None,
    *,
    max_workers: int = 4,
    progress_callback: Optional[ProgressCallback] = None,
    log_callback: Optional[LogCallback] = None,
) -> DecodeResult:
    """Decode one video and write the recovered file.

    *output_path* is optional — see the module docstring for how it is
    resolved against the original filename carried in the QR frames.

    *max_workers* sizes the QR-decoding thread pool (see the comment below on
    why threads, not processes). Default 4 matched the benchmark hardware
    this was tuned on; faster/slower machines may benefit from a different
    value.

    This function contains no tkinter calls, so a GUI can safely run it in a
    worker thread. Callbacks execute on that same worker thread.
    """
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    from common.hash_verify import sha256_file

    video_path = Path(video_path)
    log = log_callback or (lambda _message: None)
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("영상 디코딩에 opencv-python 패키지가 필요합니다") from error
    if not video_path.is_file():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"영상을 열 수 없습니다: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    log(f"영상: {video_path.name} ({total_frames} frames, {fps:.1f} fps)")
    decoder = LTDecoder()
    frames_processed = frames_with_qr = packets_received = 0
    original_filename = ""
    start_time = time.time()

    # --- 병렬 QR 디코딩 (스레드풀) ---
    # 읽기(cv2.VideoCapture.read)는 메인 루프에서 순차적으로만 한다 — 객체 하나를
    # 여러 스레드에서 동시에 읽으면 안 된다. 디코딩(pyzbar.decode)만 워커로 넘긴다.
    #
    # 스레드풀 vs 프로세스풀: working/ 아래 임시 벤치(실측)로 결정했다.
    #  - 프로세스풀: 프레임당 수 MB numpy 배열을 pickle로 직렬화해 IPC로 보내야
    #    해 그 오버헤드가 병목. 벤치 결과: serial 7.5s > proc pool 3.3s.
    #  - 스레드풀: ZBar는 ctypes로 C 라이브러리를 호출하며 디코딩 중 GIL을
    #    해제하므로 다중 코어 병렬화가 실제로 동작. IPC 없이 프레임 참조만
    #    공유. 벤치: thread pool 2.7s (8/16 워커로 추가 이득 없음, 4에서 포화).
    # 따라서 스레드풀을 사용한다.
    try:
        import numpy as np
        _array_equal = np.array_equal
    except ImportError:
        # numpy 없이도 동작하게 fallback (cv2 의존성이라 사실상 항상 있음)
        def _array_equal(a, b):
            return a.shape == b.shape and (a == b).all()

    try:
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="qr-decode")
    except (OSError, PermissionError):
        executor = None

    prev_frame = None          # 직전 프레임 (중복 스킵용)
    pending: list[Future] = []  # 제출돼 결과 미수집인 디코딩 작업
    max_pending = max_workers * 4  # 읽기-디코딩 파이프라인의 최대 백로그

    def _collect(futures_list: list[Future]) -> None:
        """수집된 future의 QR 패킷을 디코더에 투입. 완료 시 조기 반환."""
        nonlocal frames_with_qr, packets_received, original_filename
        for future in futures_list:
            if decoder.complete:
                future.cancel()
                continue
            payloads = future.result()
            if payloads:
                frames_with_qr += 1
            for raw_payload in payloads:
                try:
                    packet, filename = unpack_packet(raw_payload)
                    decoder.add_packet(packet)
                    packets_received += 1
                    if filename and not original_filename:
                        original_filename = filename
                        log(f"원본 파일명 확인: {original_filename}")
                except (ValueError, struct.error):
                    log(f"프레임 {frames_processed}: 손상된 QR 패킷 건너뜀")
            if decoder.complete:
                break

    try:
        while True:
            if decoder.complete:
                break
            ok, frame = cap.read()
            if not ok:
                break
            frames_processed += 1
            # 중복 프레임 스킵: 폰 촬영 fps가 슬라이드쇼 fps보다 높아 같은 QR이
            # 연속 프레임에 반복되면, 직전 프레임과 *완전히* 동일한(정확 비교,
            # 근사 해시 아님 — 실제 다른 프레임을 잘못 스킵하는 것을 방지)
            # 프레임은 디코딩할 필요가 없다. 스킵된 프레임도 frames_processed
            #에는 이미 포함됐으므로 진행률 지표가 왜곡되지 않는다.
            if prev_frame is None or not _array_equal(frame, prev_frame):
                if executor is not None:
                    pending.append(executor.submit(_decode_frame_worker, frame))
                    if len(pending) >= max_pending:
                        _collect(pending)
                        pending.clear()
                else:
                    payloads = decode_qr_from_frame(frame)
                    if payloads:
                        frames_with_qr += 1
                    for raw_payload in payloads:
                        try:
                            packet, filename = unpack_packet(raw_payload)
                            decoder.add_packet(packet)
                            packets_received += 1
                            if filename and not original_filename:
                                original_filename = filename
                                log(f"원본 파일명 확인: {original_filename}")
                        except (ValueError, struct.error):
                            log(f"프레임 {frames_processed}: 손상된 QR 패킷 건너뜀")
            prev_frame = frame

            # 진행률 콜백은 메인 루프에서 계속 최신 값으로 호출한다 (UI 갱신용)
            target = decoder._total_k
            if progress_callback:
                progress_callback(packets_received, target, len(decoder._blocks), frames_processed)
    finally:
        # 남아 있는 진행 중 작업: 디코딩 완료됐으면 취소/폐기, 아니면 결과를
        # 마지막으로 수집한 뒤 풀을 종료한다.
        if executor is not None:
            if decoder.complete:
                for future in pending:
                    future.cancel()
            else:
                _collect(pending)
            executor.shutdown(wait=False, cancel_futures=True)
        pending.clear()
        cap.release()

    restored_blocks = len(decoder._blocks)
    target_blocks = decoder._total_k or 0
    restore_rate = (restored_blocks / target_blocks * 100) if target_blocks else 0.0

    if not decoder.complete:
        log(
            f"복원 실패 · 복원율 {restore_rate:.1f}% "
            f"({restored_blocks}/{target_blocks or '?'} 블록) · "
            f"수신 패킷 {packets_received} · "
            f"프레임 {frames_with_qr}/{frames_processed}"
        )
        raise ValueError(
            f"디코딩 불완전: {restored_blocks}/{target_blocks or '?'} 블록 복원, "
            f"{packets_received}개 패킷 수신"
        )

    data = decoder.result()
    resolved_output_path = resolve_output_path(output_path, original_filename)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_bytes(data)
    expected_hash = decoder._file_hash or ""
    actual_hash = sha256_file(resolved_output_path)
    elapsed = time.time() - start_time
    log(
        f"복원 완료 · 복원율 {restore_rate:.1f}% "
        f"({restored_blocks}/{target_blocks} 블록) · "
        f"수신 패킷 {packets_received} · "
        f"프레임 {frames_with_qr}/{frames_processed} · "
        f"소요시간 {elapsed:.1f}초"
    )
    return DecodeResult(
        output_path=resolved_output_path,
        original_filename=original_filename,
        packets_received=packets_received,
        target_packets=decoder._total_k or 0,
        frames_processed=frames_processed,
        frames_with_qr=frames_with_qr,
        expected_hash=expected_hash,
        actual_hash=actual_hash,
        hash_ok=actual_hash.lower() == expected_hash.lower(),
        elapsed=elapsed,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QR Stream Transfer - receiver")
    parser.add_argument("--video", required=True, help="촬영 영상 파일 경로 (mp4/mov)")
    parser.add_argument(
        "--output",
        required=False,
        default=None,
        help="복원할 출력 파일 경로 또는 폴더 (생략 시 원본 파일명으로 현재 폴더에 복원)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="QR 디코딩 스레드 풀 크기 (기본 4)",
    )
    args = parser.parse_args(argv)
    if args.threads <= 0:
        print("--threads는 양수여야 합니다", file=sys.stderr)
        return 1

    try:
        result = decode_video(
            args.video, args.output, max_workers=args.threads, log_callback=print
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"  ✗ {error}", file=sys.stderr)
        return 1
    print(f"  처리: {result.frames_processed} 프레임, {result.packets_received}개 패킷, {result.elapsed:.1f}s")
    print(f"  원본 파일명: {result.original_filename or '(알 수 없음)'}")
    print(f"  출력: {result.output_path}")
    if not verify_and_report(result.output_path, result.expected_hash):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
