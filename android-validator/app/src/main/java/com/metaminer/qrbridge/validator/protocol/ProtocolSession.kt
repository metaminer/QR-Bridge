package com.metaminer.qrbridge.validator.protocol

import com.metaminer.qrbridge.protocol.AddPacketResult
import com.metaminer.qrbridge.protocol.LtDecoder
import com.metaminer.qrbridge.protocol.PacketFormatException
import com.metaminer.qrbridge.protocol.Qrt2PacketParser
import com.metaminer.qrbridge.protocol.Sha256Verifier

/** Boundary between Android video/QR processing and the pure Kotlin QRT2/LT module. */
interface ProtocolSession {
    val snapshot: ProtocolSnapshot

    /** Adds one QR payload. Invalid payloads are reported without aborting video scanning. */
    fun addQrPayload(payload: ByteArray): PacketOutcome

    /** Returns a verified result only after LT recovery and SHA-256 verification both succeed. */
    fun verifiedResultOrNull(): VerifiedFile?
}

data class ProtocolSnapshot(
    val validPackets: Int = 0,
    val duplicatePackets: Int = 0,
    val recoveredBlocks: Int = 0,
    val totalBlocks: Int? = null,
    val filename: String? = null,
)

sealed interface PacketOutcome {
    data object Accepted : PacketOutcome
    data object Duplicate : PacketOutcome
    data class Rejected(val reason: String) : PacketOutcome
}

data class VerifiedFile(
    val filename: String,
    val bytes: ByteArray,
    val expectedHash: String,
    val actualHash: String,
)

fun interface ProtocolSessionFactory {
    fun create(): ProtocolSession
}

class Qrt2ProtocolSession : ProtocolSession {
    private val parser = Qrt2PacketParser()
    private val decoder = LtDecoder()
    private var duplicates = 0
    private var verified: VerifiedFile? = null

    override val snapshot: ProtocolSnapshot
        get() = ProtocolSnapshot(
            validPackets = decoder.validPackets,
            duplicatePackets = duplicates,
            recoveredBlocks = decoder.recoveredBlocks,
            totalBlocks = decoder.totalK,
            filename = decoder.filename,
        )

    override fun addQrPayload(payload: ByteArray): PacketOutcome {
        return try {
            when (decoder.add(parser.parseBase64Ascii(payload))) {
                AddPacketResult.Duplicate -> {
                    duplicates += 1
                    PacketOutcome.Duplicate
                }
                AddPacketResult.Accepted -> PacketOutcome.Accepted
                AddPacketResult.Complete -> {
                    buildVerifiedResult()
                    PacketOutcome.Accepted
                }
            }
        } catch (error: PacketFormatException) {
            PacketOutcome.Rejected(error.message ?: "손상된 QRT2 패킷")
        }
    }

    override fun verifiedResultOrNull(): VerifiedFile? = verified

    private fun buildVerifiedResult() {
        val bytes = decoder.verifiedResult()
        val expected = decoder.expectedHash() ?: error("완료된 스트림에 SHA-256이 없습니다")
        val actual = Sha256Verifier.digest(bytes)
        verified = VerifiedFile(
            filename = decoder.filename.orEmpty().ifEmpty { "restored.bin" },
            bytes = bytes,
            expectedHash = Sha256Verifier.hex(expected),
            actualHash = Sha256Verifier.hex(actual),
        )
    }
}
