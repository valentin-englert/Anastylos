package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Stable-dwell hysteresis with an explicit fake clock (sample
 * timestamps are test inputs, never wall time).
 */
class DwellTest {
    private fun sample(
        tMs: Long,
        speed: Double = 2.0,
        angular: Double = 0.5,
        age: Long = 60,
        gates: Boolean = true,
    ) = StabilitySample(tMs, speed, angular, age, gates)

    @Test
    fun fullHoldReachesReady() {
        val dwell = DwellAccumulator()
        var state: DwellState = DwellState.Idle
        // 14 samples at 80 ms: 13 gaps, 1040 ms spanned.
        for (i in 0..13) {
            state = dwell.observe(sample(i * 80L))
        }
        assertThat(state).isEqualTo(DwellState.Ready)
    }

    @Test
    fun twelveSamplesUnderOneSecondAreNotReady() {
        val dwell = DwellAccumulator()
        var state: DwellState = DwellState.Idle
        for (i in 0..11) {
            state = dwell.observe(sample(i * 75L))
        }
        assertThat(state).isEqualTo(DwellState.Accumulating(12, 825))
    }

    @Test
    fun elevenSamplesOverOneSecondAreNotReady() {
        val dwell = DwellAccumulator()
        var state: DwellState = DwellState.Idle
        for (i in 0..10) {
            state = dwell.observe(sample(i * 100L))
        }
        assertThat(state).isEqualTo(DwellState.Accumulating(11, 1000))
    }

    @Test
    fun gapOver150msRestartsWindow() {
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(i * 80L))
        }
        val state = dwell.observe(sample(7 * 80L + 200L))
        assertThat(state).isEqualTo(DwellState.Accumulating(1, 0))
    }

    @Test
    fun negativePoseAgeResetsToIdle() {
        // HARDWARE-03: mixed clock domains once produced negative
        // ages; they must fail closed, never count as fresh.
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(i * 80L))
        }
        assertThat(dwell.observe(sample(8 * 80L, age = -541578L))).isEqualTo(DwellState.Idle)
    }

    @Test
    fun speedViolationResetsToIdle() {
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(i * 80L))
        }
        assertThat(dwell.observe(sample(8 * 80L, speed = 8.0))).isEqualTo(DwellState.Idle)
    }

    @Test
    fun angularViolationResetsToIdle() {
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(i * 80L))
        }
        assertThat(dwell.observe(sample(8 * 80L, angular = 1.5))).isEqualTo(DwellState.Idle)
    }

    @Test
    fun bridgedStaleGapDoesNotWipeTheWindow() {
        val dwell = DwellAccumulator(holdMs = 500L, minSamples = 5)
        dwell.observe(sample(0L))
        dwell.observe(sample(80L))
        dwell.observe(sample(160L))
        // Two skipped frames would make the next fresh sample 240 ms
        // after the last counted one, past the 150 ms gap, unless the
        // stale publications bridge the open window.
        dwell.bridge(240L)
        dwell.bridge(320L)
        val state = dwell.observe(sample(400L))
        assertThat(state).isEqualTo(DwellState.Accumulating(4, 400))
    }

    @Test
    fun bridgeLongerThanTheGapResets() {
        val dwell = DwellAccumulator()
        dwell.observe(sample(0L))
        dwell.observe(sample(80L))
        dwell.bridge(80L + 200L)
        assertThat(dwell.state).isEqualTo(DwellState.Idle)
    }

    @Test
    fun stalePoseResetsToIdle() {
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(i * 80L))
        }
        assertThat(dwell.observe(sample(8 * 80L, age = 150))).isNotEqualTo(DwellState.Idle)
        assertThat(dwell.observe(sample(9 * 80L, age = 151))).isEqualTo(DwellState.Idle)
    }

    @Test
    fun missingFrameResetsHold() {
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(i * 80L))
        }
        assertThat(dwell.observe(sample(8 * 80L, gates = false))).isEqualTo(DwellState.Idle)
    }

    @Test
    fun abortedHoldAccumulatesFullyAgain() {
        val dwell = DwellAccumulator()
        for (i in 0..10) {
            dwell.observe(sample(i * 80L))
        }
        dwell.observe(sample(11 * 80L, speed = 30.0))
        var state: DwellState = DwellState.Idle
        for (i in 12..25) {
            state = dwell.observe(sample(i * 80L))
        }
        assertThat(state).isEqualTo(DwellState.Ready)
    }

    @Test
    fun readySticksWhileEligibleAndBreaksOnViolation() {
        val dwell = DwellAccumulator()
        for (i in 0..13) {
            dwell.observe(sample(i * 80L))
        }
        assertThat(dwell.state).isEqualTo(DwellState.Ready)
        assertThat(dwell.observe(sample(14 * 80L))).isEqualTo(DwellState.Ready)
        assertThat(dwell.observe(sample(15 * 80L, speed = 30.0))).isEqualTo(DwellState.Idle)
    }

    @Test
    fun backwardsClockStartsFreshWindow() {
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(1000L + i * 80L))
        }
        assertThat(dwell.observe(sample(500L))).isEqualTo(DwellState.Accumulating(1, 0))
    }

    @Test
    fun resetClears() {
        val dwell = DwellAccumulator()
        for (i in 0..7) {
            dwell.observe(sample(i * 80L))
        }
        dwell.reset()
        assertThat(dwell.state).isEqualTo(DwellState.Idle)
    }
}
