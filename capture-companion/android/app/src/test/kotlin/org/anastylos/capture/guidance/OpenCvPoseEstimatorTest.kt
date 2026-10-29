package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Test

/**
 * Estimator glue (gates, status mapping, containment) with a fake
 * native backend. The fake replays the cv2 oracle's solvePnP
 * round-trip on real A3 geometry, so the VALID path exercises true
 * board correspondences end to end.
 */
class OpenCvPoseEstimatorTest {
    private class FakeBackend(
        var detections: List<PoseMath.MarkerDetection> = emptyList(),
        var solution: OpenCvPoseEstimator.ArucoBackend.PnpSolution? = null,
        var detectThrows: Boolean = false,
        var solveThrows: Boolean = false,
        var detectLinkageThrows: Boolean = false,
        var solveLinkageThrows: Boolean = false,
    ) : OpenCvPoseEstimator.ArucoBackend {
        var lastDictionary = -1
        var released = false

        override fun detect(
            gray: ByteArray,
            width: Int,
            height: Int,
            dictionary: Int,
        ): List<PoseMath.MarkerDetection> {
            lastDictionary = dictionary
            if (detectLinkageThrows) throw UnsatisfiedLinkError("libopencv_java4.so missing")
            if (detectThrows) throw IllegalStateException("native init failed")
            return detections
        }

        override fun solve(
            objectMm: List<List<Double>>,
            imagePx: List<List<Double>>,
            intrinsics: PoseEstimator.Intrinsics,
        ): OpenCvPoseEstimator.ArucoBackend.PnpSolution? {
            if (solveLinkageThrows) throw UnsatisfiedLinkError("libopencv_java4.so missing")
            if (solveThrows) throw IllegalStateException("solver failed")
            return solution
        }

        override fun release() {
            released = true
        }
    }

    private val oracle by lazy {
        val stream = javaClass.classLoader!!.getResourceAsStream("pnp-oracle.json")
            ?: error("missing test resource pnp-oracle.json")
        Json.parseToJsonElement(stream.bufferedReader().readText()).jsonObject
    }

    private fun a3Board() = PoseEstimator.BoardSpec(
        boardId = "surface_a3",
        markerSizeMm = 42.0,
        markerIds = listOf(10, 11, 12, 13, 14, 15, 16),
        dictionary = "DICT_5X5_100",
        markerCornersMm = BoardGeometry.SURFACE_A3.markers.mapValues { (_, marker) -> marker.corners },
    )

    private fun oracleDetections(): List<PoseMath.MarkerDetection> {
        val solve = oracle["solvepnp"]!!.jsonObject
        val ids = solve["marker_ids"]!!.jsonArray.map { it.jsonPrimitive.content.toInt() }
        val points = solve["image_points"]!!.jsonArray.map { row ->
            row.jsonArray.map { it.jsonPrimitive.double }
        }
        return ids.mapIndexed { index, id ->
            PoseMath.MarkerDetection(id, points.subList(index * 4, index * 4 + 4))
        }
    }

    private fun oracleSolution(): OpenCvPoseEstimator.ArucoBackend.PnpSolution {
        val solve = oracle["solvepnp"]!!.jsonObject
        fun arr(name: String) = solve[name]!!.jsonArray.map { it.jsonPrimitive.double }
        return OpenCvPoseEstimator.ArucoBackend.PnpSolution(arr("rvec_est"), arr("tvec_est"))
    }

    private fun input(width: Int = 640, height: Int = 480) = PoseEstimator.Input(
        yPlane = ByteArray(width * height),
        width = width, height = height, rotationDegrees = 90,
        sensorTimestampNs = 1000L, monotonicNs = 2000L,
        transformEpochId = "epoch-1",
    )

    private fun intrinsics(width: Int = 640, height: Int = 480) = PoseEstimator.Intrinsics(
        fx = 800.0, fy = 810.0, cx = 320.0, cy = 240.0,
        width = width, height = height,
        distortion = listOf(0.0, 0.0, 0.0, 0.0, 0.0),
        profileId = "factory-test",
    )

    @Test
    fun oracleRoundTripValidates() {
        val backend = FakeBackend(oracleDetections(), oracleSolution())
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), a3Board())
        assertThat(backend.lastDictionary).isEqualTo(org.opencv.objdetect.Objdetect.DICT_5X5_100)
        assertThat(estimate.status).isEqualTo(WireRegistration.VALID)
        assertThat(estimate.markerIdsSeen).containsExactly(10, 11, 12, 13, 14).inOrder()
        assertThat(estimate.cornerCount).isEqualTo(20)
        assertThat(estimate.cornersPx.map { it.id }).containsExactly(10, 11, 12, 13, 14).inOrder()
        assertThat(estimate.cornersPx.first().corners.size).isEqualTo(4)
        assertThat(estimate.rmsPx!!).isLessThan(1e-6)
        val center = translationOfRowMajor(estimate.tBC!!)
        assertThat(center.z).isLessThan(0.0)
        assertThat(estimate.sensorTimestampNs).isEqualTo(1000L)
        assertThat(estimate.monotonicNs).isEqualTo(2000L)
    }

    @Test
    fun emptyFrameLost() {
        val estimate = OpenCvPoseEstimator(FakeBackend()).estimate(input(), intrinsics(), a3Board())
        assertThat(estimate.status).isEqualTo(WireRegistration.LOST)
        assertThat(estimate.tBC).isNull()
    }

    @Test
    fun unknownDictionaryLost() {
        val backend = FakeBackend(oracleDetections(), oracleSolution())
        val board = a3Board().copy(dictionary = "DICT_9X9_999")
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), board)
        assertThat(estimate.status).isEqualTo(WireRegistration.LOST)
        assertThat(backend.lastDictionary).isEqualTo(-1)
    }

    @Test
    fun nullIntrinsicsDegradedNeverValid() {
        val backend = FakeBackend(oracleDetections(), oracleSolution())
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), null, a3Board())
        assertThat(estimate.status).isEqualTo(WireRegistration.DEGRADED)
        assertThat(estimate.tBC).isNull()
        assertThat(estimate.markerIdsSeen).containsExactly(10, 11, 12, 13, 14).inOrder()
    }

    @Test
    fun charucoDetectionWithoutGeometryDegraded() {
        val backend = FakeBackend(
            detections = listOf(
                PoseMath.MarkerDetection(3, listOf(listOf(1.0, 1.0), listOf(2.0, 1.0), listOf(2.0, 2.0), listOf(1.0, 2.0))),
            ),
        )
        val charuco = PoseEstimator.BoardSpec(
            boardId = "charuco_a4", markerSizeMm = 24.0,
            markerIds = (0..16).toList(), dictionary = "DICT_6X6_250",
        )
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), charuco)
        assertThat(backend.lastDictionary).isEqualTo(org.opencv.objdetect.Objdetect.DICT_6X6_250)
        assertThat(estimate.status).isEqualTo(WireRegistration.DEGRADED)
        assertThat(estimate.markerIdsSeen).containsExactly(3)
    }

    @Test
    fun insaneIntrinsicsRejected() {
        val backend = FakeBackend(oracleDetections(), oracleSolution())
        val estimator = OpenCvPoseEstimator(backend)
        for (bad in listOf(
            intrinsics().copy(fx = 0.0),
            intrinsics().copy(cx = 9999.0),
            intrinsics().copy(width = 320),
            intrinsics().copy(distortion = listOf(0.0)),
            intrinsics().copy(distortion = listOf(0.0, 0.0, Double.NaN, 0.0)),
            intrinsics().copy(profileId = ""),
        )) {
            val estimate = estimator.estimate(input(), bad, a3Board())
            assertThat(estimate.status).isEqualTo(WireRegistration.BAD_CALIBRATION)
            assertThat(estimate.tBC).isNull()
        }
    }

    @Test
    fun fewMarkersAmbiguous() {
        // Two markers solve (RMS ~0) but fail the MIN_MARKERS gate.
        val backend = FakeBackend(oracleDetections().take(2), oracleSolution())
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), a3Board())
        assertThat(estimate.status).isEqualTo(WireRegistration.AMBIGUOUS)
        assertThat(estimate.tBC).isNotNull() // best effort for coarse cues
    }

    @Test
    fun highRmsAmbiguous() {
        val perturbed = oracleSolution().copy(tvec = listOf(-198.0, -136.0, 800.0))
        val backend = FakeBackend(oracleDetections(), perturbed)
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), a3Board())
        assertThat(estimate.status).isEqualTo(WireRegistration.AMBIGUOUS)
        assertThat(estimate.rmsPx!!).isGreaterThan(GuidanceHypothesis1.LIVE_RMS_PX)
    }

    @Test
    fun behindBoardWithheld() {
        // Camera center flips behind the board (z > 0): no pose at all.
        val flipped = oracleSolution().copy(tvec = listOf(-198.0, -136.0, -700.0))
        val backend = FakeBackend(oracleDetections(), flipped)
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), a3Board())
        assertThat(estimate.status).isEqualTo(WireRegistration.AMBIGUOUS)
        assertThat(estimate.tBC).isNull()
    }

    @Test
    fun nanSolutionWithheld() {
        val nan = OpenCvPoseEstimator.ArucoBackend.PnpSolution(
            rvec = listOf(Double.NaN, 0.0, 0.0), tvec = listOf(0.0, 0.0, 400.0),
        )
        val backend = FakeBackend(oracleDetections(), nan)
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), a3Board())
        assertThat(estimate.status).isEqualTo(WireRegistration.AMBIGUOUS)
        assertThat(estimate.tBC).isNull()
    }

    @Test
    fun solverDeclineDegraded() {
        val backend = FakeBackend(oracleDetections(), solution = null)
        val estimate = OpenCvPoseEstimator(backend).estimate(input(), intrinsics(), a3Board())
        assertThat(estimate.status).isEqualTo(WireRegistration.DEGRADED)
        assertThat(estimate.markerIdsSeen).isNotEmpty()
    }

    @Test
    fun backendExceptionsContained() {
        val failing = FakeBackend(detectThrows = true)
        val lost = OpenCvPoseEstimator(failing).estimate(input(), intrinsics(), a3Board())
        assertThat(lost.status).isEqualTo(WireRegistration.LOST)
        val solveFail = FakeBackend(oracleDetections(), solveThrows = true)
        val degraded = OpenCvPoseEstimator(solveFail).estimate(input(), intrinsics(), a3Board())
        assertThat(degraded.status).isEqualTo(WireRegistration.DEGRADED)
    }

    @Test
    fun quadReshapePreservesCornerOrder() {
        // Row-major bulk read shared by the 1x4 (Android 4.14) and
        // 4x1 corner layouts: pairs must keep native corner order.
        val points = quadPointsRowMajor(floatArrayOf(1f, 2f, 3f, 4f, 5f, 6f, 7f, 8f))
        assertThat(points).isEqualTo(
            listOf(listOf(1.0, 2.0), listOf(3.0, 4.0), listOf(5.0, 6.0), listOf(7.0, 8.0)),
        )
    }

    @Test
    fun quadReshapeRejectsShortBuffer() {
        // A partial bulk read (empty or truncated quad Mat) must
        // drop the marker instead of indexing past the buffer.
        assertThat(quadPointsRowMajor(floatArrayOf(1f, 2f, 3f, 4f))).isNull()
        assertThat(quadPointsRowMajor(floatArrayOf())).isNull()
    }


    @Test
    fun closeReleasesBackend() {
        val backend = FakeBackend()
        OpenCvPoseEstimator(backend).close()
        assertThat(backend.released).isTrue()
    }

    @Test
    fun backendLinkageErrorsContained() {
        val failing = FakeBackend(detectLinkageThrows = true)
        val lost = OpenCvPoseEstimator(failing).estimate(input(), intrinsics(), a3Board())
        assertThat(lost.status).isEqualTo(WireRegistration.LOST)
        val solveFail = FakeBackend(oracleDetections(), solveLinkageThrows = true)
        val degraded = OpenCvPoseEstimator(solveFail).estimate(input(), intrinsics(), a3Board())
        assertThat(degraded.status).isEqualTo(WireRegistration.DEGRADED)
        assertThat(degraded.markerIdsSeen).isNotEmpty()
    }

    @Test
    fun nativeMissingFailsClosedBeforeMat() {
        // No desktop OpenCV ships with the unit-test runtime, so the
        // real backend must report the missing library as a plain
        // failure (contained upstream) — never as a LinkageError from
        // a Mat allocated before init. Regression test for the
        // on-device analyzer crash: init must precede any Mat.
        val backend = OpenCvArucoBackend()
        try {
            backend.detect(ByteArray(64 * 48), 64, 48, org.opencv.objdetect.Objdetect.DICT_5X5_100)
            throw AssertionError("expected IllegalStateException for the missing native library")
        } catch (e: IllegalStateException) {
            assertThat(e.message).contains("OpenCV native init failed")
        }
    }
}
