package com.metaminer.qrbridge.validator.domain

import org.junit.Assert.assertEquals
import org.junit.Test

class SampleFpsTest {
    @Test
    fun `uses rounded capture frame rate`() {
        assertEquals(60, selectSampleFps(59.94f, 30))
        assertEquals(30, selectSampleFps(29.97f, 15))
    }

    @Test
    fun `falls back when metadata frame rate is unavailable`() {
        assertEquals(30, selectSampleFps(null, 30))
        assertEquals(30, selectSampleFps(0f, 30))
        assertEquals(30, selectSampleFps(Float.NaN, 30))
    }

    @Test
    fun `clamps capture and fallback rates to frame source limits`() {
        assertEquals(60, selectSampleFps(120f, 30))
        assertEquals(1, selectSampleFps(0f, 0))
        assertEquals(60, selectSampleFps(null, 120))
    }
}
