package org.anastylos.capture.guidance

import org.anastylos.capture.guidance.GuidanceHypothesis1.HOLD_GAP_MS
import org.anastylos.capture.guidance.GuidanceHypothesis1.HOLD_MS
import org.anastylos.capture.guidance.GuidanceHypothesis1.HOLD_SAMPLES
import org.anastylos.capture.guidance.GuidanceHypothesis1.MAX_ANGULAR_SPEED_DEG_S
import org.anastylos.capture.guidance.GuidanceHypothesis1.MAX_SPEED_MM_S
import org.anastylos.capture.guidance.GuidanceHypothesis1.POSE_AGE_MS

/**
 * One stability observation. Speeds are trailing-window estimates
 * (300 ms per tracking §8) produced by the analyzer; times are
 * monotonic milliseconds. `gatesPass` folds every other §8 gate
 * (ellipsoid, image, registration, authorization) into one bit.
 */
data class StabilitySample(
    val tMs: Long,
    val speedMmS: Double,
    val angularSpeedDegS: Double,
    val poseAgeMs: Long,
    val gatesPass: Boolean,
)

sealed interface DwellState {
    data object Idle : DwellState
    data class Accumulating(val samples: Int, val elapsedMs: Long) : DwellState
    data object Ready : DwellState
}

/**
 * Stable-dwell accumulator with hysteresis per tracking §8: READY
 * needs [HOLD_MS] spanned by at least [HOLD_SAMPLES] eligible
 * samples with no gap over [HOLD_GAP_MS]. Any ineligible sample, or
 * any gap over the limit, resets the window entirely — an aborted
 * hold must accumulate the full stable interval again. Reduced
 * cadence never shortens the time requirement.
 */
class DwellAccumulator(
    private val holdMs: Long = HOLD_MS,
    private val minSamples: Int = HOLD_SAMPLES,
    private val maxGapMs: Long = HOLD_GAP_MS,
    private val maxPoseAgeMs: Long = POSE_AGE_MS,
    private val maxSpeedMmS: Double = MAX_SPEED_MM_S,
    private val maxAngularSpeedDegS: Double = MAX_ANGULAR_SPEED_DEG_S,
) {
    private var startMs: Long? = null
    private var lastMs: Long? = null
    private var count: Int = 0

    val state: DwellState
        get() {
            val start = startMs
            val last = lastMs
            if (start == null || last == null || count == 0) return DwellState.Idle
            if (count >= minSamples && last - start >= holdMs) return DwellState.Ready
            return DwellState.Accumulating(count, last - start)
        }

    fun observe(sample: StabilitySample): DwellState {
        // A negative age means the estimate postdates the clock
        // (mixed time domains); it fails closed like any stale
        // sample, matching ShutterGate's age < 0 rejection.
        val eligible = sample.gatesPass &&
            sample.speedMmS < maxSpeedMmS &&
            sample.angularSpeedDegS < maxAngularSpeedDegS &&
            sample.poseAgeMs in 0..maxPoseAgeMs
        val last = lastMs
        val timeWentBackwards = last != null && sample.tMs < last
        if (!eligible || timeWentBackwards || (last != null && sample.tMs - last > maxGapMs)) {
            reset()
            // An eligible sample after a gap starts a fresh window as
            // sample one; the unknown motion inside the gap is never
            // bridged.
            if (eligible) {
                startMs = sample.tMs
                lastMs = sample.tMs
                count = 1
            }
            return state
        }
        if (startMs == null) startMs = sample.tMs
        lastMs = sample.tMs
        count++
        return state
    }

    /**
     * Hackathon MVP: a stale publication is not a sample and not
     * alignment evidence. If it arrives within [maxGapMs] of the
     * open window, keep that window open so the next fresh sample
     * is not punished for the skipped frame. A longer hole, or a
     * clock step backwards, still resets.
     */
    fun bridge(tMs: Long) {
        val last = lastMs ?: return
        if (tMs < last || tMs - last > maxGapMs) {
            reset()
            return
        }
        lastMs = tMs
    }

    fun reset() {
        startMs = null
        lastMs = null
        count = 0
    }
}
