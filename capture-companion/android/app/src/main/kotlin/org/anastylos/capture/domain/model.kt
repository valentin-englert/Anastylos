package org.anastylos.capture.domain

import java.time.Instant
import java.util.UUID

/**
 * Evidence schema version shared with the PWA, the ZIP format and the
 * Python validator. Every shape below mirrors that contract exactly.
 */
const val SCHEMA_VERSION = 1

const val APP_ID = "anastylos-capture"

/** Validator caps, mirrored so export planning refuses what staging would. */
const val MAX_FILE_BYTES = 100L * 1024 * 1024
const val MAX_PACKAGE_BYTES = 512L * 1024 * 1024
const val MAX_MANIFEST_BYTES = 5L * 1024 * 1024
const val MAX_ENTRIES = 5000

/**
 * Ingest-time per-photo cap, matching the PWA's `MAX_PHOTO_BYTES`.
 * Stricter than the validator's staging cap on purpose: the phone
 * refuses oversized captures immediately instead of staging them.
 */
const val MAX_PHOTO_BYTES = 25L * 1024 * 1024

val IMAGE_MIMES = setOf("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")

fun newId(): String = UUID.randomUUID().toString()

private val UUID_RE =
    Regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", RegexOption.IGNORE_CASE)

fun isId(value: String): Boolean = UUID_RE.matches(value)

/** How a piece of information came to be known. Never upgraded silently. */
enum class EpistemicState {
    OBSERVED,
    INTERPRETED,
    SUGGESTED,
    CONFIRMED,
    UNKNOWN,
}

/** A value together with how strongly it is held. Absent value, explicit state. */
data class ConfirmedField<T>(
    val value: T?,
    val state: EpistemicState,
)

enum class ReviewState {
    UNREVIEWED,
    REVIEWED,
    CONFIRMED,
}

enum class CaptureSource {
    CAMERA,
    FILE,
    GALLERY,
}

enum class ImageRole {
    FRONT,
    BACK,
    DETAIL,
    OTHER,
}

enum class MeasurementProvenance {
    OPERATOR_MEASURED,
    OPERATOR_ESTIMATED,
    UNKNOWN,
}

enum class Transport {
    FILE,
    SYNC,
}

data class Marking(
    val text: String,
    val state: EpistemicState = EpistemicState.OBSERVED,
)

data class Measurement(
    val label: String?,
    val value: Double,
    val unit: String,
    val provenance: MeasurementProvenance,
)

/**
 * A model suggestion, never an identity. The wire state is always
 * "suggested" by construction: confirmation happens by copying the label
 * into category/quantity through explicit operator action.
 */
data class ObservationCandidate(
    val rank: Int,
    val label: String,
    val confidence: Double?,
    val supportingObservations: List<String>,
    val uncertainty: String?,
    val provider: String,
    val model: String,
)

data class ExportRecord(
    val packageId: String,
    val exportedAt: Instant,
    /** Null for legacy/local file exports; SYNC for workstation-confirmed transfers. */
    val via: Transport?,
)

data class CropRect(
    val x: Double,
    val y: Double,
    val width: Double,
    val height: Double,
)

data class EvidenceImage(
    val imageId: String,
    val observationId: String,
    val role: ImageRole,
    val filename: String,
    val mime: String,
    val byteLength: Long,
    val sha256: String,
    val capturedAt: Instant,
    val crop: CropRect?,
)

data class CaptureSession(
    val sessionId: String,
    val name: String,
    val createdAt: Instant,
    val updatedAt: Instant,
    val assemblyMemorySession: String?,
    val trayDefault: String?,
)

data class ComponentObservation(
    val observationId: String,
    val sessionId: String,
    val componentReference: String?,
    val trayReference: String?,
    val capturedAt: Instant,
    val captureSource: CaptureSource,
    val reviewState: ReviewState,
    val operatorNotes: String?,
    val markings: List<Marking>,
    val category: ConfirmedField<String>,
    val measurements: List<Measurement>,
    val quantity: ConfirmedField<Long>,
    val candidates: List<ObservationCandidate>,
    val appVersion: String,
    val userAgent: String?,
    val exportHistory: List<ExportRecord>,
    val images: List<EvidenceImage>,
)

data class EvidenceProvenance(
    val app: String = APP_ID,
    val appVersion: String,
    val userAgent: String?,
)
