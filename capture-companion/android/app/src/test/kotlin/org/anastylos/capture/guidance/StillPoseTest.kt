package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class StillPoseTest {
    private fun frame(sensorNs: Long) = PoseEstimator.Estimate(
        tBC = null, markerIdsSeen = emptyList(), cornerCount = 0, rmsPx = null,
        status = WireRegistration.VALID, sensorTimestampNs = sensorNs, monotonicNs = sensorNs,
    )

    private val frames = listOf(1_000_000_000L, 1_033_000_000L, 1_066_000_000L, 1_100_000_000L).map(::frame)

    @Test
    fun pairsTheFrameExposedLastBeforeTheStill() {
        val paired = poseForStill(frames, stillSensorNs = 1_080_000_000L)!!
        assertThat(paired.estimate.sensorTimestampNs).isEqualTo(1_066_000_000L)
        assertThat(paired.sampleAgeMs).isWithin(1e-9).of(14.0)
    }

    @Test
    fun framesExposedAfterTheStillAreNeverPaired() {
        // A later frame is closer in time but saw a different moment: never used.
        assertThat(poseForStill(frames, stillSensorNs = 999_000_000L)).isNull()
    }

    @Test
    fun anExactExposureMatchHasAgeZero() {
        assertThat(poseForStill(frames, stillSensorNs = 1_033_000_000L)!!.sampleAgeMs).isEqualTo(0.0)
    }
}
