package com.metaminer.qrbridge.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.util.Base64

class PacketFormatException(message: String, cause: Throwable? = null) : IllegalArgumentException(message, cause)

class Qrt2PacketParser(private val limits: ProtocolLimits = ProtocolLimits()) {
    fun parseBase64Ascii(payload: ByteArray): LtPacket {
        if (payload.isEmpty() || payload.size > limits.maxBase64PayloadBytes) {
            throw PacketFormatException("QR payload size is outside allowed range")
        }
        val whitened = try {
            Base64.getDecoder().decode(payload)
        } catch (error: IllegalArgumentException) {
            throw PacketFormatException("payload is not valid base64", error)
        }
        val decoded = whitened.copyOf()
        val random = PythonRandomCompat(WHITENING_SEED)
        for (index in decoded.indices) decoded[index] = (decoded[index].toInt() xor random.getRandBits(8).toInt()).toByte()
        return parseDecoded(decoded)
    }

    private fun parseDecoded(payload: ByteArray): LtPacket {
        if (payload.size < FIXED_HEADER_SIZE) throw PacketFormatException("payload too short")
        val buffer = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
        val magic = ByteArray(4).also(buffer::get)
        if (!magic.contentEquals(MAGIC)) throw PacketFormatException("unknown packet magic")
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
        private val MAGIC = "QRT2".toByteArray(StandardCharsets.US_ASCII)
        private const val FIXED_HEADER_SIZE = 50
        private const val WHITENING_SEED = 0x51515151L
    }
}

