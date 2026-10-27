package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import org.junit.Test
import org.junit.function.ThrowingRunnable
import org.junit.Assert.assertThrows

class QrPayloadTest {
    private val now = Instant.parse("2026-10-27T17:00:00Z")

    // Byte-identical to the payload QrPayloadTests builds on the Python side
    // (same field values, same compact separators): the cross-language golden.
    private val golden =
        "{\"p\":\"anastylos-pair\",\"v\":1,\"w\":\"9f2b7a01c3d44e5f8a0b1c2d3e4f5061\"," +
            "\"u\":\"https://192.168.1.20:8443\"," +
            "\"s\":\"5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206\"," +
            "\"c\":\"anchor banana cherry desert eagle frost\",\"e\":1793122800}"

    private fun parse(text: String = golden, at: Instant = now) = parseQrPayload(text, at)

    private fun expectReject(text: String, message: String) {
        val thrown = assertThrows(
            message, QrPayloadException::class.java, ThrowingRunnable { parse(text) }
        )
        assertThat(thrown).isNotNull()
    }

    @Test
    fun `golden payload parses fully`() {
        val payload = parse()
        assertThat(payload.workstationId).isEqualTo("9f2b7a01c3d44e5f8a0b1c2d3e4f5061")
        assertThat(payload.baseUrl).isEqualTo("https://192.168.1.20:8443")
        assertThat(payload.host).isEqualTo("192.168.1.20")
        assertThat(payload.port).isEqualTo(8443)
        assertThat(payload.spkiSha256.size).isEqualTo(32)
        assertThat(payload.spkiSha256[0]).isEqualTo(0x5b.toByte())
        assertThat(payload.spkiSha256[31]).isEqualTo(0x06.toByte())
        assertThat(payload.pairingCode).isEqualTo("anchor banana cherry desert eagle frost")
        assertThat(payload.expiresAt).isEqualTo(Instant.ofEpochSecond(1793122800))
    }

    @Test
    fun `expiry boundary is exact`() {
        parse(golden, at = Instant.ofEpochSecond(1793122799))
        val thrown = assertThrows(
            "expired at the exact second",
            QrPayloadException::class.java,
            ThrowingRunnable { parse(golden, at = Instant.ofEpochSecond(1793122800)) },
        )
        assertThat(thrown).isNotNull()
    }

    @Test
    fun `far future expiry is implausible`() {
        val far = golden.replace("1793122800", (now.epochSecond + 25 * 3600).toString())
        expectReject(far, "expiry beyond 24h")
        val edge = golden.replace("1793122800", (now.epochSecond + 3600).toString())
        assertThat(parse(edge).expiresAt).isEqualTo(Instant.ofEpochSecond(now.epochSecond + 3600))
    }

    @Test
    fun `default https port applies`() {
        val text = golden.replace("https://192.168.1.20:8443", "https://workstation.lan")
        val payload = parse(text)
        assertThat(payload.host).isEqualTo("workstation.lan")
        assertThat(payload.port).isEqualTo(443)
    }

    @Test
    fun `code whitespace is normalized`() {
        val text = golden.replace("anchor banana cherry desert eagle frost", "  anchor   banana\tcherry ")
        // Full golden code replaced by a shorter one; still valid shape.
        val payload = parse(text)
        assertThat(payload.pairingCode).isEqualTo("anchor banana cherry")
    }

    @Test
    fun `structural rejects`() {
        expectReject("not json", "not json")
        expectReject("[1,2]", "not an object")
        expectReject("x".repeat(2049), "over size bound")
        expectReject(golden.replace("\"v\":1", "\"v\":2"), "wrong version")
        expectReject(golden.replace("\"v\":1", "\"v\":1.0"), "float version")
        expectReject(golden.replace("\"p\":\"anastylos-pair\"", "\"p\":\"other\""), "wrong protocol")
        expectReject(golden.dropLast(1) + ",\"x\":1}", "extra key")
        expectReject(golden.replace(",\"e\":1793122800", ""), "missing key")
    }

    @Test
    fun `endpoint rejects`() {
        expectReject(golden.replace("https://", "http://"), "plaintext")
        expectReject(golden.replace("https://192.168.1.20:8443", "https://"), "no host")
        expectReject(golden.replace("https://192.168.1.20:8443", "notaurl"), "not a url")
        expectReject(
            golden.replace("https://192.168.1.20:8443", "https://user@192.168.1.20:8443"),
            "userinfo",
        )
        expectReject(
            golden.replace("https://192.168.1.20:8443", "https://192.168.1.20:8443/pair"),
            "path",
        )
        expectReject(
            golden.replace("https://192.168.1.20:8443", "https://192.168.1.20:8443?x=1"),
            "query",
        )
        expectReject(
            golden.replace("https://192.168.1.20:8443", "https://192.168.1.20:99999"),
            "bad port",
        )
    }

    @Test
    fun `lone trailing slash is accepted`() {
        val payload = parse(golden.replace("https://192.168.1.20:8443", "https://192.168.1.20:8443/"))
        assertThat(payload.port).isEqualTo(8443)
    }

    @Test
    fun `field rejects`() {
        expectReject(golden.replace("9f2b7a01c3d44e5f8a0b1c2d3e4f5061", ""), "empty workstation")
        expectReject(golden.replace("5b990e9c", "5b990e9"), "short pin")
        expectReject(
            golden.replace(
                "5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206",
                "5B990E9C6186BF1163FF0C7D2CEF2F156E92C57A3B4F3CDD43B98800977B9206",
            ),
            "uppercase pin",
        )
        expectReject(
            golden.replace("anchor banana cherry desert eagle frost", "   "),
            "blank code",
        )
        expectReject(golden.replace("\"e\":1793122800", "\"e\":\"soon\""), "string expiry")
        expectReject(golden.replace("\"e\":1793122800", "\"e\":1793122800.5"), "float expiry")
        expectReject(golden.replace("\"e\":1793122800", "\"e\":0"), "zero expiry")
        expectReject(
            golden.replace("\"e\":1793122800", "\"e\":${now.epochSecond - 1}"),
            "past expiry",
        )
    }
}
