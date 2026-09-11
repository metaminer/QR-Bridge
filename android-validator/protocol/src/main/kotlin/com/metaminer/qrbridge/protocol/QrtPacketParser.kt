package com.metaminer.qrbridge.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.util.Base64

class PacketFormatException(message: String, cause: Throwable? = null) : IllegalArgumentException(message, cause)

/**
 * Parses one QR frame's payload into an [LtPacket].
 *
 * QRT3 frames carry the whitened bytes raw; QRT2 frames wrapped them in
 * base64 first. Both are accepted, so a build of this app can validate video
 * captured from either sender. The layout behind the magic is identical —
 * see docs/packet_spec.md.
 */
class QrtPacketParser(private val limits: ProtocolLimits = ProtocolLimits()) {
    /** Parse a QR payload of either wire version, detecting which it is. */
    fun parse(payload: ByteArray): LtPacket {
        if (payload.isEmpty() || payload.size > limits.maxPayloadBytes) {
            throw PacketFormatException("QR payload size is outside allowed range")
        }
        return parseDecoded(unwhiten(stripArmour(payload)))
    }

    /**
     * QRT3 payloads start with the fixed whitened magic. A QRT2 payload is
     * base64 ASCII and cannot begin with those bytes, so the prefix alone
     * separates the two without having to un-whiten first.
     */
    private fun stripArmour(payload: ByteArray): ByteArray {
        if (payload.size >= WHITENED_MAGIC.size &&
            payload.copyOf(WHITENED_MAGIC.size).contentEquals(WHITENED_MAGIC)
        ) {
            return payload
        }
        return try {
            Base64.getDecoder().decode(payload)
        } catch (error: IllegalArgumentException) {
            throw PacketFormatException("payload is neither a QRT3 frame nor valid base64", error)
        }
    }

    private fun unwhiten(whitened: ByteArray): ByteArray {
        val decoded = whitened.copyOf()
        val random = PythonRandomCompat(WHITENING_SEED)
        for (index in decoded.indices) {
            decoded[index] = (decoded[index].toInt() xor random.getRandBits(8).toInt()).toByte()
        }
        return decoded
    }

    private fun parseDecoded(payload: ByteArray): LtPacket {
        if (payload.size < FIXED_HEADER_SIZE) throw PacketFormatException("payload too short")
        val buffer = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
        val magic = ByteArray(4).also(buffer::get)
        if (!magic.contentEquals(MAGIC) && !magic.contentEquals(LEGACY_MAGIC)) {
            throw PacketFormatException("unknown packet magic")
        }
        val seq = buffer.int.toLong() and 0xffff_ffffL
        val seed = buffer.int.toLong() and 0xffff_ffffL
        val totalKLong = buffer.int.toLong() and 0xffff_ffffL
        if (totalKLong !in 1..limits.maxTotalK.toLong()) throw PacketFormatException("total_k is outside allowed range")
        val hash = ByteArray(32).also(buffer::get)
        val filenameLength = buffer.short.toInt() and 0xffff
        if (filenameLength > limits.maxFilenameBytes || buffer.remaining() < filenameLength + 2) {
            throw PacketFormatException("invalid filename length")
        }
        val filenameBytes = ByteArray(filenameLength).also(buffer::get)
        val filename = try {
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(filenameBytes)).toString()
        } catch (error: CharacterCodingException) {
            throw PacketFormatException("filename is not valid UTF-8", error)
        }
        val dataLength = buffer.short.toInt() and 0xffff
        if (dataLength !in 1..limits.maxBlockBytes || buffer.remaining() < dataLength) {
            throw PacketFormatException("invalid packet data length")
        }
        val data = ByteArray(dataLength).also(buffer::get)
        return LtPacket(seq, seed, data, totalKLong.toInt(), hash, filename)
    }

    companion object {
        private val MAGIC = "QRT3".toByteArray(StandardCharsets.US_ASCII)
        private val LEGACY_MAGIC = "QRT2".toByteArray(StandardCharsets.US_ASCII)
        private const val FIXED_HEADER_SIZE = 50
        private const val WHITENING_SEED = 0x51515151L

        /** [MAGIC] run through the whitening keystream — the QRT3 frame prefix. */
        private val WHITENED_MAGIC: ByteArray = run {
            val random = PythonRandomCompat(WHITENING_SEED)
            ByteArray(MAGIC.size) { index -> (MAGIC[index].toInt() xor random.getRandBits(8).toInt()).toByte() }
        }
    }
}
