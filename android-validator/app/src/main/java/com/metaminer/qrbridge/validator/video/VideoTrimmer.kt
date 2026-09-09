package com.metaminer.qrbridge.validator.video

import android.content.ContentResolver
import android.content.Context
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer

interface VideoTrimmer {
    /** Replaces [uri] with an MP4 containing samples from the start through [cutoffUs]. */
    suspend fun trimInPlace(uri: Uri, cutoffUs: Long): Long
}

class Mp4VideoTrimmer(
    private val context: Context,
    private val contentResolver: ContentResolver,
) : VideoTrimmer {
    override suspend fun trimInPlace(uri: Uri, cutoffUs: Long): Long = withContext(Dispatchers.IO) {
        require(cutoffUs > 0) { "영상 절단 시점이 올바르지 않습니다" }
        val mime = contentResolver.getType(uri)
        require(mime == null || mime == "video/mp4" || mime == "application/mp4") {
            "현재 파일 덮어쓰기는 MP4 영상만 지원합니다: $mime"
        }

        val temporary = File.createTempFile("qrbridge-verified-", ".mp4", context.cacheDir)
        try {
            remuxTo(uri, temporary, cutoffUs)
            verifyOutput(temporary)
            replaceOriginal(uri, temporary)
            cutoffUs
        } finally {
            temporary.delete()
        }
    }

    private fun remuxTo(source: Uri, output: File, cutoffUs: Long) {
        val descriptor = contentResolver.openAssetFileDescriptor(source, "r")
            ?: error("선택한 영상을 다시 열 수 없습니다")
        descriptor.use { afd ->
            val extractor = MediaExtractor()
            var muxer: MediaMuxer? = null
            try {
                if (afd.length >= 0L) {
                    extractor.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
                } else {
                    extractor.setDataSource(afd.fileDescriptor)
                }
                muxer = MediaMuxer(output.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
                readRotation(source)?.let(muxer::setOrientationHint)

                val trackMap = mutableMapOf<Int, Int>()
                var maxInputSize = 1 shl 20
                for (track in 0 until extractor.trackCount) {
                    val format = extractor.getTrackFormat(track)
                    val trackMime = format.getString(MediaFormat.KEY_MIME).orEmpty()
                    if (!trackMime.startsWith("video/") && !trackMime.startsWith("audio/")) continue
                    extractor.selectTrack(track)
                    trackMap[track] = muxer.addTrack(format)
                    if (format.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) {
                        maxInputSize = maxOf(maxInputSize, format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE))
                    }
                }
                require(trackMap.isNotEmpty()) { "복사 가능한 영상/음성 트랙이 없습니다" }
                require(maxInputSize <= MAX_SAMPLE_BYTES) { "영상 sample 크기가 제한을 초과합니다" }

                val buffer = ByteBuffer.allocateDirect(maxInputSize)
                val info = MediaCodec.BufferInfo()
                muxer.start()
                while (true) {
                    val sourceTrack = extractor.sampleTrackIndex
                    if (sourceTrack < 0) break
                    val sampleTime = extractor.sampleTime
                    if (sampleTime < 0 || sampleTime > cutoffUs) break
                    val targetTrack = trackMap[sourceTrack]
                    if (targetTrack != null) {
                        buffer.clear()
                        val size = extractor.readSampleData(buffer, 0)
                        if (size < 0) break
                        info.set(0, size, sampleTime, extractor.sampleFlags)
                        muxer.writeSampleData(targetTrack, buffer, info)
                    }
                    if (!extractor.advance()) break
                }
                muxer.stop()
            } finally {
                runCatching { muxer?.release() }
                extractor.release()
            }
        }
    }

    private fun readRotation(uri: Uri): Int? {
        val descriptor = contentResolver.openAssetFileDescriptor(uri, "r") ?: return null
        return descriptor.use { afd ->
            val retriever = MediaMetadataRetriever()
            try {
                if (afd.length >= 0L) {
                    retriever.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
                } else {
                    retriever.setDataSource(afd.fileDescriptor)
                }
                retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull()
            } finally {
                retriever.release()
            }
        }
    }

    private fun verifyOutput(file: File) {
        require(file.length() > 0) { "잘라낸 영상이 비어 있습니다" }
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(file.absolutePath)
            val durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: 0L
            require(durationMs > 0) { "잘라낸 영상을 다시 열 수 없습니다" }
        } finally {
            retriever.release()
        }
    }

    private fun replaceOriginal(uri: Uri, source: File) {
        val descriptor = contentResolver.openFileDescriptor(uri, "rwt")
            ?: error("원본 영상에 쓸 수 없습니다")
        descriptor.use { target ->
            FileOutputStream(target.fileDescriptor).use { output ->
                source.inputStream().buffered().use { input -> input.copyTo(output) }
                output.fd.sync()
            }
        }
    }

    companion object {
        private const val MAX_SAMPLE_BYTES = 64 * 1024 * 1024
    }
}
