package com.metaminer.qrbridge.protocol

import java.lang.Double.longBitsToDouble
import java.util.Base64
import java.util.Properties
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class ProtocolGoldenTest {
    private val fixture = Properties().apply {
        ProtocolGoldenTest::class.java.getResourceAsStream("/golden_vectors.properties")!!.use(::load)
    }

    @Test
    fun `CPython random sequences match`() {
        for (seed in listOf(0L, 1L, 0x51515151L, 0xffffffffL)) {
            val random = PythonRandomCompat(seed)
            val expectedBytes = fixture.getProperty("bits8.$seed").csvLongs()
            assertContentEquals(expectedBytes, LongArray(16) { random.getRandBits(8) })

            val mixed = PythonRandomCompat(seed)
            val expectedMixed = fixture.getProperty("mixedbits.$seed").csvLongs()
            assertContentEquals(expectedMixed, longArrayOf(
                mixed.getRandBits(8), mixed.getRandBits(32), mixed.getRandBits(53), mixed.getRandBits(63),
            ))

            val doubles = PythonRandomCompat(seed)
            fixture.getProperty("randomBits.$seed").split(',').forEach { bits ->
                assertEquals(longBitsToDouble(bits.toULong(16).toLong()), doubles.random())
            }
        }
    }

    @Test
    fun `CPython sample range matches both pool and set branches`() {
        listOf(
            longArrayOf(0, 10, 4), longArrayOf(7, 100, 3),
            longArrayOf(99, 50, 10), longArrayOf(0xffffffffL, 1000, 30),
        ).forEach { (seed, n, count) ->
            val expected = fixture.getProperty("sample.$seed.$n.$count").csvInts()
            assertContentEquals(expected, PythonRandomCompat(seed).sampleRange(n.toInt(), count.toInt()))
        }
    }

    @Test
    fun `degree and source indices match Python`() {
        listOf(
            longArrayOf(10, 10, 10), longArrayOf(50, 53, 53), longArrayOf(300, 450, 450),
        ).forEach { (k, seq, seed) ->
            val expected = fixture.getProperty("indices.$k.$seq.$seed").csvInts().toSet()
            assertEquals(expected, sourceIndices(k.toInt(), seq, seed))
        }
    }

    @Test
    fun `QRT2 parser matches every Python wire packet`() {
        val parser = Qrt2PacketParser()
        packets().forEach { expected ->
            val parsed = parser.parseBase64Ascii(expected.wire)
            assertEquals(expected.seq, parsed.seq)
            assertEquals(expected.seed, parsed.seed)
            assertEquals(expected.totalK, parsed.totalK)
            assertEquals(expected.filename, parsed.filename)
            assertContentEquals(expected.hash, parsed.fileHash)
            assertContentEquals(expected.data, parsed.data)
        }
    }

    @Test
    fun `malformed packets are rejected`() {
        val parser = Qrt2PacketParser()
        assertFailsWith<PacketFormatException> { parser.parseBase64Ascii("not base64".toByteArray()) }
        val valid = packets().first().wire
        assertFailsWith<PacketFormatException> { parser.parseBase64Ascii(valid.copyOf(12)) }
    }

    @Test
    fun `LT decoder restores Python stream with fixed loss scenarios`() {
        val original = Base64.getDecoder().decode(fixture.getProperty("lt.original"))
        val allPackets = packets()
        listOf(0, 10, 30).forEach { lossPercent ->
            val decoder = LtDecoder()
            val kept = allPackets.filterIndexed { index, _ ->
                lossPercent == 0 || (index * 100 / allPackets.size) % 100 >= lossPercent
            }
            kept.forEach { packet ->
                decoder.add(packet.toLtPacket())
                if (decoder.complete) return@forEach
            }
            assertTrue(decoder.complete, "$lossPercent% loss must remain recoverable")
            assertContentEquals(original, decoder.verifiedResult())
        }
    }

    @Test
    fun `duplicates and mixed streams are handled`() {
        val items = packets()
        val decoder = LtDecoder()
        assertEquals(AddPacketResult.Accepted, decoder.add(items[0].toLtPacket()))
        assertEquals(AddPacketResult.Duplicate, decoder.add(items[0].toLtPacket()))
        val mixed = items[1].toLtPacket().copy(fileHash = ByteArray(32) { 7 })
        assertFailsWith<PacketFormatException> { decoder.add(mixed) }
    }

    private fun packets(): List<FixturePacket> {
        val count = fixture.getProperty("lt.count").toInt()
        return (0 until count).map { index ->
            val parts = fixture.getProperty("lt.packet.$index").split('|')
            FixturePacket(
                seq = parts[0].toLong(), seed = parts[1].toLong(), totalK = parts[2].toInt(),
                hash = parts[3].hexBytes(), filename = String(Base64.getDecoder().decode(parts[4]), Charsets.UTF_8),
                data = Base64.getDecoder().decode(parts[5]), wire = parts[6].toByteArray(Charsets.US_ASCII),
            )
        }
    }

    private data class FixturePacket(
        val seq: Long, val seed: Long, val totalK: Int, val hash: ByteArray,
        val filename: String, val data: ByteArray, val wire: ByteArray,
    ) {
        fun toLtPacket() = LtPacket(seq, seed, data, totalK, hash, filename)
    }

    private fun String.csvLongs() = split(',').filter(String::isNotEmpty).map(String::toLong).toLongArray()
    private fun String.csvInts() = split(',').filter(String::isNotEmpty).map(String::toInt).toIntArray()
    private fun String.hexBytes() = chunked(2).map { it.toInt(16).toByte() }.toByteArray()
}
