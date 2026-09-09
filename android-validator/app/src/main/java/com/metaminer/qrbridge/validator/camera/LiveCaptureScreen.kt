package com.metaminer.qrbridge.validator.camera

import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel

@Composable
fun LiveCaptureRoute(
    onBack: () -> Unit,
    viewModel: LiveCaptureViewModel = viewModel(),
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val state by viewModel.state.collectAsState()
    val previewView = remember {
        PreviewView(context).apply {
            scaleType = PreviewView.ScaleType.FIT_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
    }

    LaunchedEffect(previewView, lifecycleOwner) {
        viewModel.start(lifecycleOwner, previewView.surfaceProvider)
    }
    DisposableEffect(Unit) {
        onDispose { viewModel.leave() }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = {
                onBack()
            }) { Text("돌아가기") }
            if (state is LiveCaptureState.Recording || state is LiveCaptureState.Verified) {
                Button(onClick = viewModel::stop) { Text("촬영 종료") }
            }
        }
        Text("실시간 QR 촬영 검증", style = MaterialTheme.typography.headlineSmall)
        AndroidView(
            factory = { previewView },
            modifier = Modifier.fillMaxWidth().weight(1f).aspectRatio(16f / 9f),
        )
        LiveStatusCard(state)
    }
}

@Composable
private fun LiveStatusCard(state: LiveCaptureState) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            when (state) {
                LiveCaptureState.Idle -> Text("촬영 준비")
                LiveCaptureState.Preparing -> {
                    Text("카메라와 녹화를 준비하는 중")
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                }
                is LiveCaptureState.Recording -> ProgressText(state.progress, "촬영 및 검증 중")
                is LiveCaptureState.Verified -> {
                    ProgressText(state.progress, "SHA-256 검증 성공")
                    Text("마지막 프레임 보존을 위해 1초 후 자동 종료합니다.")
                }
                is LiveCaptureState.Saved -> {
                    ProgressText(
                        state.progress,
                        if (state.verifiedFile != null) "검증 성공 · 촬영 완료" else "촬영 종료 · 복원 미완료",
                    )
                    Text("저장 위치: ${state.uri}", style = MaterialTheme.typography.bodySmall)
                }
                is LiveCaptureState.Error -> Text(
                    "오류: ${state.message}",
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

@Composable
private fun ProgressText(progress: LiveCaptureProgress, title: String) {
    val protocol = progress.protocol
    Text(title, style = MaterialTheme.typography.titleMedium)
    Text("녹화: ${progress.elapsedNanos / 1_000_000_000L}초 · ${progress.bytesRecorded / 1_048_576L}MB")
    Text("분석 프레임: ${progress.framesAnalyzed} · QR: ${progress.qrDetected}")
    Text("유효/중복 패킷: ${protocol.validPackets} / ${protocol.duplicatePackets}")
    Text("LT 복원: ${protocol.recoveredBlocks} / ${protocol.totalBlocks ?: "?"}")
    protocol.totalBlocks?.takeIf { it > 0 }?.let { total ->
        LinearProgressIndicator(
            progress = { (protocol.recoveredBlocks.toFloat() / total).coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth(),
        )
    }
    protocol.filename?.let { Text("파일: $it") }
}
