package org.anastylos.capture.data.local

import java.time.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.anastylos.capture.domain.CropRect
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.ExportRecord
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.Marking
import org.anastylos.capture.domain.Measurement
import org.anastylos.capture.domain.MeasurementProvenance
import org.anastylos.capture.domain.ObservationCandidate
import org.anastylos.capture.domain.ReviewState
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.Transport
import org.anastylos.capture.domain.parseIsoUtc
import org.anastylos.capture.domain.toIsoUtc

/**
 * Wire-exact DTOs for the nested evidence structures. These serve the Room
 * JSON columns today and the export manifest tomorrow: one vocabulary,
 * spelled exactly as SCHEMA.md and the Python validator require.
 */
val EvidenceJson = Json {
    explicitNulls = false
}

fun EpistemicState.toWire(): String = name.lowercase()

fun MeasurementProvenance.toWire(): String = name.lowercase().replace('_', '-')

fun ReviewState.toWire(): String = name.lowercase()

fun CaptureSource.toWire(): String = name.lowercase()

fun ImageRole.toWire(): String = name.lowercase()

/** FILE transports serialize as absent, exactly like legacy file exports. */
fun Transport.toWire(): String? = if (this == Transport.SYNC) "sync" else null

fun epistemicFromWire(value: String): EpistemicState =
    EpistemicState.valueOf(value.uppercase())

fun provenanceFromWire(value: String): MeasurementProvenance =
    MeasurementProvenance.valueOf(value.uppercase().replace('-', '_'))

fun reviewStateFromWire(value: String): ReviewState =
    ReviewState.valueOf(value.uppercase())

fun captureSourceFromWire(value: String): CaptureSource =
    CaptureSource.valueOf(value.uppercase())

fun imageRoleFromWire(value: String): ImageRole =
    ImageRole.valueOf(value.uppercase())

fun transportFromWire(value: String?): Transport? =
    if (value == "sync") Transport.SYNC else null

fun instantToWire(instant: Instant): String = instant.toIsoUtc()

fun instantFromWire(value: String): Instant =
    parseIsoUtc(value) ?: throw IllegalArgumentException("not ISO 8601 UTC: $value")

@Serializable
data class MarkingDto(
    val text: String,
    val state: String,
)

@Serializable
data class MeasurementDto(
    val label: String? = null,
    val value: Double,
    val unit: String,
    val provenance: String,
)

@Serializable
data class CandidateDto(
    val rank: Int,
    val label: String,
    val confidence: Double? = null,
    @SerialName("supporting_observations") val supportingObservations: List<String>,
    val uncertainty: String? = null,
    val provider: String,
    val model: String,
    /** Always "suggested". Required with no default: a missing state must fail loudly. */
    val state: String,
)

@Serializable
data class ExportRecordDto(
    @SerialName("package_id") val packageId: String,
    @SerialName("exported_at") val exportedAt: String,
    val via: String? = null,
)

@Serializable
data class CropDto(
    val x: Double,
    val y: Double,
    val width: Double,
    val height: Double,
)

fun Marking.toDto() = MarkingDto(text, state.toWire())

fun MarkingDto.toDomain() = Marking(text, epistemicFromWire(state))

fun Measurement.toDto() = MeasurementDto(label, value, unit, provenance.toWire())

fun MeasurementDto.toDomain() = Measurement(label, value, unit, provenanceFromWire(provenance))

fun ObservationCandidate.toDto() = CandidateDto(
    rank = rank,
    label = label,
    confidence = confidence,
    supportingObservations = supportingObservations,
    uncertainty = uncertainty,
    provider = provider,
    model = model,
    state = "suggested",
)

fun CandidateDto.toDomain(): ObservationCandidate {
    require(state == "suggested") { "candidates are suggested, never confirmed" }
    return ObservationCandidate(
        rank = rank,
        label = label,
        confidence = confidence,
        supportingObservations = supportingObservations,
        uncertainty = uncertainty,
        provider = provider,
        model = model,
    )
}

fun ExportRecord.toDto() = ExportRecordDto(packageId, instantToWire(exportedAt), via?.toWire())

fun ExportRecordDto.toDomain() = ExportRecord(packageId, instantFromWire(exportedAt), transportFromWire(via))

fun CropRect.toDto() = CropDto(x, y, width, height)

fun CropDto.toDomain() = CropRect(x, y, width, height)
