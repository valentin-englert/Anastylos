package org.anastylos.capture.domain

import java.io.File
import java.io.InputStream
import java.security.MessageDigest

private const val HEX = "0123456789abcdef"

fun ByteArray.sha256Hex(): String {
    val digest = MessageDigest.getInstance("SHA-256").digest(this)
    return digest.toHex()
}

fun InputStream.sha256Hex(): String {
    val md = MessageDigest.getInstance("SHA-256")
    val buffer = ByteArray(64 * 1024)
    while (true) {
        val read = read(buffer)
        if (read <= 0) break
        md.update(buffer, 0, read)
    }
    return md.digest().toHex()
}

/** Streaming hash for evidence originals that must never be fully buffered. */
fun File.sha256Hex(): String = inputStream().buffered().use { it.sha256Hex() }

fun ByteArray.toHex(): String {
    val out = StringBuilder(size * 2)
    for (b in this) {
        val v = b.toInt() and 0xff
        out.append(HEX[v ushr 4]).append(HEX[v and 0x0f])
    }
    return out.toString()
}

private val SHA256_RE = Regex("^[0-9a-f]{64}$")

fun isSha256Hex(value: String): Boolean = SHA256_RE.matches(value)

/**
 * Deterministic UUIDv5 (SHA-1, RFC 4122 §4.3) for stable nominal
 * identifiers: profile and board-instance ids the phone derives
 * from descriptor bytes it actually holds. Nominal ids are always
 * declared as such in sidecar reasons — they identify a nominal
 * description, never a measured physical instance.
 */
fun uuidV5(namespace: String, name: String): String {
    val hash = MessageDigest.getInstance("SHA-1")
        .digest("$namespace:$name".toByteArray(Charsets.UTF_8))
    hash[6] = ((hash[6].toInt() and 0x0f) or 0x50).toByte()
    hash[8] = ((hash[8].toInt() and 0x3f) or 0x80).toByte()
    val hex = hash.take(16).toByteArray().toHex()
    return "${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-" +
        "${hex.substring(16, 20)}-${hex.substring(20, 32)}"
}
