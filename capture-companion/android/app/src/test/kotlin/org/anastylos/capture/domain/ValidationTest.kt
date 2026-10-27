package org.anastylos.capture.domain

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class ValidationTest {
    @Test
    fun `valid fixtures pass every level`() {
        assertThat(validateSessionDraft(Fixtures.session())).isEmpty()
        assertThat(validateObservationDraft(Fixtures.observation())).isEmpty()
        assertThat(validateObservationForExport(Fixtures.observation())).isEmpty()
        assertThat(validateImage(Fixtures.image())).isEmpty()
    }

    @Test
    fun `session needs a UUID and a name`() {
        assertThat(validateSessionDraft(Fixtures.session().copy(sessionId = "x")))
            .containsExactly(ValidationIssue("session_id", "must be a UUID"))
        assertThat(validateSessionDraft(Fixtures.session().copy(name = "  ")))
            .containsExactly(ValidationIssue("name", "must not be blank"))
    }

    @Test
    fun `markings need text and observed state`() {
        val blank = Fixtures.observation().copy(markings = listOf(Marking("ok"), Marking(" ")))
        assertThat(validateObservationDraft(blank).map { it.path }).contains("markings[1]")

        val interpreted = Fixtures.observation().copy(
            markings = listOf(Marking("x", EpistemicState.INTERPRETED)),
        )
        assertThat(validateObservationDraft(interpreted).map { it.path }).contains("markings[0]")
    }

    @Test
    fun `measurements need a number and an explicit unit`() {
        val unitless = Fixtures.observation().copy(
            measurements = listOf(Measurement("length", 12.0, "", MeasurementProvenance.OPERATOR_MEASURED)),
        )
        assertThat(validateObservationDraft(unitless).map { it.path }).contains("measurements[0]")

        val labeled = Fixtures.observation().copy(
            measurements = listOf(Measurement("length", 12.0, "mm", MeasurementProvenance.OPERATOR_MEASURED)),
        )
        assertThat(validateObservationDraft(labeled)).isEmpty()
    }

    @Test
    fun `category and quantity mirror the confirmed-or-unknown rule`() {
        val suggested = Fixtures.observation().copy(
            category = ConfirmedField("resistor", EpistemicState.SUGGESTED),
        )
        assertThat(validateObservationDraft(suggested).map { it.path }).contains("category")

        val valuedUnknown = Fixtures.observation().copy(
            quantity = ConfirmedField(4L, EpistemicState.UNKNOWN),
        )
        assertThat(validateObservationDraft(valuedUnknown).map { it.path }).contains("quantity")

        val zero = Fixtures.observation().copy(
            quantity = ConfirmedField(0L, EpistemicState.CONFIRMED),
        )
        assertThat(validateObservationDraft(zero).map { it.path }).contains("quantity")

        val confirmed = Fixtures.observation().copy(
            category = ConfirmedField("resistor", EpistemicState.CONFIRMED),
            quantity = ConfirmedField(4L, EpistemicState.CONFIRMED),
        )
        assertThat(validateObservationDraft(confirmed)).isEmpty()
    }

    @Test
    fun `candidates need label provider and model`() {
        val bad = Fixtures.observation().copy(
            candidates = listOf(
                ObservationCandidate(
                    rank = 0,
                    label = "",
                    confidence = null,
                    supportingObservations = emptyList(),
                    uncertainty = null,
                    provider = "p",
                    model = "m",
                ),
            ),
        )
        assertThat(validateObservationDraft(bad).map { it.path }).contains("candidates[0]")

        val good = Fixtures.observation().copy(
            candidates = listOf(
                ObservationCandidate(
                    rank = 0,
                    label = "resistor",
                    confidence = 0.9,
                    supportingObservations = emptyList(),
                    uncertainty = null,
                    provider = "provider",
                    model = "model",
                ),
            ),
        )
        assertThat(validateObservationDraft(good)).isEmpty()
    }

    @Test
    fun `imageless observation is a valid draft but export-blocked`() {
        val bare = Fixtures.observation().copy(images = emptyList())
        assertThat(validateObservationDraft(bare)).isEmpty()
        assertThat(validateObservationForExport(bare).map { it.path }).contains("images")
    }

    @Test
    fun `image validation pins every evidence field`() {
        val base = Fixtures.image()
        assertThat(validateImage(base.copy(mime = "image/gif"))).isNotEmpty()
        assertThat(validateImage(base.copy(mime = "image/heic"))).isEmpty()
        assertThat(validateImage(base.copy(filename = "../evil.jpg"))).isNotEmpty()
        assertThat(validateImage(base.copy(filename = "a/b.jpg"))).isNotEmpty()
        assertThat(validateImage(base.copy(byteLength = 0))).isNotEmpty()
        assertThat(validateImage(base.copy(sha256 = "zz"))).isNotEmpty()
        assertThat(validateImage(base.copy(crop = CropRect(0.0, 0.0, 0.0, 0.5)))).isNotEmpty()
        assertThat(validateImage(base.copy(crop = CropRect(0.1, 0.1, 0.5, 0.5)))).isEmpty()
    }

    @Test
    fun `oversized files are export-blocked`() {
        val big = Fixtures.observation().copy(
            images = listOf(Fixtures.image().copy(byteLength = MAX_FILE_BYTES + 1)),
        )
        assertThat(validateObservationDraft(big)).isEmpty()
        assertThat(validateObservationForExport(big).map { it.path })
            .contains("images[${Fixtures.IMAGE_ID}].byte_length")
    }
}
