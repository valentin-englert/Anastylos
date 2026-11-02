package org.anastylos.capture.pairing

import java.time.Instant
import org.anastylos.capture.sync.QrPayloadException
import org.anastylos.capture.sync.parseQrPayload

private val SESSION_ID = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

/** The first session id (UUID) inside a QR payload, whether bare, in a link or in JSON. */
fun sessionIdFromQr(text: String?): String? = text?.let { SESSION_ID.find(it)?.value?.lowercase() }

/** What a scanned QR code is for; one scanner reads both kinds. */
sealed interface ScanKind {
    /** The workstation's pairing code. */
    data object Pairing : ScanKind

    /** A guided session to attach to. */
    data class Session(val id: String) : ScanKind

    /** Anything else. */
    data object Unknown : ScanKind
}

fun classifyScan(text: String?, now: Instant = Instant.now()): ScanKind {
    val trimmed = text?.trim().orEmpty()
    if (trimmed.isEmpty()) return ScanKind.Unknown
    try {
        parseQrPayload(trimmed, now)
        return ScanKind.Pairing
    } catch (_: QrPayloadException) {
        // not a pairing code: it may still be a session code
    }
    return sessionIdFromQr(trimmed)?.let(ScanKind::Session) ?: ScanKind.Unknown
}
