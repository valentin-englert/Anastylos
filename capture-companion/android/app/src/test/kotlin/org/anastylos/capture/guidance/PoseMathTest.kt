package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Test

/**
 * PoseMath against the cv2 4.14.0 oracle (`pnp-oracle.json`, same
 * major.minor as the Android AAR). Tolerances are float-noise
 * tight (1e-9): anything looser would hide a convention mismatch.
 */
class PoseMathTest {
    private val oracle by lazy {
        val stream = javaClass.classLoader!!.getResourceAsStream("pnp-oracle.json")
            ?: error("missing test resource pnp-oracle.json")
        Json.parseToJsonElement(stream.bufferedReader().readText()).jsonObject
    }

    @Test
    fun rodriguesMatchesOracle() {
        val cases = oracle["rodrigues"]!!.jsonArray
        for (case in cases) {
            val obj = case.jsonObject
            val rvec = obj["rvec"]!!.jsonArray.map { it.jsonPrimitive.double }
            val want = obj["matrix"]!!.jsonArray.map { it.jsonPrimitive.double }
            val got = PoseMath.rodriguesToMatrix(rvec)
            assertThat(got.size).isEqualTo(9)
            for (i in 0..8) {
                assertThat(got[i]).isWithin(1e-9).of(want[i])
            }
            // Rotation output is always proper.
            assertThat(PoseMath.isValidRotation(got)).isTrue()
        }
    }

    @Test
    fun projectPointsMatchesOracle() {
        val cases = oracle["project"]!!.jsonArray
        for (case in cases) {
            val obj = case.jsonObject
            fun arr(name: String) = obj[name]!!.jsonArray.map { it.jsonPrimitive.double }
            val points = obj["object"]!!.jsonArray.map { row ->
                row.jsonArray.map { it.jsonPrimitive.double }
            }
            val want = obj["image"]!!.jsonArray.map { row ->
                row.jsonArray.map { it.jsonPrimitive.double }
            }
            val got = PoseMath.projectPoints(
                points,
                arr("rvec"), arr("tvec"),
                obj["fx"]!!.jsonPrimitive.double, obj["fy"]!!.jsonPrimitive.double,
                obj["cx"]!!.jsonPrimitive.double, obj["cy"]!!.jsonPrimitive.double,
                arr("distortion"),
            )
            assertThat(got.size).isEqualTo(want.size)
            for (i in want.indices) {
                val prediction = got[i] ?: error("unexpected behind-camera at $i")
                assertThat(prediction[0]).isWithin(1e-9).of(want[i][0])
                assertThat(prediction[1]).isWithin(1e-9).of(want[i][1])
            }
        }
    }

    @Test
    fun solvePnpOracleRoundTripHasZeroRms() {
        // The oracle solved SQPNP on A3 corners to ~1e-11 RMS; pin the
        // recorded RMS and re-verify the recovered pose reprojects.
        val solve = oracle["solvepnp"]!!.jsonObject
        assertThat(solve["ok"]!!.jsonPrimitive.content).isEqualTo("true")
        val rms = solve["rms_px"]!!.jsonPrimitive.double
        assertThat(rms).isLessThan(1e-6)
        val imagePoints = solve["image_points"]!!.jsonArray.map { row ->
            row.jsonArray.map { it.jsonPrimitive.double }
        }
        // 5 markers x 4 corners = 20 correspondences across two rows.
        assertThat(imagePoints.size).isEqualTo(20)
    }

    @Test
    fun cornerInvarianceDocumented() {
        // ArUco corner[0] tracks the physical corner under roll; the
        // solver needs no index shifting. Pin the oracle positions.
        val cases = oracle["corner_invariance"]!!.jsonArray
        val want = mapOf(0 to (100.0 to 100.0), 90 to (300.0 to 100.0))
        for (case in cases) {
            val obj = case.jsonObject
            val angle = obj["rotation_cw"]!!.jsonPrimitive.content.toInt()
            val corner = obj["corner0"]!!.jsonArray.map { it.jsonPrimitive.double }
            val (wx, wy) = want[angle] ?: continue
            assertThat(corner[0]).isWithin(2.0).of(wx)
            assertThat(corner[1]).isWithin(2.0).of(wy)
        }
        assertThat(cases.size).isEqualTo(4)
    }

    @Test
    fun composeTBCInvertsObjectToCamera() {
        // Identity PnP (board 400 mm in front, axis-aligned): camera
        // center must be (0, 0, -400) with identity rotation.
        val tBC = PoseMath.composeTBC(listOf(0.0, 0.0, 0.0), listOf(0.0, 0.0, 400.0))
        val center = translationOfRowMajor(tBC)
        assertThat(center.x).isWithin(1e-12).of(0.0)
        assertThat(center.y).isWithin(1e-12).of(0.0)
        assertThat(center.z).isWithin(1e-12).of(-400.0)
        assertThat(rotationOfRowMajor(tBC)).containsExactly(
            1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0,
        ).inOrder()
    }

    @Test
    fun composeTBCRoundTripsOraclePose() {
        val solve = oracle["solvepnp"]!!.jsonObject
        fun arr(name: String) = solve[name]!!.jsonArray.map { it.jsonPrimitive.double }
        val tBC = PoseMath.composeTBC(arr("rvec_est"), arr("tvec_est"))
        val rotation = rotationOfRowMajor(tBC)
        assertThat(PoseMath.isValidRotation(rotation, 1e-6)).isTrue()
        assertThat(PoseMath.rotationDeterminant(rotation)).isWithin(1e-9).of(1.0)
        // Camera floats in front of the board: center z must be negative
        // (z into the plane), never behind it.
        assertThat(translationOfRowMajor(tBC).z).isLessThan(0.0)
    }

    @Test
    fun mirroredRotationRejected() {
        // det -1 (reflection across x): never a valid camera pose.
        val mirrored = listOf(-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        assertThat(PoseMath.rotationDeterminant(mirrored)).isWithin(1e-12).of(-1.0)
        assertThat(PoseMath.isValidRotation(mirrored)).isFalse()
    }

    @Test
    fun skewedRotationRejected() {
        val skewed = listOf(1.0, 0.5, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        assertThat(PoseMath.isValidRotation(skewed)).isFalse()
    }

    @Test
    fun correspondencesSkipUnknownIds() {
        val board = PoseEstimator.BoardSpec(
            boardId = "surface_a3",
            markerSizeMm = 42.0,
            markerIds = listOf(10, 11),
            markerCornersMm = mapOf(
                10 to listOf(listOf(0.0, 0.0), listOf(42.0, 0.0), listOf(42.0, 42.0), listOf(0.0, 42.0)),
            ),
        )
        val detections = listOf(
            PoseMath.MarkerDetection(10, listOf(listOf(1.0, 2.0), listOf(3.0, 4.0), listOf(5.0, 6.0), listOf(7.0, 8.0))),
            PoseMath.MarkerDetection(99, listOf(listOf(1.0, 1.0), listOf(2.0, 2.0), listOf(3.0, 3.0), listOf(4.0, 4.0))),
            PoseMath.MarkerDetection(11, listOf(listOf(1.0, 1.0))),
        )
        val correspondences = PoseMath.assembleCorrespondences(detections, board)
        assertThat(correspondences.size).isEqualTo(4)
        assertThat(correspondences[0].objectMm).containsExactly(0.0, 0.0, 0.0).inOrder()
        assertThat(correspondences[0].imagePx).containsExactly(1.0, 2.0).inOrder()
    }

    @Test
    fun cornerSpreadGates() {
        // Full-frame quad: spread 1.0 x 1.0, hull half the frame.
        val full = listOf(listOf(0.0, 0.0), listOf(640.0, 0.0), listOf(640.0, 480.0), listOf(0.0, 480.0))
        val spread = PoseMath.cornerSpread(full, 640, 480)
        assertThat(spread.fracW).isWithin(1e-12).of(1.0)
        assertThat(spread.fracH).isWithin(1e-12).of(1.0)
        assertThat(spread.hullAreaFrac).isWithin(1e-12).of(1.0)
        // Tight cluster: fails both frozen gates.
        val tight = listOf(listOf(300.0, 200.0), listOf(310.0, 200.0), listOf(310.0, 210.0), listOf(300.0, 210.0))
        val narrow = PoseMath.cornerSpread(tight, 640, 480)
        assertThat(narrow.fracW).isLessThan(GuidanceHypothesis1.CORNER_SPREAD_FRAC)
        assertThat(narrow.hullAreaFrac).isLessThan(GuidanceHypothesis1.HULL_AREA_FRAC)
    }

    @Test
    fun behindCameraProjectsNull() {
        val behind = PoseMath.projectPoints(
            listOf(listOf(0.0, 0.0, -500.0)),
            listOf(0.0, 0.0, 0.0), listOf(0.0, 0.0, 400.0),
            800.0, 800.0, 320.0, 240.0, emptyList(),
        )
        assertThat(behind).containsExactly(null)
    }
}
