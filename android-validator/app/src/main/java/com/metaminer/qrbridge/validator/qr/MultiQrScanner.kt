package com.metaminer.qrbridge.validator.qr

import android.graphics.Bitmap
import com.google.android.gms.tasks.Task
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.Closeable
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

interface MultiQrScanner : Closeable {
    suspend fun scan(bitmap: Bitmap): List<ByteArray>
}

class MlKitMultiQrScanner : MultiQrScanner {
    private val scanner: BarcodeScanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
            .build(),
    )

    override suspend fun scan(bitmap: Bitmap): List<ByteArray> {
        val barcodes = scanner.process(InputImage.fromBitmap(bitmap, 0)).await()
        return barcodes.mapNotNull(::payloadOf).distinctBy { it.contentHashCode() }
    }

    /**
     * QRT3 frames are raw bytes, so [Barcode.getRawBytes] is the only source
     * that can carry them: [Barcode.getRawValue] is a String ML Kit decoded
     * from those bytes, and that conversion is lossy for non-text payloads.
     * The rawValue path therefore stays a QRT2-only fallback — QRT2 frames
     * are base64 ASCII and survive it intact.
     */
    private fun payloadOf(barcode: Barcode): ByteArray? {
        barcode.rawBytes?.let { return it }
        val text = barcode.rawValue ?: return null
        return if (text.all { it.code in 0x20..0x7e }) text.toByteArray(Charsets.US_ASCII) else null
    }

    override fun close() = scanner.close()
}

private suspend fun <T> Task<T>.await(): T = suspendCancellableCoroutine { continuation ->
    addOnSuccessListener { result ->
        if (continuation.isActive) continuation.resume(result)
    }
    addOnFailureListener { error ->
        if (continuation.isActive) continuation.resumeWithException(error)
    }
    addOnCanceledListener { continuation.cancel() }
}

