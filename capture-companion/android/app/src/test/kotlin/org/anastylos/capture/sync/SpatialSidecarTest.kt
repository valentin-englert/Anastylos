package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.anastylos.capture.guidance.WireRegistration
import org.junit.Test

class SpatialSidecarTest {
    private val json = Json { ignoreUnknownKeys = true }
    private val sha = "ab".repeat(32)

    private fun input(
        calibrated: Boolean = false,
        estimatorStatus: WireRegistration = WireRegistration.VALID,
        tBC: List<Double>? = listOf(
            1.0, 0.0, 0.0, 120.0,
            0.0, 1.0, 0.0, 70.0,
            0.0, 0.0, 1.0, -450.0,
            0.0, 0.0, 0.0, 1.0,
        ),
        sampleAgeMs: Double? = 42.0,
        rmsPx: Double? = 0.8,
        detected: List<Int> = listOf(10, 11, 12, 13),
    ) = SpatialSidecar.Input(
        evidenceId = "00000000-0000-4000-8000-000000000001",
        sessionId = "00000000-0000-4000-8000-000000000002",
        sourceId = "00000000-0000-4000-8000-000000000003",
        requestId = "00000000-0000-4000-8000-000000000004",
        requestRevision = 1,
        attemptId = "00000000-0000-4000-8000-000000000005",
        createdAt = "2026-10-29T12:01:10Z",
        image = SpatialSidecar.Image(
            packageId = "00000000-0000-4000-8000-000000000006",
            observationId = "00000000-0000-4000-8000-000000000007",
            imageId = "00000000-0000-4000-8000-000000000008",
            assetId = "00000000-0000-4000-8000-000000000009",
            sha256 = sha,
            byteLength = 259,
            mime = "image/jpeg",
            widthPx = 4000,
            heightPx = 3000,
            exifOrientation = 1,
        ),
        camera = SpatialSidecar.Camera(
            cameraId = "camera:0",
            lensFacing = "back",
            sensorMode = "default",
        ),
        calibration = SpatialSidecar.Calibration(
            profileId = if (calibrated) "00000000-0000-4000-8000-00000000000a" else "",
            profileSha256 = if (calibrated) "cd".repeat(32) else "",
            nominalDescriptor = "factory-fx800-fy800-cx640-cy360-dist0",
            layoutSha256 = "ef".repeat(32),
            boardInstanceId = if (calibrated) "00000000-0000-4000-8000-00000000000b" else "",
            boardInstanceSha256 = if (calibrated) "b".repeat(64) else "",
            boardFrameId = "surface_a3",
            metricValidity = if (calibrated) "validated" else "nominal_only",
        ),
        analysisWidthPx = 1280,
        analysisHeightPx = 720,
        analysisRotationDeg = 0,
        transformEpoch = "00000000-0000-4000-8000-00000000000c",
        captureTime = SpatialSidecar.CaptureTime(
            callbackUtc = "2026-10-29T12:01:10Z",
            callbackMonotonicNs = "987654321",
            bootId = "00000000-0000-4000-8000-00000000000d",
        ),
        pose = SpatialSidecar.Pose(
            tBC = tBC,
            estimatorStatus = estimatorStatus,
            rmsPx = rmsPx,
            detectedMarkerIds = detected,
            residualReferenceWidthPx = 1280,
            sampleAgeMs = sampleAgeMs,
            imageSha256 = sha,
        ),
        calibrated = calibrated,
        target = SpatialSidecar.Target(
            goalBytes = """{"kind":"pose","label":"t"}""".toByteArray(Charsets.UTF_8),
            roiVerticesMm = listOf(
                listOf(0.0, 0.0, 0.0),
                listOf(100.0, 0.0, 0.0),
                listOf(100.0, 50.0, 0.0),
                listOf(0.0, 50.0, 0.0),
            ),
            objectPoseEpochRef = "00000000-0000-4000-8000-00000000000e",
        ),
        quality = SpatialSidecar.Quality(
            rmsPx = rmsPx,
            rmsGatePx = 1.2,
            motionMedianPx = 0.3,
            motionP90Px = 0.9,
            motionMedianGatePx = 0.7,
            motionP90GatePx = 1.5,
            sharpness = 120.0,
            sharpnessReference = 100.0,
            sharpnessRatioGate = 0.7,
            darkFraction = 0.01,
            saturatedFraction = 0.01,
            badExposureGate = 0.05,
            visibility = "visible",
            inFrame = true,
            thresholdConfigJson = """{"profile":"guidance-hypothesis-1"}""",
        ),
        appVersion = "0.1.0",
        buildSha = "0.1.0+1",
        algorithmConfigJson = """{"solver":"iterative","dict":"DICT_5X5_100"}""",
    )

    private fun docOf(result: SpatialSidecar.Result): JsonObject {
        assertThat(result.json).isNotNull()
        assertThat(result.blockedReasons).isEmpty()
        return json.parseToJsonElement(result.json!!).jsonObject
    }

    @Test
    fun `nominal converged solve is degraded never valid`() {
        val doc = docOf(SpatialSidecar.build(input()))

        assertThat(doc.getValue("schema").jsonPrimitive.content)
            .isEqualTo("anastylos-spatial-evidence@1")
        val pose = doc.getValue("pose").jsonObject
        assertThat(pose.getValue("validity").jsonPrimitive.content).isEqualTo("degraded")
        assertThat(pose.getValue("solve_source").jsonPrimitive.content).isEqualTo("unknown")
        val calibration = doc.getValue("calibration").jsonObject
        assertThat(calibration.getValue("metric_validity").jsonPrimitive.content)
            .isEqualTo("nominal_only")
        val reasons = calibration.getValue("reasons").jsonArray.map { it.jsonPrimitive.content }
        assertThat(reasons).contains("nominal_profile_unmeasured: factory intrinsics, not a measured calibration")
        assertThat(reasons).contains("nominal_board_instance_unmeasured: print scale not measured")
        // Nominal ids are stable uuids, not measurements.
        assertThat(calibration.getValue("profile_id").jsonPrimitive.content).matches(
            "[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        )
        // Transform ids bind camera + board instance.
        val transform = pose.getValue("T_board_camera").jsonObject
        assertThat(transform.getValue("from").jsonPrimitive.content).isEqualTo("camera:camera:0")
        assertThat(transform.getValue("to").jsonPrimitive.content).startsWith("board:")
        assertThat(transform.getValue("units").jsonPrimitive.content).isEqualTo("mm")
        // Quality cannot be accepted without calibration.
        assertThat(doc.getValue("quality").jsonObject.getValue("overall").jsonPrimitive.content)
            .isEqualTo("unknown")
        // Limitations say the honest part out loud.
        val limitations = doc.getValue("limitations").jsonArray.map { it.jsonPrimitive.content }
        assertThat(limitations).isNotEmpty()
    }

    @Test
    fun `calibrated in-gate solve is accepted`() {
        val doc = docOf(SpatialSidecar.build(input(calibrated = true)))

        assertThat(doc.getValue("pose").jsonObject.getValue("validity").jsonPrimitive.content)
            .isEqualTo("valid")
        assertThat(doc.getValue("quality").jsonObject.getValue("overall").jsonPrimitive.content)
            .isEqualTo("accepted")
        val basis = doc.getValue("provenance").jsonObject.getValue("basis").jsonArray
            .map { it.jsonPrimitive.content }
        assertThat(basis).contains("geometric_measurement")
    }

    @Test
    fun `failed gate rejects overall`() {
        val bad = input().copy(
            quality = input().quality.copy(motionP90Px = 9.9),
        )
        val doc = docOf(SpatialSidecar.build(bad))

        assertThat(doc.getValue("quality").jsonObject.getValue("overall").jsonPrimitive.content)
            .isEqualTo("rejected")
        val motion = doc.getValue("quality").jsonObject.getValue("motion").jsonObject
        assertThat(motion.getValue("status").jsonPrimitive.content).isEqualTo("fail")
    }

    @Test
    fun `lost tracking yields a transform-less pose`() {
        val doc = docOf(
            SpatialSidecar.build(
                input(estimatorStatus = WireRegistration.LOST, tBC = null, rmsPx = null, detected = emptyList()),
            ),
        )

        val pose = doc.getValue("pose").jsonObject
        assertThat(pose.getValue("validity").jsonPrimitive.content).isEqualTo("lost")
        assertThat(pose.getValue("T_board_camera")).isEqualTo(JsonNull)
    }

    @Test
    fun `stale sample blocks the sidecar`() {
        val result = SpatialSidecar.build(input(sampleAgeMs = 151.0))

        assertThat(result.json).isNull()
        assertThat(result.blockedReasons).isNotEmpty()
    }

    @Test
    fun `inverted board depth blocks the sidecar`() {
        val flipped = listOf(
            1.0, 0.0, 0.0, 120.0,
            0.0, 1.0, 0.0, 70.0,
            0.0, 0.0, 1.0, 450.0,
            0.0, 0.0, 0.0, 1.0,
        )
        val result = SpatialSidecar.build(input(tBC = flipped))

        assertThat(result.json).isNull()
    }

    @Test
    fun `reflected rotation blocks the sidecar`() {
        val reflected = listOf(
            -1.0, 0.0, 0.0, 120.0,
            0.0, 1.0, 0.0, 70.0,
            0.0, 0.0, 1.0, -450.0,
            0.0, 0.0, 0.0, 1.0,
        )
        val result = SpatialSidecar.build(input(tBC = reflected))

        assertThat(result.json).isNull()
    }

    @Test
    fun `validated claim on nominal inputs blocks`() {
        val bad = input().copy(
            calibration = input().calibration.copy(metricValidity = "validated"),
        )
        val result = SpatialSidecar.build(bad)

        assertThat(result.json).isNull()
    }

    @Test
    fun `goal and threshold hashes pin their bytes`() {
        val doc = docOf(SpatialSidecar.build(input()))

        val goal = doc.getValue("target").jsonObject.getValue("goal_sha256").jsonPrimitive.content
        assertThat(goal).hasLength(64)
        val doc2 = docOf(SpatialSidecar.build(input()))
        assertThat(
            doc2.getValue("target").jsonObject.getValue("goal_sha256").jsonPrimitive.content,
        ).isEqualTo(goal)
        val threshold = doc.getValue("quality").jsonObject
            .getValue("threshold_config_sha256").jsonPrimitive.content
        assertThat(threshold).hasLength(64)
    }

    @Test
    fun `reference-to-image scales analysis to still`() {
        val doc = docOf(SpatialSidecar.build(input()))

        val geo = doc.getValue("image_geometry").jsonObject
        val matrix = geo.getValue("reference_to_image").jsonArray.map { it.jsonPrimitive.double }
        // 4000/1280 x, 3000/720 y, pure scale, det > 0.
        assertThat(matrix[0]).isWithin(1e-9).of(3.125)
        assertThat(matrix[4]).isWithin(1e-9).of(3000.0 / 720.0)
        assertThat(matrix[8]).isWithin(1e-9).of(1.0)
        assertThat(geo.getValue("distortion_before_image_transform").jsonPrimitive.content)
            .isEqualTo("true")
    }
}
