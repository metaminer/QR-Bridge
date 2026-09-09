package com.metaminer.qrbridge.validator.ui

import android.app.Application
import android.net.Uri
import android.provider.OpenableColumns
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.metaminer.qrbridge.validator.domain.ValidationCoordinator
import com.metaminer.qrbridge.validator.domain.ValidationState
import com.metaminer.qrbridge.validator.protocol.ProtocolSessionFactory
import com.metaminer.qrbridge.validator.protocol.Qrt2ProtocolSession
import com.metaminer.qrbridge.validator.qr.MlKitMultiQrScanner
import com.metaminer.qrbridge.validator.video.RetrieverVideoFrameSource
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class ValidatorViewModel(application: Application) : AndroidViewModel(application) {
    private val scanner = MlKitMultiQrScanner()
    private val coordinator = ValidationCoordinator(
        frameSource = RetrieverVideoFrameSource(application.contentResolver),
        scanner = scanner,
        protocolFactory = ProtocolSessionFactory { Qrt2ProtocolSession() },
    )
    private val _state = MutableStateFlow<ValidationState>(ValidationState.Idle)
    val state: StateFlow<ValidationState> = _state.asStateFlow()
    private var selectedUri: Uri? = null
    private var selectedName: String = "영상"
    private var validationJob: Job? = null

    fun select(uri: Uri) {
        validationJob?.cancel()
        selectedUri = uri
        selectedName = displayName(uri)
        _state.value = ValidationState.Ready(uri, selectedName)
    }

    fun start() {
        val uri = selectedUri ?: return
        validationJob?.cancel()
        validationJob = viewModelScope.launch {
            try {
                coordinator.validate(uri, selectedName).collect { _state.value = it }
            } catch (_: CancellationException) {
                _state.value = ValidationState.Ready(uri, selectedName)
            } catch (error: Exception) {
                _state.value = ValidationState.Error(selectedName, error.message ?: "알 수 없는 오류")
            }
        }
    }

    fun cancel() {
        validationJob?.cancel()
    }

    override fun onCleared() {
        scanner.close()
        super.onCleared()
    }

    private fun displayName(uri: Uri): String {
        getApplication<Application>().contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) return cursor.getString(index)
        }
        return uri.lastPathSegment ?: "영상"
    }
}
