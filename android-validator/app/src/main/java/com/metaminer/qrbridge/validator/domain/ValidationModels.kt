package com.metaminer.qrbridge.validator.domain

import android.net.Uri

data class VideoMetadata(
    val durationUs: Long,
    val width: Int,
    val height: Int,
    val capturedFrameRate: Float?,
)

data class ValidationProgress(
    val positionUs: Long = 0,
    val durationUs: Long = 0,
    val framesInspected: Int = 0,
    val qrDetected: Int = 0,
    val validPackets: Int = 0,
    val duplicatePackets: Int = 0,
    val recoveredBlocks: Int = 0,
    val totalBlocks: Int? = null,
    val filename: String? = null,
) {
    val videoFraction: Float
        get() = if (durationUs <= 0) 0f else (positionUs.toDouble() / durationUs).toFloat().coerceIn(0f, 1f)

    val recoveryFraction: Float?
        get() = totalBlocks?.takeIf { it > 0 }?.let {
            (recoveredBlocks.toFloat() / it).coerceIn(0f, 1f)
        }
}

sealed interface ValidationState {
    data object Idle : ValidationState
    data class Ready(val uri: Uri, val displayName: String) : ValidationState
    data class ReadingMetadata(val displayName: String) : ValidationState
    data class Scanning(val displayName: String, val progress: ValidationProgress) : ValidationState
    data class Trimming(
        val displayName: String,
        val progress: ValidationProgress,
        val cutoffUs: Long,
    ) : ValidationState
    data class Success(
        val displayName: String,
        val filename: String,
        val fileSize: Long,
        val expectedHash: String,
        val actualHash: String,
        val progress: ValidationProgress,
        val trimmedAtUs: Long,
    ) : ValidationState

    data class Insufficient(
        val displayName: String,
        val progress: ValidationProgress,
        val reason: String,
    ) : ValidationState

    data class Error(val displayName: String?, val message: String) : ValidationState
}
