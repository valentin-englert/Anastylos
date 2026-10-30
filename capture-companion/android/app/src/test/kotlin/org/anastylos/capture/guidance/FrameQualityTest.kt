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

    /** 20x20 frame: black border (bench), white-ish board in the centre square 5..14. */
    private fun benchAndBoard(): ByteArray = ByteArray(20 * 20) { index ->
        val u = index % 20
        val v = index / 20
        if (u in 5..14 && v in 5..14) 200.toByte() else 0
    }

    private val boardCorners = listOf(
        listOf(5.0, 5.0), listOf(15.0, 5.0), listOf(15.0, 15.0), listOf(5.0, 15.0),
    )

    @Test
    fun boardExposureIgnoresBlackBench() {
        val frame = benchAndBoard()
        val full = FrameQuality.measure(frame, 20, 20)
        assertThat(full.darkFraction).isWithin(1e-12).of(0.75)
        val roi = FrameQuality.withBoardExposure(full, frame, 20, 20, boardCorners)
        assertThat(roi.exposureRoi).isEqualTo(FrameQuality.ROI_MARKER_HULL)
        assertThat(roi.darkFraction).isEqualTo(0.0)
        assertThat(roi.saturatedFraction).isEqualTo(0.0)
        assertThat(roi.fullFrameDarkFraction).isWithin(1e-12).of(0.75)
        assertThat(roi.sharpness).isEqualTo(full.sharpness)
    }

    @Test
    fun boardExposureStillRejectsDarkBoard() {
        val frame = ByteArray(20 * 20) // everything black, board included
        val full = FrameQuality.measure(frame, 20, 20)
        val roi = FrameQuality.withBoardExposure(full, frame, 20, 20, boardCorners)
        assertThat(roi.darkFraction).isEqualTo(1.0)
    }

    @Test
    fun boardExposureCountsSaturationInsideHull() {
        val frame = ByteArray(20 * 20) { index ->
            val u = index % 20
            val v = index / 20
            if (u in 5..14 && v in 5..14) -1 else 0
        }
        val roi = FrameQuality.withBoardExposure(
            FrameQuality.measure(frame, 20, 20), frame, 20, 20, boardCorners,
        )
        assertThat(roi.saturatedFraction).isEqualTo(1.0)
        assertThat(roi.darkFraction).isEqualTo(0.0)
    }

    @Test
    fun degenerateHullKeepsFullFrameMeasurement() {
        val frame = benchAndBoard()
        val full = FrameQuality.measure(frame, 20, 20)
        val collinear = listOf(listOf(1.0, 1.0), listOf(5.0, 5.0), listOf(9.0, 9.0))
        assertThat(FrameQuality.withBoardExposure(full, frame, 20, 20, collinear)).isEqualTo(full)
        assertThat(FrameQuality.withBoardExposure(full, frame, 20, 20, emptyList())).isEqualTo(full)
    }

    @Test
    fun hullOutsideFrameKeepsFullFrameMeasurement() {
        val frame = benchAndBoard()
        val full = FrameQuality.measure(frame, 20, 20)
        val offFrame = listOf(listOf(40.0, 40.0), listOf(60.0, 40.0), listOf(50.0, 60.0))
        assertThat(FrameQuality.withBoardExposure(full, frame, 20, 20, offFrame)).isEqualTo(full)
    }
}
