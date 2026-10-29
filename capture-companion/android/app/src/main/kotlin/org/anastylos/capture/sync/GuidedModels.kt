package org.anastylos.capture.sync

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Guided-control wire models. Field names mirror the frozen G0
 * `control.schema.json` exactly; states stay unparsed strings so an
 * unknown spelling fails closed at the mapping layer instead of
 * crashing the decoder. See `GuidedControlClient` for the exchange
 * semantics and the nine `guided-wire-*.json` fixtures.
 */

@Serializable
data class GuidedCapabilities(
    @SerialName("source_id") val sourceId: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
    val stale: Boolean = true,
    @SerialName("camera_ids") val cameraIds: List<String> = emptyList(),
    @SerialName("resolutions_px") val resolutionsPx: List<List<Int>> = emptyList(),
    @SerialName("calibration_ids") val calibrationIds: List<String> = emptyList(),
    @SerialName("target_kinds") val targetKinds: List<String> = emptyList(),
    @SerialName("quality_profile_ids") val qualityProfileIds: List<String> = emptyList(),
    @SerialName("app_version") val appVersion: String = "",
    @SerialName("protocol_version") val protocolVersion: Int? = null,
)

@Serializable
data class GuidedSession(
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("source_id") val sourceId: String = "",
    @SerialName("grant_id") val grantId: String? = null,
    val revision: Int = 0,
    val state: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("expires_at") val expiresAt: String = "",
    @SerialName("lease_expires_at") val leaseExpiresAt: String? = null,
    @SerialName("lease_remaining_ms") val leaseRemainingMs: Long = 0,
    val capabilities: GuidedCapabilities = GuidedCapabilities(),
    @SerialName("active_request_id") val activeRequestId: String? = null,
    @SerialName("consent_revision") val consentRevision: Int = 0,
)

@Serializable
data class GuidedTransform(
    val from: String = "",
    val to: String = "",
    val units: String = "",
    val matrix: List<Double> = emptyList(),
)

@Serializable
data class GuidedTarget(
    val kind: String = "",
    @SerialName("T_B_C") val tbc: GuidedTransform = GuidedTransform(),
    @SerialName("translation_tolerance_mm") val translationToleranceMm: List<Double> = emptyList(),
    @SerialName("rotation_tolerance_deg") val rotationToleranceDeg: Double = 0.0,
    val roi: JsonObject? = null,
)

@Serializable
data class GuidedViewRequest(
    @SerialName("request_id") val requestId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("source_id") val sourceId: String = "",
    val revision: Int = 0,
    @SerialName("operation_id") val operationId: String = "",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("expires_at") val expiresAt: String = "",
    @SerialName("target_label") val targetLabel: String = "",
    @SerialName("board_id") val boardId: String = "",
    @SerialName("calibration_id") val calibrationId: String = "",
    val target: GuidedTarget = GuidedTarget(),
    @SerialName("quality_profile_id") val qualityProfileId: String = "",
    val status: String = "",
    @SerialName("board_registration_epoch_id") val boardRegistrationEpochId: String = "",
    @SerialName("object_pose_epoch_id") val objectPoseEpochId: String = "",
    @SerialName("cad_context") val cadContext: JsonObject? = null,
    val reason: String = "",
)

@Serializable
data class GuidedSourceEvent(
    @SerialName("event_id") val eventId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("source_id") val sourceId: String = "",
    @SerialName("request_id") val requestId: String? = null,
    @SerialName("request_revision") val requestRevision: Int? = null,
    val kind: String = "",
    @SerialName("occurred_at") val occurredAt: String = "",
    @SerialName("monotonic_ns") val monotonicNs: String = "",
    @SerialName("attempt_id") val attemptId: String? = null,
    val reason: String? = null,
)

@Serializable
data class GuidedEventAck(
    @SerialName("accepted_event_id") val acceptedEventId: String = "",
    @SerialName("event_seq") val eventSeq: Long = 0,
    @SerialName("session_revision") val sessionRevision: Int = 0,
)

@Serializable
data class GuidedDeviceStatus(
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("source_id") val sourceId: String = "",
    @SerialName("request_id") val requestId: String? = null,
    @SerialName("request_revision") val requestRevision: Int? = null,
    @SerialName("event_seq") val eventSeq: Long = 0,
    @SerialName("observed_at") val observedAt: String = "",
    @SerialName("device_monotonic_ns") val deviceMonotonicNs: String = "",
    val registration: String = "",
    @SerialName("guidance_state") val guidanceState: String = "",
    @SerialName("pose_age_ms") val poseAgeMs: Double? = null,
    @SerialName("translation_error_camera_mm") val translationErrorCameraMm: List<Double>? = null,
    @SerialName("rotation_error_deg") val rotationErrorDeg: Double? = null,
    @SerialName("hold_elapsed_ms") val holdElapsedMs: Long = 0,
    @SerialName("quality_blockers") val qualityBlockers: List<String> = emptyList(),
    @SerialName("queue_count") val queueCount: Int = 0,
)

@Serializable
data class GuidedStatusAck(
    @SerialName("accepted_seq") val acceptedSeq: Long = 0,
    @SerialName("server_time") val serverTime: String = "",
)

@Serializable
data class GuidedConsentInput(
    @SerialName("operation_id") val operationId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("expected_session_revision") val expectedSessionRevision: Int = 0,
    val action: String = "",
    @SerialName("consent_revision") val consentRevision: Int = 0,
    @SerialName("max_images") val maxImages: Int = 0,
    @SerialName("expires_at") val expiresAt: String = "",
    @SerialName("camera_id") val cameraId: String = "",
    @SerialName("allow_auto_upload") val allowAutoUpload: Boolean = false,
)

@Serializable
data class GuidedAttemptInput(
    @SerialName("operation_id") val operationId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("request_id") val requestId: String = "",
    @SerialName("request_revision") val requestRevision: Int = 0,
    @SerialName("attempt_id") val attemptId: String = "",
    @SerialName("consent_revision") val consentRevision: Int = 0,
    @SerialName("board_registration_epoch_id") val boardRegistrationEpochId: String = "",
    @SerialName("object_pose_epoch_id") val objectPoseEpochId: String = "",
)

@Serializable
data class GuidedAttemptAck(
    @SerialName("attempt_id") val attemptId: String = "",
    @SerialName("attempt_number") val attemptNumber: Int = 0,
    @SerialName("remaining_exposures") val remainingExposures: Int = 0,
    @SerialName("grant_id") val grantId: String = "",
    @SerialName("session_revision") val sessionRevision: Int = 0,
    val status: String = "",
)

@Serializable
data class GuidedAssetBinding(
    @SerialName("operation_id") val operationId: String = "",
    @SerialName("attempt_id") val attemptId: String = "",
    @SerialName("package_id") val packageId: String = "",
    @SerialName("observation_id") val observationId: String = "",
    @SerialName("image_id") val imageId: String = "",
    @SerialName("image_sha256") val imageSha256: String = "",
    @SerialName("package_sha256") val packageSha256: String = "",
)

@Serializable
data class GuidedAttemptDelivery(
    @SerialName("attempt_id") val attemptId: String = "",
    @SerialName("request_id") val requestId: String = "",
    @SerialName("request_revision") val requestRevision: Int = 0,
    val state: String = "",
    @SerialName("receipt_status") val receiptStatus: String? = null,
)

@Serializable
data class GuidedReceipt(
    @SerialName("receipt_id") val receiptId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("source_id") val sourceId: String = "",
    @SerialName("request_id") val requestId: String = "",
    @SerialName("request_revision") val requestRevision: Int = 0,
    @SerialName("attempt_id") val attemptId: String = "",
    @SerialName("package_id") val packageId: String = "",
    @SerialName("observation_id") val observationId: String = "",
    @SerialName("image_id") val imageId: String = "",
    @SerialName("image_sha256") val imageSha256: String = "",
    @SerialName("package_sha256") val packageSha256: String = "",
    @SerialName("sidecar_sha256") val sidecarSha256: String? = null,
    @SerialName("sidecar_revision") val sidecarRevision: Int = 0,
    @SerialName("accepted_at") val acceptedAt: String = "",
    val status: String = "",
    @SerialName("reason_codes") val reasonCodes: List<String> = emptyList(),
)

@Serializable
data class GuidedChange(
    @SerialName("change_seq") val changeSeq: Long = 0,
    val kind: String = "",
    @SerialName("session_revision") val sessionRevision: Int = 0,
    val request: GuidedViewRequest? = null,
    @SerialName("source_event") val sourceEvent: GuidedSourceEvent? = null,
    val receipt: GuidedReceipt? = null,
)

@Serializable
data class GuidedChangesPage(
    val events: List<GuidedChange> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String = "",
    @SerialName("snapshot_required") val snapshotRequired: Boolean = false,
    @SerialName("server_time") val serverTime: String = "",
    @SerialName("lease_expires_at") val leaseExpiresAt: String? = null,
    @SerialName("lease_remaining_ms") val leaseRemainingMs: Long = 0,
)

@Serializable
data class GuidedSessionSnapshot(
    val session: GuidedSession = GuidedSession(),
    val requests: List<GuidedViewRequest> = emptyList(),
    @SerialName("latest_status") val latestStatus: GuidedDeviceStatus? = null,
    val receipts: List<GuidedReceipt> = emptyList(),
)

@Serializable
data class GuidedPendingSessions(
    val sessions: List<GuidedSession> = emptyList(),
)

@Serializable
data class GuidedEnrollReply(
    @SerialName("source_id") val sourceId: String = "",
    val token: String = "",
    @SerialName("expires_at") val expiresAt: String = "",
)

@Serializable
data class GuidedRequestRevision(
    @SerialName("request_id") val requestId: String = "",
    @SerialName("request_revision") val requestRevision: Int = 0,
)

@Serializable
data class GuidedCaptureSet(
    @SerialName("capture_set_id") val captureSetId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val revision: Int = 0,
    @SerialName("created_at") val createdAt: String = "",
    val status: String = "",
    @SerialName("required_requests") val requiredRequests: List<GuidedRequestRevision> = emptyList(),
    val receipts: List<GuidedReceipt> = emptyList(),
    val omissions: List<JsonObject> = emptyList(),
    @SerialName("calibration_assets") val calibrationAssets: List<JsonObject> = emptyList(),
    @SerialName("manifest_sha256") val manifestSha256: String = "",
)

@Serializable
data class GuidedErrorDetail(
    val code: String = "",
    val message: String = "",
    @SerialName("current_revision") val currentRevision: Int? = null,
)

@Serializable
data class GuidedErrorBody(
    val error: GuidedErrorDetail? = null,
)
