package org.anastylos.capture.sync

import java.net.URI
import java.time.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

/**
 * Versioned QR pairing payload (v1), the exact contract
 * `build_qr_payload` in `anastylos_capture_receiver.py` emits:
 * `{"p":"anastylos-pair","v":1,"w":…,"u":…,"s":…,"c":…,"e":…}`.
 *
 * Scanned QR contents are untrusted input: [parseQrPayload] rejects
 * anything that is not exactly this shape — wrong protocol or version,
 * missing or *extra* keys, non-https endpoints, malformed pins, and
 * expired timestamps. The pairing code itself is only sanity-checked
 * here (non-empty, printable, bounded); the receiver's rate-limited
 * redeem endpoint is the authority on whether it is real.
 */
data class QrPairPayload(
    /** Advisory workstation id, shown for confirmation and echoed back by the server. */
    val workstationId: String,
    /** The exact https base URL as scanned (host + explicit or default port). */
    val baseUrl: String,
    val host: String,
    val port: Int,
    /** 32 raw SPKI SHA-256 bytes: the TLS trust anchor for this pairing. */
    val spkiSha256: ByteArray,
    /** Whitespace-normalized pairing code, redeemed once via `/v1/pair/qr`. */
    val pairingCode: String,
    val expiresAt: Instant,
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is QrPairPayload) return false
        return workstationId == other.workstationId && baseUrl == other.baseUrl &&
            host == other.host && port == other.port && spkiSha256.contentEquals(other.spkiSha256) &&
            pairingCode == other.pairingCode && expiresAt == other.expiresAt
    }

    override fun hashCode(): Int {
        var result = workstationId.hashCode()
        result = 31 * result + baseUrl.hashCode()
        result = 31 * result + host.hashCode()
        result = 31 * result + port
        result = 31 * result + spkiSha256.contentHashCode()
        result = 31 * result + pairingCode.hashCode()
        result = 31 * result + expiresAt.hashCode()
        return result
    }
}

class QrPayloadException(message: String) : IllegalArgumentException(message)

private val REQUIRED_KEYS = setOf("p", "v", "w", "u", "s", "c", "e")
private const val MAX_QR_CHARS = 2048
private const val MAX_FUTURE_SKEW_SECONDS = 24L * 3600

private fun JsonObject.string(key: String): String {
    val value = get(key) as? JsonPrimitive
        ?: throw QrPayloadException("'$key' must be a string")
    if (!value.isString) throw QrPayloadException("'$key' must be a string")
    return value.content
}

/**
 * Parse and strictly validate scanned QR text.
 *
 * @param now Injected clock for the expiry check (tests pin it).
 * @throws QrPayloadException on anything outside the v1 contract.
 */
fun parseQrPayload(text: String, now: Instant = Instant.now()): QrPairPayload {
    if (text.length > MAX_QR_CHARS) throw QrPayloadException("QR text exceeds $MAX_QR_CHARS chars")
    val root = try {
        Json.parseToJsonElement(text) as? JsonObject
    } catch (_: Exception) {
        null
    } ?: throw QrPayloadException("QR text is not a JSON object")
    if (root.keys != REQUIRED_KEYS) throw QrPayloadException("QR payload must hold exactly p/v/w/u/s/c/e")

    val protocol = root.string("p")
    if (protocol != "anastylos-pair") throw QrPayloadException("unknown pairing protocol '$protocol'")
    val version = (root["v"] as? JsonPrimitive)?.intOrNull
    if (version != 1) throw QrPayloadException("unsupported pairing version")

    val workstationId = root.string("w")
    if (workstationId.isBlank() || workstationId.length > 128 ||
        workstationId.any { it.isISOControl() }
    ) {
        throw QrPayloadException("bad workstation id")
    }

    val baseUrl = root.string("u")
    val uri = try {
        URI(baseUrl)
    } catch (_: Exception) {
        throw QrPayloadException("bad endpoint URL")
    }
    if (!uri.scheme.equals("https", ignoreCase = true)) throw QrPayloadException("endpoint must be https")
    val host = uri.host ?: throw QrPayloadException("endpoint needs a host")
    if (uri.userInfo != null) throw QrPayloadException("endpoint must not carry user info")
    if (uri.query != null || uri.fragment != null) throw QrPayloadException("endpoint takes no query")
    val path = uri.path
    if (path != null && path.isNotEmpty() && path != "/") {
        throw QrPayloadException("endpoint takes no path")
    }
    val port = if (uri.port == -1) 443 else uri.port
    if (port !in 1..65535) throw QrPayloadException("bad endpoint port")

    val spkiHex = root.string("s")
    if (spkiHex.length != 64 || spkiHex.any { it !in '0'..'9' && it !in 'a'..'f' }) {
        throw QrPayloadException("SPKI pin must be 64 lowercase hex chars")
    }
    val spki = ByteArray(32) { i ->
        ((spkiHex[i * 2].digitToInt(16) shl 4) or spkiHex[i * 2 + 1].digitToInt(16)).toByte()
    }

    val code = root.string("c").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
    if (code.isEmpty() || code.length > 200 || code.any { it.isISOControl() || it.code > 0x7E }) {
        throw QrPayloadException("bad pairing code")
    }

    val expiresSeconds = (root["e"] as? JsonPrimitive)?.longOrNull
        ?: throw QrPayloadException("expiry must be unix seconds")
    if (expiresSeconds <= 0) throw QrPayloadException("expiry must be positive")
    val expiresAt = Instant.ofEpochSecond(expiresSeconds)
    if (!expiresAt.isAfter(now)) throw QrPayloadException("QR code expired; ask for a fresh one")
    if (expiresAt.isAfter(now.plusSeconds(MAX_FUTURE_SKEW_SECONDS))) {
        throw QrPayloadException("expiry implausibly far in the future")
    }

    return QrPairPayload(
        workstationId = workstationId,
        baseUrl = baseUrl,
        host = host,
        port = port,
        spkiSha256 = spki,
        pairingCode = code,
        expiresAt = expiresAt,
    )
}
