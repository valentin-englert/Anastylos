package org.anastylos.capture.domain

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class TimeTest {
    @Test
    fun `nowUtc is millisecond-precise UTC`() {
        val now = nowUtc()
        assertThat(now.nano % 1_000_000).isEqualTo(0)
        assertThat(now.toIsoUtc()).endsWith("Z")
    }

    @Test
    fun `timestamps always carry millis like the PWA`() {
        assertThat(parseIsoUtc("2026-01-15T10:30:00.000Z")?.toIsoUtc())
            .isEqualTo("2026-01-15T10:30:00.000Z")
        assertThat(parseIsoUtc("2026-01-15T10:30:00Z")?.toIsoUtc())
            .isEqualTo("2026-01-15T10:30:00.000Z")
        assertThat(nowUtc().toIsoUtc()).matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{3}Z")
    }

    @Test
    fun `parseIsoUtc rejects non-UTC shapes`() {
        assertThat(parseIsoUtc("2026-01-15T10:30:00+00:00")).isNull()
        assertThat(parseIsoUtc("2026-01-15 10:30:00")).isNull()
        assertThat(parseIsoUtc("not-a-time")).isNull()
        assertThat(parseIsoUtc("")).isNull()
    }
}
