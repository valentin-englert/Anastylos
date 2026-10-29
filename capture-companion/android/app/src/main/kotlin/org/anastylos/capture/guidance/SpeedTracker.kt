package org.anastylos.capture.guidance

import kotlin.math.sqrt

/**
 * Trailing-window speed estimates for dwell gating. Keeps timestamped
 * camera centers + rotations and reports linear/angular speed over
 * the last 300 ms (tracking §8). Pure and unit-tested; the caller
 * feeds one observation per VALID estimate.
 */
class SpeedTracker(private val windowMs: Long = 300L) {
    private data class Sample(val tMs: Long, val center: Vec3, val rotation: List<Double>)

    private val samples = ArrayDeque<Sample>()

    data class Speeds(val speedMmS: Double, val angularSpeedDegS: Double)

    /**
     * Latest windowed speeds, or null until the window holds two
     * samples spanning a positive interval.
     */
    fun observe(tMs: Long, center: Vec3, rotation: List<Double>): Speeds? {
        require(rotation.size == 9) { "rotation must hold 9 row-major doubles" }
        samples.addLast(Sample(tMs, center, rotation))
        while (samples.size > 1 && tMs - samples.first().tMs > windowMs) {
            samples.removeFirst()
        }
        if (samples.size < 2) return null
        val first = samples.first()
        val last = samples.last()
        val dtS = (last.tMs - first.tMs) / 1000.0
        if (dtS <= 0.0) return null
        val dx = last.center.x - first.center.x
        val dy = last.center.y - first.center.y
        val dz = last.center.z - first.center.z
        val distMm = sqrt(dx * dx + dy * dy + dz * dz)
        val angleDeg = rotationAngleBetweenDeg(first.rotation, last.rotation)
        return Speeds(distMm / dtS, angleDeg / dtS)
    }

    fun reset() {
        samples.clear()
    }
}
