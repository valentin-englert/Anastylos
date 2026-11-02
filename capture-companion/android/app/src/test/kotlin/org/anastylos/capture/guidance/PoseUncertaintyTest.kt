package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class PoseUncertaintyTest {
    private val intrinsics = PoseEstimator.Intrinsics(
        fx = 1428.5, fy = 1428.6, cx = 967.2, cy = 532.2, width = 1920, height = 1080,
        distortion = listOf(-0.021, 0.069, 0.0, 0.001, -0.10), profileId = "test",
    )

    // Camera ~450 mm above an A3-sized grid, slightly rotated.
    private val rvec = listOf(0.05, -0.03, 0.02)
    private val tvec = listOf(-190.0, -130.0, 450.0)
    private val grid = (0..5).flatMap { i -> (0..3).map { j -> listOf(30.0 + 60.0 * i, 30.0 + 70.0 * j, 0.0) } }

    private fun observed(noisePx: Double): List<PoseMath.Correspondence> {
        val projected = PoseMath.projectPoints(
            grid, rvec, tvec, intrinsics.fx, intrinsics.fy, intrinsics.cx, intrinsics.cy, intrinsics.distortion,
        )
        // Deterministic, zero-mean alternating noise: reproducible and not fitted away.
        return grid.indices.map { k ->
            val sign = if (k % 2 == 0) 1.0 else -1.0
            val p = projected[k]!!
            PoseMath.Correspondence(grid[k], listOf(p[0] + sign * noisePx, p[1] - sign * noisePx * 0.5))
        }
    }

    @Test
    fun noisierCornersGiveLargerButFiniteUncertainty() {
        val small = PoseUncertainty.of(observed(0.2), rvec, tvec, intrinsics)!!
        val large = PoseUncertainty.of(observed(1.0), rvec, tvec, intrinsics)!!
        assertThat(small.positionMm).hasSize(3)
        small.positionMm.forEach { assertThat(it).isGreaterThan(0.0) }
        assertThat(large.positionMm[2]).isGreaterThan(small.positionMm[2])
        assertThat(large.angleDeg).isGreaterThan(small.angleDeg)
        // Sub-pixel noise at 450 mm: millimetre-level, not centimetres.
        large.positionMm.forEach { assertThat(it).isLessThan(10.0) }
        assertThat(large.angleDeg).isLessThan(1.0)
    }

    @Test
    fun tooFewPointsGiveNoClaim() {
        assertThat(PoseUncertainty.of(observed(0.5).take(3), rvec, tvec, intrinsics)).isNull()
    }
}
