package com.metaminer.qrbridge.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.ArrayDeque

sealed interface AddPacketResult {
    data object Accepted : AddPacketResult
    data object Duplicate : AddPacketResult
    data object Complete : AddPacketResult
}

class LtDecoder(private val limits: ProtocolLimits = ProtocolLimits()) {
    private data class Equation(val indices: MutableSet<Int>, val payload: ByteArray)

    private val equations = mutableListOf<Equation>()
    private val seen = mutableSetOf<Long>()
    private val blocks = mutableMapOf<Int, ByteArray>()
    private var streamHash: ByteArray? = null
    private var chunkSize: Int? = null
    var totalK: Int? = null
        private set
    var filename: String? = null
        private set

    val recoveredBlocks: Int get() = blocks.size
    val validPackets: Int get() = seen.size
    val complete: Boolean get() = totalK != null && blocks.size == totalK

    fun add(packet: LtPacket): AddPacketResult {
        if (packet.seq in seen) return AddPacketResult.Duplicate
        if (totalK == null) {
            totalK = packet.totalK
            streamHash = packet.fileHash.copyOf()
            chunkSize = packet.data.size
            filename = packet.filename
        } else if (
            packet.totalK != totalK || packet.data.size != chunkSize ||
            !packet.fileHash.contentEquals(streamHash) || packet.filename != filename
        ) {
            throw PacketFormatException("packet belongs to a different LT stream")
        }
        if (equations.size >= limits.maxPendingEquations) throw PacketFormatException("too many LT equations")

        seen += packet.seq
        val indices = sourceIndices(packet.totalK, packet.seq, packet.seed)
        val payload = packet.data.copyOf()
        indices.toList().forEach { index ->
            blocks[index]?.let { block ->
                xorInto(payload, block)
                indices.remove(index)
            }
        }
        equations += Equation(indices, payload)
        peel()
        return if (complete) AddPacketResult.Complete else AddPacketResult.Accepted
    }

    fun verifiedResult(): ByteArray {
        if (!complete) throw IllegalStateException("not enough independent packets")
        val k = totalK ?: error("missing total_k")
        val size = Math.multiplyExact(k, chunkSize ?: error("missing chunk size"))
        val framed = ByteArray(size)
        for (index in 0 until k) {
            val block = blocks[index] ?: error("missing decoded block")
            block.copyInto(framed, index * block.size)
        }
        if (framed.size < 8) throw PacketFormatException("decoded stream has no length header")
        val length = ByteBuffer.wrap(framed, 0, 8).order(ByteOrder.BIG_ENDIAN).long
        if (length < 0 || length > framed.size - 8L || length > Int.MAX_VALUE) {
            throw PacketFormatException("decoded length is invalid")
        }
        val data = framed.copyOfRange(8, 8 + length.toInt())
        if (!Sha256Verifier.verify(data, streamHash ?: error("missing hash"))) {
            throw PacketFormatException("decoded data failed SHA-256 verification")
        }
        return data
    }

    fun expectedHash(): ByteArray? = streamHash?.copyOf()

    private fun peel() {
        val queue = ArrayDeque<Int>()
        equations.indices.filterTo(queue) { equations[it].indices.size == 1 }
        while (queue.isNotEmpty()) {
            val equation = equations[queue.removeFirst()]
            if (equation.indices.size != 1) continue
            val blockIndex = equation.indices.first()
            if (blocks.containsKey(blockIndex)) continue
            val block = equation.payload.copyOf()
            blocks[blockIndex] = block
            equation.indices.clear()
            equations.forEachIndexed { index, other ->
                if (other.indices.remove(blockIndex)) {
                    xorInto(other.payload, block)
                    if (other.indices.size == 1) queue.add(index)
                }
            }
        }
    }

    private fun xorInto(target: ByteArray, source: ByteArray) {
        for (index in target.indices) target[index] = (target[index].toInt() xor source[index].toInt()).toByte()
    }
}
