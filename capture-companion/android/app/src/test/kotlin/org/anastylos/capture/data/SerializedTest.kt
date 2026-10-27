package org.anastylos.capture.data

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.builtins.ListSerializer
import org.anastylos.capture.data.local.CandidateDto
import org.anastylos.capture.data.local.CropDto
import org.anastylos.capture.data.local.EvidenceJson
import org.anastylos.capture.data.local.ExportRecordDto
import org.anastylos.capture.data.local.MarkingDto
import org.anastylos.capture.data.local.MeasurementDto
import org.anastylos.capture.data.local.toDomain
import org.anastylos.capture.data.local.toDto
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.ExportRecord
import org.anastylos.capture.domain.Marking
import org.anastylos.capture.domain.Measurement
import org.anastylos.capture.domain.MeasurementProvenance
import org.anastylos.capture.domain.ObservationCandidate
import org.anastylos.capture.domain.Transport
import org.junit.Test
import java.time.Instant

class SerializedTest {
    @Test
    fun `measurement wire spelling matches the schema`() {
        val dto = Measurement("length", 12.0, "mm", MeasurementProvenance.OPERATOR_MEASURED).toDto()
        assertThat(EvidenceJson.encodeToString(MeasurementDto.serializer(), dto))
            .isEqualTo("""{"label":"length","value":12.0,"unit":"mm","provenance":"operator-measured"}""")
        assertThat(dto.toDomain()).isEqualTo(
            Measurement("length", 12.0, "mm", MeasurementProvenance.OPERATOR_MEASURED),
        )
    }

    @Test
    fun `markings carry observed state`() {
        val dto = Marking("R12").toDto()
        assertThat(EvidenceJson.encodeToString(MarkingDto.serializer(), dto))
            .isEqualTo("""{"text":"R12","state":"observed"}""")
        assertThat(dto.toDomain()).isEqualTo(Marking("R12", EpistemicState.OBSERVED))
    }

    @Test
    fun `candidates are suggested by construction`() {
        val candidate = ObservationCandidate(
            rank = 0,
            label = "resistor",
            confidence = 0.9,
            supportingObservations = listOf("22222222-2222-4222-8222-222222222222"),
            uncertainty = null,
            provider = "provider",
            model = "model",
        )
        val dto = candidate.toDto()
        assertThat(EvidenceJson.encodeToString(CandidateDto.serializer(), dto)).isEqualTo(
            """{"rank":0,"label":"resistor","confidence":0.9,"supporting_observations":["22222222-2222-4222-8222-222222222222"],"provider":"provider","model":"model","state":"suggested"}""",
        )
        assertThat(dto.toDomain()).isEqualTo(candidate)
    }

    @Test
    fun `non-suggested candidate DTOs are refused`() {
        try {
            CandidateDto(
                rank = 0,
                label = "x",
                supportingObservations = emptyList(),
                provider = "p",
                model = "m",
                state = "confirmed",
            ).toDomain()
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
            // Expected: suggestions can never arrive confirmed.
        }
    }

    @Test
    fun `export records spell sync explicitly and file as absent`() {
        val sync = ExportRecord("11111111-1111-4111-8111-111111111111", Instant.parse("2026-01-15T10:30:00Z"), Transport.SYNC)
        assertThat(EvidenceJson.encodeToString(ExportRecordDto.serializer(), sync.toDto()))
            .isEqualTo("""{"package_id":"11111111-1111-4111-8111-111111111111","exported_at":"2026-01-15T10:30:00.000Z","via":"sync"}""")
        val file = ExportRecord("11111111-1111-4111-8111-111111111111", Instant.parse("2026-01-15T10:30:00Z"), null)
        val decoded = EvidenceJson.decodeFromString(
            ListSerializer(ExportRecordDto.serializer()),
            EvidenceJson.encodeToString(ListSerializer(ExportRecordDto.serializer()), listOf(file.toDto())),
        )
        assertThat(decoded.single().toDomain()).isEqualTo(file)

        val legacy = EvidenceJson.decodeFromString(
            ExportRecordDto.serializer(),
            """{"package_id":"11111111-1111-4111-8111-111111111111","exported_at":"2026-01-15T10:30:00Z"}""",
        )
        assertThat(legacy.toDomain().via).isNull()
    }

    @Test
    fun `crop round-trips`() {
        val dto = CropDto(0.1, 0.2, 0.5, 0.4)
        val text = EvidenceJson.encodeToString(CropDto.serializer(), dto)
        assertThat(EvidenceJson.decodeFromString(CropDto.serializer(), text)).isEqualTo(dto)
    }
}
