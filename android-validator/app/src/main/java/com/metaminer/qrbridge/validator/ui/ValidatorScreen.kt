package com.metaminer.qrbridge.validator.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.metaminer.qrbridge.validator.camera.LiveCaptureRoute
import com.metaminer.qrbridge.validator.domain.ValidationProgress
import com.metaminer.qrbridge.validator.domain.ValidationState

@Composable
fun ValidatorRoute(viewModel: ValidatorViewModel = viewModel()) {
    val context = LocalContext.current
    var liveMode by rememberSaveable { mutableStateOf(false) }
    val permissions = remember {
        buildList {
            add(Manifest.permission.CAMERA)
            if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P) {
                add(Manifest.permission.WRITE_EXTERNAL_STORAGE)
            }
        }.toTypedArray()
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        if (permissions.all { grants[it] == true }) liveMode = true
    }
    if (liveMode) {
        LiveCaptureRoute(onBack = { liveMode = false })
        return
    }

    val state by viewModel.state.collectAsState()
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) viewModel.select(uri)
    }
    ValidatorScreen(
        state = state,
        onSelect = { picker.launch(arrayOf("video/mp4")) },
        onStart = viewModel::start,
        onCancel = viewModel::cancel,
        onLiveCapture = {
            if (permissions.all { permission ->
                    ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
                }
            ) {
                liveMode = true
            } else {
                permissionLauncher.launch(permissions)
            }
        },
    )
}

@Composable
private fun ValidatorScreen(
    state: ValidationState,
    onSelect: () -> Unit,
    onStart: () -> Unit,
    onCancel: () -> Unit,
    onLiveCapture: () -> Unit,
) {
    Scaffold { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(20.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("QR 촬영 검증", style = MaterialTheme.typography.headlineMedium)
            Text("PC로 옮기기 전에 촬영 영상만으로 파일 복원과 SHA-256 일치를 확인합니다.")
            Text("검증에 성공하면 필요한 구간만 남기고 선택한 MP4 원본 파일을 덮어씁니다.")

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = onSelect) { Text("영상 선택") }
                Button(onClick = onLiveCapture) { Text("실시간 촬영") }
                if (state is ValidationState.Ready || state is ValidationState.Insufficient || state is ValidationState.Error) {
                    Button(onClick = onStart) { Text("검증 시작") }
                }
                if (state is ValidationState.ReadingMetadata || state is ValidationState.Scanning || state is ValidationState.Trimming) {
                    OutlinedButton(onClick = onCancel) { Text("취소") }
                }
            }

            when (state) {
                ValidationState.Idle -> StatusCard("촬영 영상을 선택하세요.")
                is ValidationState.Ready -> StatusCard("선택됨: ${state.displayName}")
                is ValidationState.ReadingMetadata -> StatusCard("영상 정보를 읽는 중: ${state.displayName}")
                is ValidationState.Scanning -> ProgressCard(state.displayName, state.progress)
                is ValidationState.Trimming -> {
                    ProgressCard(state.displayName, state.progress)
                    StatusCard("검증 완료 · ${formatTimestamp(state.cutoffUs)} 이후 구간을 제거하는 중")
                }
                is ValidationState.Success -> SuccessCard(state)
                is ValidationState.Insufficient -> {
                    ProgressCard(state.displayName, state.progress)
                    StatusCard("복원 불충분\n${state.reason}", error = true)
                }
                is ValidationState.Error -> StatusCard("오류: ${state.message}", error = true)
            }
        }
    }
}

@Composable
private fun ProgressCard(name: String, progress: ValidationProgress) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(name, style = MaterialTheme.typography.titleMedium)
            Text("영상 처리")
            LinearProgressIndicator(progress = { progress.videoFraction }, modifier = Modifier.fillMaxWidth())
            progress.recoveryFraction?.let { fraction ->
                Text("LT 복원")
                LinearProgressIndicator(progress = { fraction }, modifier = Modifier.fillMaxWidth())
            }
            Text("검사 프레임: ${progress.framesInspected}")
            Text("QR 검출: ${progress.qrDetected}")
            Text("유효/중복 패킷: ${progress.validPackets} / ${progress.duplicatePackets}")
            Text("복원 블록: ${progress.recoveredBlocks} / ${progress.totalBlocks ?: "?"}")
            progress.filename?.let { Text("원본 파일: $it") }
        }
    }
}

@Composable
private fun SuccessCard(state: ValidationState.Success) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("검증 성공", style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.primary)
            Text("파일: ${state.filename}")
            Text("크기: ${state.fileSize} bytes")
            Text(
                "보존된 영상 구간: 00:00.0 ~ ${formatTimestamp(state.trimmedAtUs)} " +
                    "(전체 ${formatTimestamp(state.progress.durationUs)})",
            )
            if (state.progress.durationUs > 0) {
                val keptFraction = state.trimmedAtUs.toDouble() / state.progress.durationUs
                val savedPercent = ((1.0 - keptFraction) * 100).toInt().coerceIn(0, 100)
            Text("검증 완료 이후 구간을 원본 영상에서 제거했습니다. 예상 절감: $savedPercent%")
            }
            Text("SHA-256")
            Text(state.actualHash, style = MaterialTheme.typography.bodySmall)
            Text("촬영본을 PC로 옮겨도 됩니다.")
        }
    }
}

private fun formatTimestamp(timestampUs: Long): String {
    val totalTenths = (timestampUs.coerceAtLeast(0) + 50_000L) / 100_000L
    val minutes = totalTenths / 600
    val seconds = (totalTenths / 10) % 60
    val tenths = totalTenths % 10
    return "%02d:%02d.%d".format(minutes, seconds, tenths)
}

@Composable
private fun StatusCard(message: String, error: Boolean = false) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(
                message,
                color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
