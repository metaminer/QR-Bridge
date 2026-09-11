package com.metaminer.qrbridge.protocol

data class ProtocolLimits(
    val maxPayloadBytes: Int = 8 * 1024,
    val maxFilenameBytes: Int = 1024,
    val maxTotalK: Int = 100_000,
    val maxBlockBytes: Int = 4 * 1024,
    val maxPendingEquations: Int = 200_000,
)

