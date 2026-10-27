package org.anastylos.capture.sync

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.anastylos.capture.data.local.CandidateDto
import org.anastylos.capture.data.local.CropDto
import org.anastylos.capture.data.local.ExportRecordDto
import org.anastylos.capture.data.local.MarkingDto
import org.anastylos.capture.data.local.MeasurementDto
import org.anastylos.capture.data.local.epistemicFromWire
import org.anastylos.capture.data.local.toDto
import org.anastylos.capture.data.local.toWire
import org.anastylos.capture.domain.APP_ID
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.SCHEMA_VERSION
import org.anastylos.capture.domain.toIsoUtc

/**
 * Export-manifest vocabulary, spelled byte-for-byte like the PWA's
 * `ExportManifest` (`JSON.stringify(manifest, null, 2) + "\n"`).
 *
 * Declaration order IS the key order: every property below sits exactly
 * where the TypeScript interface lists it, including `images` last on
 * observations (the PWA spreads `{...obs, images}`).
 *
 * Two deliberate divergences from the PWA writer, both validator-neutral:
 * - Re-exported observations whose history holds a legacy file export
 *   (PWA: `via` key absent) spell it as explicit `"via": null`. The
 *   validator only requires `export_history` to be an array.
 * - Non-integral doubles (measurement values, confidence) use JVM number
 *   formatting, which can differ from JavaScript's for extreme magnitudes.
 *   Frame packages carry no such values; the validator accepts both.
 */
val ManifestJson = Json {
    prettyPrint = true
    prettyPrintIndent = "  "
    explicitNulls = true
}

@Serializable
data class TextFieldDto(
    val value: String?,
    val state: String,
)

@Serializable
data class CountFieldDto(
    val value: Long?,
    val state: String,
)

@Serializable
data class ProvenanceDto(
    val app: String,
    @SerialName("app_version") val appVersion: String,
    @SerialName("user_agent") val userAgent: String?,
)

@Serializable
data class GeneratorDto(
    val app: String,
    @SerialName("app_version") val appVersion: String,
)

@Serializable
data class SessionManifestDto(
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("session_id") val sessionId: String,
    val name: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
    @SerialName("assembly_memory_session") val assemblyMemorySession: String?,
    @SerialName("tray_default") val trayDefault: String?,
)

@Serializable
data class ImageManifestDto(
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("image_id") val imageId: String,
    @SerialName("observation_id") val observationId: String,
    val role: String,
    val filename: String,
    val mime: String,
    @SerialName("byte_length") val byteLength: Long,
    val sha256: String,
    @SerialName("captured_at") val capturedAt: String,
    val crop: CropDto?,
)

@Serializable
data class ObservationManifestDto(
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("observation_id") val observationId: String,
    @SerialName("session_id") val sessionId: String,
    @SerialName("component_reference") val componentReference: String?,
    @SerialName("tray_reference") val trayReference: String?,
    @SerialName("captured_at") val capturedAt: String,
    @SerialName("capture_source") val captureSource: String,
    @SerialName("review_state") val reviewState: String,
    @SerialName("operator_notes") val operatorNotes: String?,
    val markings: List<MarkingDto>,
    val category: TextFieldDto,
    val measurements: List<MeasurementDto>,
    val quantity: CountFieldDto,
    val candidates: List<CandidateDto>,
    val provenance: ProvenanceDto,
    @SerialName("export_history") val exportHistory: List<ExportRecordDto>,
    val images: List<ImageManifestDto>,
)

@Serializable
data class ManifestFileDto(
    val path: String,
    val kind: String,
    @SerialName("observation_id") val observationId: String,
    @SerialName("image_id") val imageId: String,
    val sha256: String,
    val bytes: Long,
    val mime: String,
)

@Serializable
data class ManifestDto(
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("package_id") val packageId: String,
    @SerialName("exported_at") val exportedAt: String,
    val generator: GeneratorDto,
    val sessions: List<SessionManifestDto>,
    val observations: List<ObservationManifestDto>,
    val files: List<ManifestFileDto>,
)

@Serializable
data class CropEntryDto(
    val crop: CropDto,
)

@Serializable
data class AnnotationsDto(
    val crops: Map<String, CropEntryDto>,
)

fun ConfirmedField<String>.toTextDto() = TextFieldDto(value, state.toWire())

fun ConfirmedField<Long>.toCountDto() = CountFieldDto(value, state.toWire())

fun TextFieldDto.toDomainField() = ConfirmedField(value, epistemicFromWire(state))

fun CountFieldDto.toDomainField() = ConfirmedField(value, epistemicFromWire(state))

fun CaptureSession.toManifestDto() = SessionManifestDto(
    schemaVersion = SCHEMA_VERSION,
    sessionId = sessionId,
    name = name,
    createdAt = createdAt.toIsoUtc(),
    updatedAt = updatedAt.toIsoUtc(),
    assemblyMemorySession = assemblyMemorySession,
    trayDefault = trayDefault,
)

fun EvidenceImage.toManifestDto() = ImageManifestDto(
    schemaVersion = SCHEMA_VERSION,
    imageId = imageId,
    observationId = observationId,
    role = role.toWire(),
    filename = filename,
    mime = mime,
    byteLength = byteLength,
    sha256 = sha256,
    capturedAt = capturedAt.toIsoUtc(),
    crop = crop?.toDto(),
)

/**
 * The observation's own capture-time [ComponentObservation.appVersion] is
 * preserved verbatim in `provenance`, exactly like the PWA's `{...obs}`.
 * Only the manifest `generator` block takes the export-time version.
 */
fun ComponentObservation.toManifestDto() = ObservationManifestDto(
    schemaVersion = SCHEMA_VERSION,
    observationId = observationId,
    sessionId = sessionId,
    componentReference = componentReference,
    trayReference = trayReference,
    capturedAt = capturedAt.toIsoUtc(),
    captureSource = captureSource.toWire(),
    reviewState = reviewState.toWire(),
    operatorNotes = operatorNotes,
    markings = markings.map { it.toDto() },
    category = category.toTextDto(),
    measurements = measurements.map { it.toDto() },
    quantity = quantity.toCountDto(),
    candidates = candidates.map { it.toDto() },
    provenance = ProvenanceDto(app = APP_ID, appVersion = appVersion, userAgent = userAgent),
    exportHistory = exportHistory.map { it.toDto() },
    images = images.map { it.toManifestDto() },
)
