package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class FrameQualityTest {
    @Test
    fun flatMidGrayIsCleanButSoft() {
        val frame = ByteArray(16 * 16) { 128.toByte() }
        val measurement = FrameQuality.measure(frame, 16, 16)
        assertThat(measurement.darkFraction).isEqualTo(0.0)
        assertThat(measurement.saturatedFraction).isEqualTo(0.0)
        assertThat(measurement.sharpness).isEqualTo(0.0)
    }

    @Test
    fun exposureFractions() {
        // 4x4: 4 dark, 4 saturated, 8 mid.
        val frame = byteArrayOf(
            0, 0, 0, 0,
            -1, -1, -1, -1,
            127, 127, 127, 127,
            127, 127, 127, 127,
        )
        val measurement = FrameQuality.measure(frame, 4, 4)
        assertThat(measurement.darkFraction).isWithin(1e-12).of(0.25)
        assertThat(measurement.saturatedFraction).isWithin(1e-12).of(0.25)
    }

    @Test
    fun checkerboardIsSharperThanFlat() {
        val sharp = ByteArray(16 * 16) { index ->
            val u = index % 16
            val v = index / 16
            if ((u + v) % 2 == 0) 0 else -1
        }
        val flat = ByteArray(16 * 16) { 127 }
        assertThat(FrameQuality.measure(sharp, 16, 16).sharpness)
            .isGreaterThan(FrameQuality.measure(flat, 16, 16).sharpness)
    }

    @Test
    fun verdictWiring() {
        val sharp = FrameQuality.measure(ByteArray(16 * 16) { 127 }, 16, 16)
        // Flat frame: sharpness 0 vs reference 100 -> rejected.
        val verdict = evaluateImageQuality(
            ImageQualityInput(
                sharpness = sharp.sharpness, sharpReference = 100.0,
                darkFraction = sharp.darkFraction, saturatedFraction = sharp.saturatedFraction,
                motionMedianPx = 0.1, motionP90Px = 0.2,
            ),
        )
        assertThat(verdict).isEqualTo(ImageQualityVerdict.Rejected(listOf("sharpness")))
    }

    @Test
    fun tinyFrameRejected() {
        try {
            FrameQuality.measure(ByteArray(4), 2, 2)
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
        }
    }
}
