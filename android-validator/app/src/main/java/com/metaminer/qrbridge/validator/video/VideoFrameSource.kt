package com.metaminer.qrbridge.validator.video

import android.content.ContentResolver
import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.net.Uri
import com.metaminer.qrbridge.validator.domain.VideoMetadata
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import java.io.Closeable
import kotlin.coroutines.coroutineContext

data class VideoFrame(val bitmap: Bitmap, val timestampUs: Long) : Closeable {
    override fun close() = bitmap.recycle()
}

interface VideoFrameSource {
    suspend fun metadata(uri: Uri): VideoMetadata
    fun frames(uri: Uri, sampleFps: Int): Flow<VideoFrame>
}

/**
 * Bounded-memory frame source. Frames are emitted one at a time and ownership passes to the caller.
 * MediaCodec sequential decoding can replace this class without changing the validation pipeline.
 */
class RetrieverVideoFrameSource(
    private val contentResolver: ContentResolver,
) : VideoFrameSource {
    override suspend fun metadata(uri: Uri): VideoMetadata = withRetriever(uri) { retriever ->
        val durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
            ?.toLongOrNull() ?: 0L
        val width = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)
            ?.toIntOrNull() ?: 0
        val height = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)
            ?.toIntOrNull() ?: 0
        val fps = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_CAPTURE_FRAMERATE)
            ?.toFloatOrNull()
        VideoMetadata(durationMs * 1_000L, width, height, fps)
    }

    override fun frames(uri: Uri, sampleFps: Int): Flow<VideoFrame> = flow {
        require(sampleFps in 1..60) { "sampleFps must be between 1 and 60" }
        withRetriever(uri) { retriever ->
            val durationUs = (retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: 0L) * 1_000L
            val stepUs = 1_000_000L / sampleFps
            var timestampUs = 0L
            while (timestampUs <= durationUs) {
                coroutineContext.ensureActive()
                val bitmap = retriever.getFrameAtTime(
                    timestampUs,
                    MediaMetadataRetriever.OPTION_CLOSEST,
                )
                if (bitmap != null) emit(VideoFrame(bitmap, timestampUs))
                timestampUs += stepUs
            }
        }
    }.flowOn(Dispatchers.IO)

    private inline fun <T> withRetriever(uri: Uri, block: (MediaMetadataRetriever) -> T): T {
        val descriptor = contentResolver.openAssetFileDescriptor(uri, "r")
            ?: error("선택한 영상을 열 수 없습니다")
        return descriptor.use { afd ->
            val retriever = MediaMetadataRetriever()
            try {
                if (afd.length >= 0L) {
                    retriever.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
                } else {
                    retriever.setDataSource(afd.fileDescriptor)
                }
                block(retriever)
            } finally {
                retriever.release()
            }
        }
    }
}
