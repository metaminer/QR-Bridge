package com.metaminer.qrbridge.validator.domain

import android.net.Uri
import com.metaminer.qrbridge.validator.protocol.ProtocolSessionFactory
import com.metaminer.qrbridge.validator.protocol.VerifiedFile
import com.metaminer.qrbridge.validator.qr.MultiQrScanner
import com.metaminer.qrbridge.validator.video.VideoFrameSource
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

class ValidationCoordinator(
    private val frameSource: VideoFrameSource,
    private val scanner: MultiQrScanner,
    private val protocolFactory: ProtocolSessionFactory,
) {
    fun validate(uri: Uri, displayName: String, sampleFps: Int = 15): Flow<ValidationState> = flow {
        emit(ValidationState.ReadingMetadata(displayName))
        val metadata = frameSource.metadata(uri)
        val session = protocolFactory.create()
        var frames = 0
        var qrDetected = 0
        var lastPositionUs = 0L

        try {
            frameSource.frames(uri, sampleFps).collect { frame ->
                frame.use {
                    lastPositionUs = frame.timestampUs
                    frames += 1
                    val payloads = scanner.scan(frame.bitmap)
                    qrDetected += payloads.size
                    payloads.forEach(session::addQrPayload)

                    val snapshot = session.snapshot
                    val progress = ValidationProgress(
                        positionUs = lastPositionUs,
                        durationUs = metadata.durationUs,
                        framesInspected = frames,
                        qrDetected = qrDetected,
                        validPackets = snapshot.validPackets,
                        duplicatePackets = snapshot.duplicatePackets,
                        recoveredBlocks = snapshot.recoveredBlocks,
                        totalBlocks = snapshot.totalBlocks,
                        filename = snapshot.filename,
                    )
                    emit(ValidationState.Scanning(displayName, progress))

                    session.verifiedResultOrNull()?.let { result ->
                        throw VerificationFinished(result, progress)
                    }
                }
            }
        } catch (finished: VerificationFinished) {
            emit(
                ValidationState.Success(
                    displayName = displayName,
                    filename = finished.result.filename,
                    fileSize = finished.result.bytes.size.toLong(),
                    expectedHash = finished.result.expectedHash,
                    actualHash = finished.result.actualHash,
                    progress = finished.progress,
                ),
            )
            return@flow
        }

        val snapshot = session.snapshot
        emit(
            ValidationState.Insufficient(
                displayName = displayName,
                progress = ValidationProgress(
                    positionUs = lastPositionUs,
                    durationUs = metadata.durationUs,
                    framesInspected = frames,
                    qrDetected = qrDetected,
                    validPackets = snapshot.validPackets,
                    duplicatePackets = snapshot.duplicatePackets,
                    recoveredBlocks = snapshot.recoveredBlocks,
                    totalBlocks = snapshot.totalBlocks,
                    filename = snapshot.filename,
                ),
                reason = if (qrDetected == 0) {
                    "QR을 찾지 못했습니다. 화면 점유율, 초점, 흔들림과 노출을 확인하세요."
                } else {
                    "영상 끝까지 처리했지만 SHA-256 검증까지 완료되지 않았습니다."
                },
            ),
        )
    }

    private class VerificationFinished(
        val result: VerifiedFile,
        val progress: ValidationProgress,
    ) : RuntimeException(null, null, false, false)
}
