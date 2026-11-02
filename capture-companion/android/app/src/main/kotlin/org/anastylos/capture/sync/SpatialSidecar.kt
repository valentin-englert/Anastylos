package org.anastylos.capture.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.anastylos.capture.domain.isSha256Hex
import org.anastylos.capture.domain.sha256Hex
import org.anastylos.capture.domain.uuidV5
import org.anastylos.capture.guidance.PoseMath
import org.anastylos.capture.guidance.WireRegistration

/**
 * Builds `anastylos-spatial-evidence@1` sidecars from real phone
 * measurements — or refuses to build one.
 *
 * Honesty policy (fail closed, never invented):
 * - `valid` pose validity only with a calibrated profile; a
 *   converged solve on factory intrinsics is `degraded`.
 * - Nominal (unmeasured) profile/instance ids are deterministic
 *   UUIDv5 over descriptor bytes the phone holds, always declared
 *   in `calibration.reasons` and `limitations`.
 * - `solve_source` is `unknown`: the pose comes from a preview
 *   frame near the still, not the saved image. Metric
 *   qualification therefore stays unavailable by validator rule.
 * - `sample_age_ms` above the 150 ms validator gate blocks the
 *   sidecar (the shutter gate already enforces this; the builder
 *   is defense in depth, not a second opinion).
 * - Quality `accepted` only when every gate passes on a valid
 *   pose with a validated calibration (the validator rejects
 *   contradictions); otherwise `unknown`, or `rejected` when a
 *   gate genuinely fails.
 */
object SpatialSidecar {
    const val SCHEMA_ID = "anastylos-spatial-evidence@1"
    const val PIXEL_CONVENTION = "top_left_pixel_center_0_0_x_right_y_down"
    const val SAMPLE_AGE_GATE_MS = 150.0
    const val PRODUCER = "anastylos-android-guided-capture"
    const val SOLVER_METHOD = "aruco-solvepnp-iterative"
    const val SOLVER_VERSION = "opencv-4.14.0"

    data class Image(
        val packageId: String,
        val observationId: String,
        val imageId: String,
        val assetId: String,
        val sha256: String,
        val byteLength: Long,
        val mime: String,
        val widthPx: Int,
        val heightPx: Int,
        val exifOrientation: Int,
    )

    data class Camera(
        val cameraId: String,
        val physicalLensId: String? = null,
        val lensFacing: String,
        val sensorMode: String,
        val zoom: Double = 1.0,
        val focusDistanceDiopters: Double? = null,
        val focusStatus: String = "unknown",
    )

    data class Calibration(
        /** Measured profile uuid, or blank when nominal. */
        val profileId: String = "",
        /** Hex64 over the measured profile bytes, or blank. */
        val profileSha256: String = "",
        /** Canonical nominal descriptor; hashed when nominal. */
        val nominalDescriptor: String = "",
        val layoutSha256: String,
        /** Measured board-instance uuid, or blank when nominal. */
        val boardInstanceId: String = "",
        /** Hex64 over the measured board-instance record bytes, or blank. */
        val boardInstanceSha256: String = "",
        val boardFrameId: String,
        val metricValidity: String,
        val reasons: List<String> = emptyList(),
    )

    data class Pose(
        val tBC: List<Double>?,
        val estimatorStatus: WireRegistration,
        val rmsPx: Double?,
        val maxResidualPx: Double? = null,
        /** 1 sigma camera centre in board mm and rotation in degrees, with how it was computed. */
        val positionUncertaintyMm: List<Double>? = null,
        val angleUncertaintyDeg: Double? = null,
        val uncertaintyMethod: String = "none",
        val detectedMarkerIds: List<Int>,
        val residualReferenceWidthPx: Int,
        val sampleAgeMs: Double?,
        val imageSha256: String,
    )

    data class Target(
        /** Exact bytes hashed into `goal_sha256` (wire target or local descriptor). */
        val goalBytes: ByteArray,
        /** Board-plane ROI vertices (z == 0), e.g. board corners. */
        val roiVerticesMm: List<List<Double>>,
        val subjectRef: String? = null,
        val objectPoseEpochRef: String? = null,
    )

    data class Quality(
        val rmsPx: Double?,
        val rmsGatePx: Double,
        val motionMedianPx: Double?,
        val motionP90Px: Double?,
        val motionMedianGatePx: Double,
        val motionP90GatePx: Double,
        val sharpness: Double?,
        val sharpnessReference: Double?,
        val sharpnessRatioGate: Double,
        val darkFraction: Double?,
        val saturatedFraction: Double?,
        val badExposureGate: Double,
        val visibility: String,
        val inFrame: Boolean?,
        /** Canonical threshold-config bytes; hashed into the doc. */
        val thresholdConfigJson: String,
    )

    data class CaptureTime(
        val callbackUtc: String,
        val callbackMonotonicNs: String,
        val bootId: String,
        val exposureUtcEstimate: String? = null,
        val uncertaintyMs: Double? = null,
    )

    data class Input(
        val evidenceId: String,
        val sessionId: String,
        val sourceId: String,
        val requestId: String,
        val requestRevision: Int,
        val attemptId: String,
        val createdAt: String,
        val supersedes: String? = null,
        val image: Image,
        val camera: Camera,
        val calibration: Calibration,
        val analysisWidthPx: Int,
        val analysisHeightPx: Int,
        val analysisRotationDeg: Int,
        val transformEpoch: String,
        val captureTime: CaptureTime,
        val pose: Pose,
        val calibrated: Boolean,
        /** The measured profile's own pixel frame; null keeps the analysis-stream frame. */
        val calibrationPixelFrameId: String? = null,
        val target: Target,
        val quality: Quality,
        val associationReason: String = "no custody observer linked",
        val appVersion: String,
        val buildSha: String,
        val algorithmConfigJson: String,
        val extraLimitations: List<String> = emptyList(),
    )

    data class Result(val json: String?, val blockedReasons: List<String>)

    fun build(input: Input): Result {
        val blocked = mutableListOf<String>()
        checkUuid(input.evidenceId, "evidence_id", blocked)
        checkUuid(input.sessionId, "session_id", blocked)
        checkUuid(input.sourceId, "source_id", blocked)
        checkUuid(input.requestId, "request_id", blocked)
        checkUuid(input.attemptId, "attempt_id", blocked)
        if (input.requestRevision < 1) blocked += "request_revision must be >= 1"
        if (!isUtcZ(input.createdAt)) blocked += "created_at must be ISO 8601 UTC"
        if (input.supersedes != null && !isUuid(input.supersedes)) blocked += "supersedes must be a uuid"
        if (input.supersedes == input.evidenceId) blocked += "supersedes must not be self"

        val image = input.image
        checkUuid(image.packageId, "asset.package_id", blocked)
        checkUuid(image.observationId, "asset.observation_id", blocked)
        checkUuid(image.imageId, "asset.image_id", blocked)
        checkUuid(image.assetId, "asset.asset_id", blocked)
        if (!isSha256Hex(image.sha256)) blocked += "asset.sha256 must be hex64"
        if (image.byteLength !in 1..26214400) blocked += "asset.byte_length out of range"
        if (image.mime !in MIME_TYPES) blocked += "asset.mime unknown: ${image.mime}"
        if (image.widthPx !in 1..32768 || image.heightPx !in 1..32768) {
            blocked += "image dimensions out of range"
        }
        if (image.exifOrientation !in 1..8) blocked += "exif_orientation must be 1..8"

        val camera = input.camera
        if (!isText(camera.cameraId, 256)) blocked += "camera.camera_id invalid"
        if (camera.physicalLensId != null && !isText(camera.physicalLensId, 256)) {
            blocked += "camera.physical_lens_id invalid"
        }
        if (camera.lensFacing !in LENS_FACINGS) blocked += "lens_facing unknown: ${camera.lensFacing}"
        if (!isText(camera.sensorMode, 256)) blocked += "camera.sensor_mode invalid"
        if (!camera.zoom.isFinite() || camera.zoom !in 0.1..100.0) blocked += "camera.zoom out of range"
        val focus = camera.focusDistanceDiopters
        if (focus != null && (!focus.isFinite() || focus < 0)) {
            blocked += "camera.focus_distance_diopters invalid"
        }
        if (camera.focusStatus !in FOCUS_STATUSES) blocked += "focus_status unknown"

        val calibration = checkCalibration(input.calibration, blocked) ?: NominalCalibration(
            profileId = "", profileSha256 = "", boardInstanceId = "",
            boardInstanceSha256 = "", metricValidity = "", reasons = emptyList(),
        )

        if (input.analysisWidthPx !in 1..32768 || input.analysisHeightPx !in 1..32768) {
            blocked += "analysis dimensions out of range"
        }
        if (!isUuid(input.transformEpoch)) blocked += "transform_epoch must be a uuid"

        val time = input.captureTime
        if (!isUtcZ(time.callbackUtc)) blocked += "capture_time.callback_utc invalid"
        if (!isNs(time.callbackMonotonicNs)) blocked += "capture_time.callback_monotonic_ns invalid"
        if (!isUuid(time.bootId)) blocked += "capture_time.boot_id must be a uuid"
        if (time.exposureUtcEstimate != null && !isUtcZ(time.exposureUtcEstimate)) {
            blocked += "capture_time.exposure_utc_estimate invalid"
        }
        if (time.uncertaintyMs != null && (!time.uncertaintyMs.isFinite() || time.uncertaintyMs < 0)) {
            blocked += "capture_time.uncertainty_ms invalid"
        }

        val poseCheck = checkPose(input.pose, input.calibrated, blocked)
        val target = input.target
        if (target.roiVerticesMm.isEmpty() || target.roiVerticesMm.size > 64 ||
            target.roiVerticesMm.any { it.size != 3 || it.any { v -> !v.isFinite() } || it[2] != 0.0 }
        ) {
            blocked += "target roi must be 1..64 finite board-plane vertices"
        }
        if (target.subjectRef != null && !isText(target.subjectRef, 256)) blocked += "target.subject_ref invalid"
        if (target.objectPoseEpochRef != null && !isText(target.objectPoseEpochRef, 256)) {
            blocked += "target.object_pose_epoch_ref invalid"
        }

        val quality = input.quality
        if (quality.visibility !in VISIBILITY) blocked += "visibility unknown: ${quality.visibility}"

        if (!isText(input.appVersion, 256)) blocked += "app_version invalid"
        if (!isText(input.buildSha, 64)) blocked += "build_sha invalid"
        if (blocked.isNotEmpty()) return Result(null, blocked)

        val nominal = calibration
        val poseValidity = poseCheck!!
        val gates = buildGates(quality, input.pose)
        val overall = when {
            poseValidity == "valid" && nominal.metricValidity == "validated" &&
                gates.all { it.status == "pass" } -> "accepted"
            gates.any { it.status == "fail" } -> "rejected"
            else -> "unknown"
        }
        val limitations = mutableListOf<String>()
        limitations += input.extraLimitations.filter { it.isNotBlank() }
        if (!input.calibrated) {
            limitations += "factory intrinsics uncalibrated: poses are coarse guidance, not metric evidence"
            limitations += "nominal board geometry: print scale unmeasured"
        }
        limitations += "pose solved from a preview frame near the still, not the saved image (solve_source unknown)"

        val doc = buildJsonObject {
            put("schema", SCHEMA_ID)
            put("evidence_id", input.evidenceId)
            put("session_id", input.sessionId)
            put("source_id", input.sourceId)
            put("request_id", input.requestId)
            put("request_revision", input.requestRevision)
            put("attempt_id", input.attemptId)
            put("created_at", input.createdAt)
            put("supersedes", input.supersedes?.let { JsonPrimitive(it) } ?: JsonNull)
            put("asset", buildJsonObject {
                put("package_id", image.packageId)
                put("observation_id", image.observationId)
                put("image_id", image.imageId)
                put("asset_id", image.assetId)
                put("sha256", image.sha256)
                put("byte_length", image.byteLength)
                put("mime", image.mime)
            })
            put("camera", buildJsonObject {
                put("camera_id", camera.cameraId)
                put("physical_lens_id", camera.physicalLensId?.let { JsonPrimitive(it) } ?: JsonNull)
                put("lens_facing", camera.lensFacing)
                put("sensor_mode", camera.sensorMode)
                put("zoom", camera.zoom)
                put("focus_distance_diopters", camera.focusDistanceDiopters?.let { JsonPrimitive(it) } ?: JsonNull)
                put("focus_status", camera.focusStatus)
            })
            put("calibration", buildJsonObject {
                put("profile_id", nominal.profileId)
                put("profile_sha256", nominal.profileSha256)
                put("layout_sha256", input.calibration.layoutSha256)
                put("board_instance_id", nominal.boardInstanceId)
                put("board_instance_sha256", nominal.boardInstanceSha256)
                put("board_frame_id", input.calibration.boardFrameId)
                put("metric_validity", nominal.metricValidity)
                put("reasons", JsonArray(nominal.reasons.map { JsonPrimitive(it) }))
            })
            val uprightW: Int
            val uprightH: Int
            if (input.analysisRotationDeg == 90 || input.analysisRotationDeg == 270) {
                uprightW = input.analysisHeightPx
                uprightH = input.analysisWidthPx
            } else {
                uprightW = input.analysisWidthPx
                uprightH = input.analysisHeightPx
            }
            val scaleX = image.widthPx.toDouble() / uprightW
            val scaleY = image.heightPx.toDouble() / uprightH
            put("image_geometry", buildJsonObject {
                put("width_px", image.widthPx)
                put("height_px", image.heightPx)
                put("exif_orientation", image.exifOrientation)
                put("pixel_convention", PIXEL_CONVENTION)
                put(
                    "calibration_pixel_frame_id",
                    input.calibrationPixelFrameId
                        ?: "analysis-${input.analysisWidthPx}x${input.analysisHeightPx}-rot${input.analysisRotationDeg}",
                )
                put(
                    "reference_to_image",
                    JsonArray(listOf(scaleX, 0.0, 0.0, 0.0, scaleY, 0.0, 0.0, 0.0, 1.0).map { JsonPrimitive(it) }),
                )
                put("distortion_before_image_transform", true)
                put("transform_epoch", input.transformEpoch)
            })
            put("capture_time", buildJsonObject {
                put("sensor_start_ns", JsonNull)
                put("sensor_end_ns", JsonNull)
                put("sensor_timestamp_source", "unknown")
                put("boot_id", time.bootId)
                put("callback_utc", time.callbackUtc)
                put("callback_monotonic_ns", time.callbackMonotonicNs)
                put(
                    "exposure_utc_estimate",
                    time.exposureUtcEstimate?.let { JsonPrimitive(it) } ?: JsonNull,
                )
                put(
                    "uncertainty_ms",
                    time.uncertaintyMs?.let { JsonPrimitive(it) } ?: JsonNull,
                )
                put("sync_exchange_ref", JsonNull)
            })
            put(
                "pose",
                buildPose(
                    input.pose,
                    poseValidity,
                    fromId = "camera:${camera.cameraId}",
                    toId = "board:${nominal.boardInstanceId}:${input.calibration.boardFrameId}",
                ),
            )
            put("target", buildJsonObject {
                put("goal_sha256", target.goalBytes.sha256Hex())
                put("requested_roi", buildJsonObject {
                    put("frame", "board:${nominal.boardInstanceId}:${input.calibration.boardFrameId}")
                    put(
                        "vertices_mm",
                        JsonArray(target.roiVerticesMm.map { v -> JsonArray(v.map { JsonPrimitive(it) }) }),
                    )
                    put("basis", "board_plane")
                    put("evidence_refs", JsonArray(emptyList()))
                })
                put("subject_ref", target.subjectRef?.let { JsonPrimitive(it) } ?: JsonNull)
                put(
                    "object_pose_epoch_ref",
                    target.objectPoseEpochRef?.let { JsonPrimitive(it) } ?: JsonNull,
                )
            })
            put("quality", buildJsonObject {
                put("overall", overall)
                for (gate in gates) {
                    put(gate.name, buildJsonObject {
                        put("status", gate.status)
                        put("value", gate.value?.let { JsonPrimitive(it) } ?: JsonNull)
                        put("threshold", gate.threshold?.let { JsonPrimitive(it) } ?: JsonNull)
                        put("unit", gate.unit?.let { JsonPrimitive(it) } ?: JsonNull)
                        put("algorithm", gate.algorithm)
                        put("reason", gate.reason?.let { JsonPrimitive(it) } ?: JsonNull)
                    })
                }
                put("visibility_assessment", quality.visibility)
                put("in_frame", quality.inFrame?.let { JsonPrimitive(it) } ?: JsonNull)
                put("threshold_config_sha256", quality.thresholdConfigJson.toByteArray(Charsets.UTF_8).sha256Hex())
            })
            put("observer_relation", buildJsonObject {
                put("observer_source_id", JsonNull)
                put("run_ref", JsonNull)
                put("epoch_id", JsonNull)
                put("time_mapping_ref", JsonNull)
                put("capture_interval_video_s", JsonNull)
                put("association", "unknown")
                put("reason", input.associationReason.ifBlank { "no custody observer linked" })
            })
            put("interpretations", JsonArray(emptyList()))
            put("provenance", buildJsonObject {
                put("producer", PRODUCER)
                put("app_version", input.appVersion)
                put("build_sha", input.buildSha)
                put(
                    "algorithm_config_refs",
                    JsonArray(
                        listOf(
                            buildJsonObject {
                                put(
                                    "asset_id",
                                    uuidV5("anastylos-algo-config", input.algorithmConfigJson),
                                )
                                put(
                                    "sha256",
                                    input.algorithmConfigJson.toByteArray(Charsets.UTF_8).sha256Hex(),
                                )
                            },
                        ),
                    ),
                )
                put(
                    "input_refs",
                    JsonArray(
                        listOf(
                            buildJsonObject {
                                put("asset_id", image.imageId)
                                put("sha256", image.sha256)
                            },
                        ),
                    ),
                )
                val basis = if (input.calibrated && poseValidity == "valid") {
                    listOf("operator_testimony", "geometric_measurement")
                } else {
                    listOf("operator_testimony")
                }
                put("basis", JsonArray(basis.map { JsonPrimitive(it) }))
                put("operator_action_refs", JsonArray(emptyList()))
            })
            put("limitations", JsonArray(limitations.map { JsonPrimitive(it) }))
        }
        return Result(Json.encodeToString(JsonObject.serializer(), doc), emptyList())
    }

    private data class NominalCalibration(
        val profileId: String,
        val profileSha256: String,
        val boardInstanceId: String,
        val boardInstanceSha256: String,
        val metricValidity: String,
        val reasons: List<String>,
    )

    private fun checkCalibration(input: Calibration, blocked: MutableList<String>): NominalCalibration? {
        if (!isSha256Hex(input.layoutSha256)) blocked += "calibration.layout_sha256 must be hex64"
        if (input.boardFrameId !in BOARD_FRAMES) blocked += "board_frame_id unknown: ${input.boardFrameId}"
        if (input.metricValidity !in METRIC_VALIDITY) {
            blocked += "metric_validity unknown: ${input.metricValidity}"
        }
        if (input.reasons.size > 32 || input.reasons.any { !isText(it, 1024) }) {
            blocked += "calibration.reasons invalid"
        }
        val nominalProfile = input.profileId.isBlank()
        val nominalBoard = input.boardInstanceId.isBlank()
        if (!nominalProfile && !isUuid(input.profileId)) blocked += "calibration.profile_id must be a uuid"
        if (!nominalProfile && !isSha256Hex(input.profileSha256)) {
            blocked += "calibration.profile_sha256 must be hex64"
        }
        if (nominalProfile && input.profileSha256.isNotBlank()) {
            blocked += "nominal profile must not carry a measured sha"
        }
        if (nominalProfile && input.nominalDescriptor.isBlank()) {
            blocked += "nominal profile needs its descriptor bytes"
        }
        if (!nominalBoard && !isUuid(input.boardInstanceId)) {
            blocked += "calibration.board_instance_id must be a uuid"
        }
        if (!nominalBoard && !isSha256Hex(input.boardInstanceSha256)) {
            blocked += "calibration.board_instance_sha256 must be hex64"
        }
        if (input.metricValidity == "validated" && (nominalProfile || nominalBoard)) {
            blocked += "validated metric_validity requires measured profile and board instance"
        }
        if (blocked.isNotEmpty()) return null
        val reasons = input.reasons.toMutableList()
        val profileId: String
        val profileSha: String
        if (nominalProfile) {
            profileId = uuidV5("anastylos-nominal-profile", input.nominalDescriptor)
            profileSha = input.nominalDescriptor.toByteArray(Charsets.UTF_8).sha256Hex()
            reasons += "nominal_profile_unmeasured: factory intrinsics, not a measured calibration"
        } else {
            profileId = input.profileId
            profileSha = input.profileSha256
        }
        val boardId: String
        val boardSha: String
        if (nominalBoard) {
            val descriptor = "nominal-board-instance:${input.boardFrameId}:${input.layoutSha256}"
            boardId = uuidV5("anastylos-nominal-board", descriptor)
            boardSha = descriptor.toByteArray(Charsets.UTF_8).sha256Hex()
            reasons += "nominal_board_instance_unmeasured: print scale not measured"
        } else {
            boardId = input.boardInstanceId
            boardSha = input.boardInstanceSha256
            reasons += "measured board instance pinned by record hash"
        }
        return NominalCalibration(profileId, profileSha, boardId, boardSha, input.metricValidity, reasons)
    }

    /** Returns the pose validity to declare, or null when blocked. */
    private fun checkPose(input: Pose, calibrated: Boolean, blocked: MutableList<String>): String? {
        val declared = when (input.estimatorStatus) {
            WireRegistration.VALID -> if (calibrated) "valid" else "degraded"
            WireRegistration.DEGRADED -> "degraded"
            WireRegistration.AMBIGUOUS -> "ambiguous"
            WireRegistration.LOST -> "lost"
            WireRegistration.UNREGISTERED -> "unregistered"
            WireRegistration.BAD_CALIBRATION -> "bad_calibration"
        }
        val matrix = input.tBC
        if (matrix == null) {
            if (declared == "valid") {
                blocked += "valid pose needs its transform"
                return null
            }
        } else {
            if (matrix.size != 16 || matrix.any { !it.isFinite() }) {
                blocked += "pose matrix must hold 16 finite doubles"
                return null
            }
            if (matrix[12] != 0.0 || matrix[13] != 0.0 || matrix[14] != 0.0 || matrix[15] != 1.0) {
                blocked += "pose matrix bottom row must be 0,0,0,1"
                return null
            }
            val rotation = listOf(
                matrix[0], matrix[1], matrix[2],
                matrix[4], matrix[5], matrix[6],
                matrix[8], matrix[9], matrix[10],
            )
            if (!PoseMath.isValidRotation(rotation)) {
                blocked += "pose rotation is not a valid det+1 rotation"
                return null
            }
            // T_B_C maps camera -> board, so the translation part
            // IS the camera center in board coordinates; with +z
            // into the board plane, the camera must sit at z < 0.
            val centerZ = matrix[11]
            if (!centerZ.isFinite() || centerZ >= 0) {
                blocked += "pose puts the camera behind the board plane"
                return null
            }
        }
        if (input.rmsPx != null && (!input.rmsPx.isFinite() || input.rmsPx < 0)) {
            blocked += "rms_px invalid"
            return null
        }
        if (input.detectedMarkerIds.size > 100 || input.detectedMarkerIds.any { it < 0 }) {
            blocked += "detected_marker_ids invalid"
            return null
        }
        if (input.residualReferenceWidthPx !in 1..32768) {
            blocked += "residual_reference_width_px out of range"
            return null
        }
        val age = input.sampleAgeMs
        if (age != null && (!age.isFinite() || age < 0)) {
            blocked += "sample_age_ms invalid"
            return null
        }
        if (age != null && age > SAMPLE_AGE_GATE_MS) {
            blocked += "sample_age_ms above the 150 ms exposure-association gate"
            return null
        }
        if (!isSha256Hex(input.imageSha256)) {
            blocked += "pose.image_sha256 must be hex64"
            return null
        }
        return declared
    }

    private data class Gate(
        val name: String,
        val status: String,
        val value: Double?,
        val threshold: Double?,
        val unit: String?,
        val algorithm: String,
        val reason: String?,
    )

    private fun buildGates(quality: Quality, pose: Pose): List<Gate> {
        val rmsOk = quality.rmsPx != null && quality.rmsPx.isFinite() && quality.rmsPx >= 0 &&
            quality.rmsGatePx.isFinite()
        val poseGate = Gate(
            name = "pose",
            status = when {
                !rmsOk -> "unknown"
                quality.rmsPx!! <= quality.rmsGatePx -> "pass"
                else -> "fail"
            },
            value = quality.rmsPx?.takeIf { it.isFinite() && it >= 0 },
            threshold = quality.rmsGatePx.takeIf { it.isFinite() },
            unit = "px",
            algorithm = "reprojection-rms",
            reason = if (!rmsOk) "no rms measurement" else null,
        )
        val medianOk = quality.motionMedianPx?.let { it.isFinite() && it >= 0 } == true
        val p90Ok = quality.motionP90Px?.let { it.isFinite() && it >= 0 } == true
        val motionGate = Gate(
            name = "motion",
            status = when {
                !medianOk || !p90Ok -> "unknown"
                quality.motionMedianPx!! <= quality.motionMedianGatePx &&
                    quality.motionP90Px!! <= quality.motionP90GatePx -> "pass"
                else -> "fail"
            },
            value = quality.motionP90Px?.takeIf { it.isFinite() && it >= 0 },
            threshold = quality.motionP90GatePx.takeIf { it.isFinite() },
            unit = "px",
            algorithm = "corner-displacement-p90",
            reason = if (!medianOk || !p90Ok) "no motion measurement" else null,
        )
        val sharp = quality.sharpness
        val ref = quality.sharpnessReference
        val focusGate = Gate(
            name = "focus",
            status = when {
                sharp == null || !sharp.isFinite() || sharp < 0 ||
                    ref == null || !ref.isFinite() || ref <= 0 -> "unknown"
                sharp / ref >= quality.sharpnessRatioGate -> "pass"
                else -> "fail"
            },
            value = sharp?.takeIf { it.isFinite() && it >= 0 },
            threshold = ref?.takeIf { it.isFinite() && it > 0 }?.times(quality.sharpnessRatioGate),
            unit = "laplacian-var",
            algorithm = "laplacian-variance-ratio",
            reason = if (ref == null) "sharpness reference unset" else null,
        )
        val dark = quality.darkFraction
        val sat = quality.saturatedFraction
        val exposureBad = dark != null && sat != null && dark.isFinite() && sat.isFinite() &&
            (sat > quality.badExposureGate || dark > 0.5)
        val exposureGate = Gate(
            name = "exposure",
            status = when {
                dark == null || sat == null || !dark.isFinite() || !sat.isFinite() -> "unknown"
                exposureBad -> "fail"
                else -> "pass"
            },
            value = sat?.takeIf { it.isFinite() },
            threshold = quality.badExposureGate.takeIf { it.isFinite() },
            unit = "fraction",
            algorithm = "saturated-fraction",
            reason = if (exposureBad) "dark=$dark saturated=$sat" else null,
        )
        val detected = pose.detectedMarkerIds.size
        val visibilityGate = Gate(
            name = "visibility",
            status = when {
                detected >= 3 -> "pass"
                detected > 0 -> "unknown"
                else -> "fail"
            },
            value = detected.toDouble(),
            threshold = 3.0,
            unit = "markers",
            algorithm = "detected-marker-count",
            reason = if (detected == 0) "no markers in the solving frame" else null,
        )
        return listOf(poseGate, motionGate, focusGate, exposureGate, visibilityGate)
    }

    private fun buildPose(pose: Pose, validity: String, fromId: String, toId: String): JsonObject {
        val matrix = pose.tBC
        // ITERATIVE PnP has no outlier rejection: every detected
        // marker constrains the solve, so inliers == detected.
        val detected = pose.detectedMarkerIds
        val ratio = if (detected.isEmpty()) null else 1.0
        return buildJsonObject {
            put("validity", validity)
            if (matrix == null) {
                put("T_board_camera", JsonNull)
            } else {
                put("T_board_camera", buildJsonObject {
                    put("from", fromId)
                    put("to", toId)
                    put("units", "mm")
                    put("matrix", JsonArray(matrix.map { JsonPrimitive(it) }))
                })
            }
            put("method", SOLVER_METHOD)
            put("version", SOLVER_VERSION)
            put("solve_source", "unknown")
            put("image_sha256", pose.imageSha256)
            put("sample_sensor_ns", JsonNull)
            put("sample_age_ms", pose.sampleAgeMs?.let { JsonPrimitive(it) } ?: JsonNull)
            put("bracketing_samples", JsonArray(emptyList()))
            put("metrics", buildJsonObject {
                put("detected_marker_ids", JsonArray(detected.map { JsonPrimitive(it) }))
                put("inlier_marker_ids", JsonArray(detected.map { JsonPrimitive(it) }))
                put("rms_px", pose.rmsPx?.let { JsonPrimitive(it) } ?: JsonNull)
                put("max_residual_px", pose.maxResidualPx?.let { JsonPrimitive(it) } ?: JsonNull)
                put("residual_reference_width_px", pose.residualReferenceWidthPx)
                put("inlier_ratio", ratio?.let { JsonPrimitive(it) } ?: JsonNull)
                put("alternate_solution_separation_mm", JsonNull)
                put("alternate_solution_separation_deg", JsonNull)
                val uncertain = pose.positionUncertaintyMm != null && pose.angleUncertaintyDeg != null
                put("uncertainty_method", if (uncertain) pose.uncertaintyMethod else "none")
                put(
                    "position_uncertainty_mm",
                    pose.positionUncertaintyMm?.takeIf { uncertain }
                        ?.let { v -> JsonArray(v.map { JsonPrimitive(it) }) } ?: JsonNull,
                )
                put("angle_uncertainty_deg", pose.angleUncertaintyDeg?.takeIf { uncertain }?.let { JsonPrimitive(it) } ?: JsonNull)
                put("corner_evidence", JsonNull)
            })
            put("reasons", JsonArray(emptyList()))
        }
    }

    private val MIME_TYPES = setOf("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
    private val LENS_FACINGS = setOf("back", "front", "external")
    private val FOCUS_STATUSES = setOf("locked", "converged", "searching", "unknown")
    private val BOARD_FRAMES = setOf("surface_a3", "surface_a4", "fixture_front", "fixture_back")
    private val METRIC_VALIDITY = setOf("validated", "nominal_only", "invalid", "unknown")
    private val VISIBILITY = setOf("visible", "partially_occluded", "occluded", "unknown")

    private fun checkUuid(value: String, what: String, blocked: MutableList<String>) {
        if (!isUuid(value)) blocked += "$what must be a uuid"
    }

    private fun isUuid(value: String): Boolean =
        try {
            java.util.UUID.fromString(value)
            true
        } catch (_: IllegalArgumentException) {
            false
        }

    private fun isUtcZ(value: String): Boolean =
        try {
            value.endsWith("Z") && java.time.Instant.parse(value) != null
        } catch (_: Exception) {
            false
        }

    private fun isNs(value: String): Boolean =
        value.length in 1..20 && value.all { it.isDigit() }

    private fun isText(value: String?, max: Int): Boolean =
        value != null && value.isNotEmpty() && value.length <= max
}
