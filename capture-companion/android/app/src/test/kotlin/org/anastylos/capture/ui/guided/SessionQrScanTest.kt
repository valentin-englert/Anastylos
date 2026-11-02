package org.anastylos.capture.ui.guided

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class SessionQrScanTest {
    private val id = "3f2b8c1e-9a4d-4e57-8b21-0c6d5e7f9a10"

    @Test
    fun findsABareSessionId() {
        assertThat(sessionIdFromQr(id)).isEqualTo(id)
    }

    @Test
    fun findsTheSessionIdInsideALinkOrJson() {
        assertThat(sessionIdFromQr("https://workstation.local/session/$id?x=1")).isEqualTo(id)
        assertThat(sessionIdFromQr("""{"session_id":"${id.uppercase()}"}""")).isEqualTo(id)
    }

    @Test
    fun rejectsCodesWithoutASessionId() {
        assertThat(sessionIdFromQr("https://example.com")).isNull()
        assertThat(sessionIdFromQr(null)).isNull()
    }
}
