package com.metaminer.qrbridge.validator.camera

import android.app.Application
import androidx.camera.core.Preview
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LifecycleOwner
import com.metaminer.qrbridge.validator.protocol.ProtocolSessionFactory
import com.metaminer.qrbridge.validator.protocol.Qrt2ProtocolSession
import kotlinx.coroutines.flow.StateFlow

class LiveCaptureViewModel(application: Application) : AndroidViewModel(application) {
    private val manager = LiveCaptureManager(
        context = application,
        protocolFactory = ProtocolSessionFactory { Qrt2ProtocolSession() },
    )
    val state: StateFlow<LiveCaptureState> = manager.state

    fun start(lifecycleOwner: LifecycleOwner, surfaceProvider: Preview.SurfaceProvider) {
        manager.start(lifecycleOwner, surfaceProvider)
    }

    fun stop() = manager.stop()
    fun leave() = manager.leave()

    override fun onCleared() {
        manager.close()
        super.onCleared()
    }
}
