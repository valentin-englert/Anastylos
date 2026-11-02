package org.anastylos.capture.pairing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class ScanKindTest {
    private val id = "3f2b8c1e-9a4d-4e57-8b21-0c6d5e7f9a10"
    private val now = java.time.Instant.parse("2026-10-27T17:00:00Z")
    private val pairing =
        "{\"p\":\"anastylos-pair\",\"v\":1,\"w\":\"9f2b7a01c3d44e5f8a0b1c2d3e4f5061\"," +
            "\"u\":\"https://192.168.1.20:8443\"," +
            "\"s\":\"5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206\"," +
            "\"c\":\"anchor banana cherry desert eagle frost\",\"e\":1793122800}"

    @Test
    fun aSessionIdIsFoundBareInALinkOrInJson() {
        assertThat(sessionIdFromQr(id)).isEqualTo(id)
        assertThat(sessionIdFromQr("https://workstation.local/session/$id?x=1")).isEqualTo(id)
        assertThat(sessionIdFromQr("""{"session_id":"${id.uppercase()}"}""")).isEqualTo(id)
        assertThat(sessionIdFromQr("https://example.com")).isNull()
        assertThat(sessionIdFromQr(null)).isNull()
    }

    @Test
    fun oneScannerTellsPairingSessionAndUnknownCodesApart() {
        assertThat(classifyScan(pairing, now)).isEqualTo(ScanKind.Pairing)
        assertThat(classifyScan(id)).isEqualTo(ScanKind.Session(id))
        assertThat(classifyScan("hello world")).isEqualTo(ScanKind.Unknown)
        assertThat(classifyScan("")).isEqualTo(ScanKind.Unknown)
        assertThat(classifyScan(null)).isEqualTo(ScanKind.Unknown)
    }
}
