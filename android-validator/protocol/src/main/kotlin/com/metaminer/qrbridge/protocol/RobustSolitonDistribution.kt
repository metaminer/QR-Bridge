package com.metaminer.qrbridge.protocol

import kotlin.math.ln
import kotlin.math.sqrt

class RobustSolitonDistribution(
    val totalK: Int,
    c: Double = 0.1,
    delta: Double = 0.05,
) {
    private val probabilities: DoubleArray

    init {
        require(totalK > 0)
        require(c > 0 && delta > 0 && delta < 1)
        val rho = DoubleArray(totalK + 1)
        rho[1] = 1.0 / totalK
        for (degree in 2..totalK) rho[degree] = 1.0 / (degree * (degree - 1.0))
        val r = c * ln(totalK / delta) * sqrt(totalK.toDouble())
        val pivot = (totalK / r).toInt().coerceIn(1, totalK)
        val tau = DoubleArray(totalK + 1)
        for (degree in 1 until pivot) tau[degree] = r / (degree * totalK)
        tau[pivot] = r * ln(r / delta) / totalK
        val normalization = rho.sum() + tau.sum()
        probabilities = DoubleArray(totalK + 1) { degree ->
            (rho[degree] + tau[degree]) / normalization
        }
    }

    fun sample(random: PythonRandomCompat): Int {
        val needle = random.random()
        var cumulative = 0.0
        for (degree in 1..totalK) {
            cumulative += probabilities[degree]
            if (needle <= cumulative) return degree
        }
        return totalK
    }
}

internal fun sourceIndices(totalK: Int, seq: Long, seed: Long): MutableSet<Int> {
    if (seq < totalK) return mutableSetOf(seq.toInt())
    val random = PythonRandomCompat(seed)
    val degree = RobustSolitonDistribution(totalK).sample(random)
    return random.sampleRange(totalK, degree).toMutableSet()
}

