package com.metaminer.qrbridge.protocol

import kotlin.math.ceil
import kotlin.math.ln
import kotlin.math.max

/** The subset of CPython 3.14 `random.Random` used by QR-Bridge QRT2. */
class PythonRandomCompat(seed: Long) {
    private val state = IntArray(N)
    private var index = N

    init {
        require(seed >= 0) { "QRT2 seeds are unsigned" }
        val words = mutableListOf<Int>()
        var remaining = seed
        do {
            words += (remaining and 0xffff_ffffL).toInt()
            remaining = remaining ushr 32
        } while (remaining != 0L)
        initByArray(words.toIntArray())
    }

    fun getRandBits(bitCount: Int): Long {
        require(bitCount in 0..63)
        if (bitCount == 0) return 0
        if (bitCount <= 32) {
            val mask = if (bitCount == 32) 0xffff_ffffL else (1L shl bitCount) - 1
            return (nextUInt32() ushr (32 - bitCount)).toLong() and mask
        }
        var result = 0L
        var produced = 0
        var remaining = bitCount
        while (remaining > 0) {
            val take = minOf(remaining, 32)
            val word = nextUInt32() ushr (32 - take)
            result = result or ((word.toLong() and 0xffff_ffffL) shl produced)
            produced += take
            remaining -= take
        }
        return result
    }

    fun random(): Double {
        val a = nextUInt32() ushr 5
        val b = nextUInt32() ushr 6
        return (a.toDouble() * 67_108_864.0 + b.toDouble()) / 9_007_199_254_740_992.0
    }

    fun sampleRange(n: Int, count: Int): IntArray {
        require(count in 0..n)
        val result = IntArray(count)
        var setSize = 21
        if (count > 5) {
            setSize += pow4(ceil(ln(count * 3.0) / ln(4.0)).toInt())
        }
        if (n <= setSize) {
            val pool = IntArray(n) { it }
            repeat(count) { i ->
                val j = randBelow(n - i)
                result[i] = pool[j]
                pool[j] = pool[n - i - 1]
            }
        } else {
            val selected = HashSet<Int>(count * 2)
            repeat(count) { i ->
                var j = randBelow(n)
                while (!selected.add(j)) j = randBelow(n)
                result[i] = j
            }
        }
        return result
    }

    private fun randBelow(n: Int): Int {
        require(n > 0)
        val bits = 32 - n.countLeadingZeroBits()
        var value: Long
        do value = getRandBits(bits) while (value >= n)
        return value.toInt()
    }

    private fun initGenRand(seed: Int) {
        state[0] = seed
        for (i in 1 until N) {
            state[i] = 1_812_433_253 * (state[i - 1] xor (state[i - 1] ushr 30)) + i
        }
        index = N
    }

    private fun initByArray(key: IntArray) {
        initGenRand(19_650_218)
        var i = 1
        var j = 0
        repeat(max(N, key.size)) {
            state[i] = (state[i] xor ((state[i - 1] xor (state[i - 1] ushr 30)) * 1_664_525)) + key[j] + j
            i += 1
            j += 1
            if (i >= N) {
                state[0] = state[N - 1]
                i = 1
            }
            if (j >= key.size) j = 0
        }
        repeat(N - 1) {
            state[i] = (state[i] xor ((state[i - 1] xor (state[i - 1] ushr 30)) * 1_566_083_941)) - i
            i += 1
            if (i >= N) {
                state[0] = state[N - 1]
                i = 1
            }
        }
        state[0] = Int.MIN_VALUE
    }

    private fun nextUInt32(): Int {
        if (index >= N) twist()
        var value = state[index++]
        value = value xor (value ushr 11)
        value = value xor ((value shl 7) and 0x9d2c5680.toInt())
        value = value xor ((value shl 15) and 0xefc60000.toInt())
        value = value xor (value ushr 18)
        return value
    }

    private fun twist() {
        for (i in 0 until N) {
            val y = (state[i] and Int.MIN_VALUE) or (state[(i + 1) % N] and Int.MAX_VALUE)
            state[i] = state[(i + M) % N] xor (y ushr 1) xor if ((y and 1) != 0) MATRIX_A else 0
        }
        index = 0
    }

    private fun pow4(exponent: Int): Int {
        var result = 1
        repeat(exponent) { result = Math.multiplyExact(result, 4) }
        return result
    }

    companion object {
        private const val N = 624
        private const val M = 397
        private const val MATRIX_A = 0x9908b0df.toInt()
    }
}
