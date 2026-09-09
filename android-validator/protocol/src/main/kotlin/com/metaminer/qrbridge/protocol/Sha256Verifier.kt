package com.metaminer.qrbridge.protocol

import java.security.MessageDigest

object Sha256Verifier {
    fun digest(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)
    fun verify(data: ByteArray, expected: ByteArray): Boolean =
        expected.size == LtPacket.SHA256_SIZE && MessageDigest.isEqual(digest(data), expected)

    fun hex(digest: ByteArray): String = digest.joinToString("") { "%02x".format(it.toInt() and 0xff) }
}

