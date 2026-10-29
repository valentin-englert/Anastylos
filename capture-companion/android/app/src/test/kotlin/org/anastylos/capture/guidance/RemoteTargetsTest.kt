package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class RemoteTargetsTest {
    private val matrix = listOf(
        1.0, 0.0, 0.0, 120.0,
        0.0, 1.0, 0.0, 70.0,
        0.0, 0.0, 1.0, -450.0,
        0.0, 0.0, 0.0, 1.0,
    )

    @Test
    fun `wire target converts to a viewpoint`() {
        val view = remoteTargetView(
            requestId = "req-1", targetLabel = "upper-left", boardFrameId = "surface_a3",
            tbcUnits = "mm", tbcMatrix = matrix,
            translationToleranceMm = listOf(15.0, 15.0, 20.0), rotationToleranceDeg = 5.0,
        )!!

        assertThat(view.pose.centerMm).isEqualTo(Vec3(120.0, 70.0, -450.0))
        assertThat(view.pose.yawDeg).isWithin(1e-9).of(0.0)
        assertThat(view.posTolMm).isEqualTo(Vec3(15.0, 15.0, 20.0))
        assertThat(view.angTolDeg).isEqualTo(5.0)
    }

    @Test
    fun `malformed tolerances fall back to the profile`() {
        val view = remoteTargetView(
            requestId = "req-1", targetLabel = "t", boardFrameId = "surface_a3",
            tbcUnits = "mm", tbcMatrix = matrix,
            translationToleranceMm = listOf(1.0, 2.0), rotationToleranceDeg = Double.NaN,
        )!!

        assertThat(view.posTolMm).isEqualTo(
            Vec3(
                GuidanceHypothesis1.POS_TOL_X_MM,
                GuidanceHypothesis1.POS_TOL_Y_MM,
                GuidanceHypothesis1.POS_TOL_Z_MM,
            ),
        )
        assertThat(view.angTolDeg).isEqualTo(GuidanceHypothesis1.ANG_TOL_DEG)
    }

    @Test
    fun `wrong units refuse adoption`() {
        assertThat(
            remoteTargetView(
                "req-1", "t", "surface_a3", "cm", matrix,
                listOf(1.0, 1.0, 1.0), 1.0,
            ),
        ).isNull()
    }

    @Test
    fun `camera behind the board refuses adoption`() {
        val flipped = matrix.toMutableList().also { it[11] = 450.0 }
        assertThat(
            remoteTargetView(
                "req-1", "t", "surface_a3", "mm", flipped,
                listOf(1.0, 1.0, 1.0), 1.0,
            ),
        ).isNull()
    }

    @Test
    fun `reflected rotation refuses adoption`() {
        val reflected = matrix.toMutableList().also { it[0] = -1.0 }
        assertThat(
            remoteTargetView(
                "req-1", "t", "surface_a3", "mm", reflected,
                listOf(1.0, 1.0, 1.0), 1.0,
            ),
        ).isNull()
    }

    @Test
    fun `short matrix refuses adoption`() {
        assertThat(
            remoteTargetView(
                "req-1", "t", "surface_a3", "mm", listOf(1.0, 2.0),
                listOf(1.0, 1.0, 1.0), 1.0,
            ),
        ).isNull()
    }
}
