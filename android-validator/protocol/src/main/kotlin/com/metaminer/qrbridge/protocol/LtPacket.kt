package com.metaminer.qrbridge.protocol

/** Parsed QRT2 packet. Unsigned 32-bit wire values are held in [Long]. */
data class LtPacket(
    val seq: Long,
    val seed: Long,
    val data: ByteArray,
    val totalK: Int,
    val fileHash: ByteArray,
    val filename: String,
) {
    init {
        require(seq in 0..UINT32_MAX)
        require(seed in 0..UINT32_MAX)
        require(totalK > 0)
        require(data.isNotEmpty())
        require(fileHash.size == SHA256_SIZE)
    }

    companion object {
        const val UINT32_MAX = 0xffff_ffffL
        const val SHA256_SIZE = 32
    }
}

