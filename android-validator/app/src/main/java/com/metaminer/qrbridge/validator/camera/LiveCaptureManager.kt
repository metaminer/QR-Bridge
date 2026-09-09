package com.metaminer.qrbridge.validator.camera

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.MediaStoreOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.metaminer.qrbridge.validator.protocol.ProtocolSession
import com.metaminer.qrbridge.validator.protocol.ProtocolSessionFactory
import com.metaminer.qrbridge.validator.protocol.ProtocolSnapshot
import com.metaminer.qrbridge.validator.protocol.VerifiedFile
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

data class LiveCaptureProgress(
    val elapsedNanos: Long = 0,
    val bytesRecorded: Long = 0,
    val framesAnalyzed: Int = 0,
    val qrDetected: Int = 0,
    val protocol: ProtocolSnapshot = ProtocolSnapshot(),
)

sealed interface LiveCaptureState {
    data object Idle : LiveCaptureState
    data object Preparing : LiveCaptureState
    data class Recording(val progress: LiveCaptureProgress) : LiveCaptureState
    data class Verified(val progress: LiveCaptureProgress) : LiveCaptureState
    data class Saved(
        val uri: String,
        val progress: LiveCaptureProgress,
        val verifiedFile: VerifiedFile?,
    ) : LiveCaptureState
    data class Error(val message: String) : LiveCaptureState
}

class LiveCaptureManager(
    private val context: Context,
    private val protocolFactory: ProtocolSessionFactory,
) : AutoCloseable {
    private val mainExecutor = ContextCompat.getMainExecutor(context)
    private val analysisExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val processingFrame = AtomicBoolean(false)
    private val autoStopScheduled = AtomicBoolean(false)
    private val captureActive = AtomicBoolean(false)
    private val scanner: BarcodeScanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build(),
    )
    private val _state = MutableStateFlow<LiveCaptureState>(LiveCaptureState.Idle)
    val state: StateFlow<LiveCaptureState> = _state.asStateFlow()

    private var cameraProvider: ProcessCameraProvider? = null
    private var recording: Recording? = null
    private var session: ProtocolSession = protocolFactory.create()
    private var progress = LiveCaptureProgress()
    private var verifiedFile: VerifiedFile? = null

    fun start(lifecycleOwner: LifecycleOwner, surfaceProvider: Preview.SurfaceProvider) {
        if (recording != null || _state.value == LiveCaptureState.Preparing) return
        _state.value = LiveCaptureState.Preparing
        session = protocolFactory.create()
        progress = LiveCaptureProgress()
        verifiedFile = null
        autoStopScheduled.set(false)
        captureActive.set(true)

        val providerFuture = ProcessCameraProvider.getInstance(context)
        providerFuture.addListener({
            try {
                if (!captureActive.get()) return@addListener
                val provider = providerFuture.get()
                cameraProvider = provider
                provider.unbindAll()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(surfaceProvider) }
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(
                        ResolutionSelector.Builder()
                            .setResolutionStrategy(
                                ResolutionStrategy(
                                    Size(1920, 1080),
                                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER,
                                ),
                            )
                            .build(),
                    )
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { it.setAnalyzer(analysisExecutor, ::analyze) }
                val qualitySelector = QualitySelector.fromOrderedList(
                    listOf(Quality.UHD, Quality.FHD, Quality.HD),
                    FallbackStrategy.lowerQualityOrHigherThan(Quality.HD),
                )
                val videoCapture = VideoCapture.withOutput(
                    Recorder.Builder().setQualitySelector(qualitySelector).build(),
                )
                provider.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    videoCapture,
                    analysis,
                )
                beginRecording(videoCapture)
            } catch (error: Exception) {
                _state.value = LiveCaptureState.Error(error.message ?: "카메라를 시작할 수 없습니다")
            }
        }, mainExecutor)
    }

    fun stop() {
        captureActive.set(false)
        recording?.stop()
        recording = null
    }

    fun leave() {
        stop()
        cameraProvider?.unbindAll()
    }

    private fun beginRecording(videoCapture: VideoCapture<Recorder>) {
        val name = "QRBridge-" + SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
            .format(System.currentTimeMillis()) + ".mp4"
        val values = ContentValues().apply {
            put(MediaStore.Video.Media.DISPLAY_NAME, name)
            put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(MediaStore.Video.Media.RELATIVE_PATH, "Movies/QRBridge")
            }
        }
        val output = MediaStoreOutputOptions.Builder(
            context.contentResolver,
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
        ).setContentValues(values).build()
        recording = videoCapture.output.prepareRecording(context, output)
            .start(mainExecutor, ::onVideoEvent)
    }

    private fun analyze(imageProxy: ImageProxy) {
        if (!captureActive.get() || !processingFrame.compareAndSet(false, true)) {
            imageProxy.close()
            return
        }
        val mediaImage = imageProxy.image
        if (mediaImage == null) {
            processingFrame.set(false)
            imageProxy.close()
            return
        }
        val input = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
        scanner.process(input)
            .addOnSuccessListener(analysisExecutor) { barcodes ->
                var detected = 0
                barcodes.forEach { barcode ->
                    val payload = barcode.rawBytes ?: barcode.rawValue?.toByteArray(Charsets.US_ASCII)
                    if (payload != null) {
                        detected += 1
                        session.addQrPayload(payload)
                    }
                }
                progress = progress.copy(
                    framesAnalyzed = progress.framesAnalyzed + 1,
                    qrDetected = progress.qrDetected + detected,
                    protocol = session.snapshot,
                )
                val result = session.verifiedResultOrNull()
                if (result != null && autoStopScheduled.compareAndSet(false, true)) {
                    verifiedFile = result
                    _state.value = LiveCaptureState.Verified(progress)
                    mainHandler.postDelayed({ stop() }, AUTO_STOP_DELAY_MS)
                } else if (result == null) {
                    _state.value = LiveCaptureState.Recording(progress)
                }
            }
            .addOnFailureListener { error ->
                _state.value = LiveCaptureState.Error(error.message ?: "실시간 QR 분석에 실패했습니다")
            }
            .addOnCompleteListener {
                processingFrame.set(false)
                imageProxy.close()
            }
    }

    private fun onVideoEvent(event: VideoRecordEvent) {
        when (event) {
            is VideoRecordEvent.Start -> _state.value = LiveCaptureState.Recording(progress)
            is VideoRecordEvent.Status -> {
                progress = progress.copy(
                    elapsedNanos = event.recordingStats.recordedDurationNanos,
                    bytesRecorded = event.recordingStats.numBytesRecorded,
                )
                if (_state.value !is LiveCaptureState.Verified) {
                    _state.value = LiveCaptureState.Recording(progress)
                }
            }
            is VideoRecordEvent.Finalize -> {
                recording = null
                if (event.hasError()) {
                    _state.value = LiveCaptureState.Error("영상 저장 실패: ${event.error}")
                } else {
                    _state.value = LiveCaptureState.Saved(
                        uri = event.outputResults.outputUri.toString(),
                        progress = progress,
                        verifiedFile = verifiedFile,
                    )
                }
            }
        }
    }

    override fun close() {
        mainHandler.removeCallbacksAndMessages(null)
        recording?.stop()
        recording = null
        cameraProvider?.unbindAll()
        scanner.close()
        analysisExecutor.shutdown()
    }

    companion object {
        private const val AUTO_STOP_DELAY_MS = 1_000L
    }
}
