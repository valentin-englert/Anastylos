package org.anastylos.capture.domain

import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.time.temporal.ChronoUnit

/** Current UTC time at millisecond precision, the only clock the app stores. */
fun nowUtc(): Instant = Instant.now().truncatedTo(ChronoUnit.MILLIS)

private val IsoMillis = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC)

/**
 * Millis-exact ISO-8601 UTC, spelled exactly like the PWA's
 * `Date.toISOString()` so manifests compare byte-for-byte.
 */
fun Instant.toIsoUtc(): String = IsoMillis.format(this)

private val ISO_UTC_RE = Regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,9})?Z$")

/**
 * Strict ISO-8601 UTC parsing with the validator's exact shape (seconds
 * required, `Z` only). Anything else is rejected, never coerced.
 */
fun parseIsoUtc(value: String): Instant? {
    if (!ISO_UTC_RE.matches(value)) return null
    return try {
        Instant.parse(value)
    } catch (_: DateTimeParseException) {
        null
    }
}
