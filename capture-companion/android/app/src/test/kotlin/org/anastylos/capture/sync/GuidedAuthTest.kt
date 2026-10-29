package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import kotlinx.coroutines.test.runTest
import org.junit.Test

class GuidedAuthTest {
    private val now = Instant.parse("2026-10-29T12:00:00Z")

    private class ScriptedTransport(
        var handler: (HttpRequest) -> HttpResponse = { HttpResponse(500, ByteArray(0)) },
    ) : HttpTransport {
        var calls = 0
        override fun exchange(request: HttpRequest): HttpResponse {
            calls++
            return handler(request)
        }
    }

    private fun pairing() = StoredPairing(
        baseUrl = "https://ws:8443",
        workstationId = "ws-1",
        token = "legacy-secret",
        pinnedSpkiSha256Hex = "ab".repeat(32),
    )

    private fun enrollReply() = HttpResponse(
        201,
        """{"source_id":"00000000-0000-4000-8000-000000000003","token":"guided-secret","expires_at":"2026-11-28T12:01:00Z"}"""
            .toByteArray(Charsets.UTF_8),
    )

    @Test
    fun `live cached token needs no wire`() = runTest {
        val store = EphemeralPairingStore()
        store.save(
            pairing().copy(
                guidedSourceId = "00000000-0000-4000-8000-000000000003",
                guidedToken = "cached-guided",
                guidedTokenExpiresAt = "2026-11-28T12:01:00Z",
            ),
        )
        val transport = ScriptedTransport()
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        val outcome = auth.guidedToken() as GuidedTokenOutcome.Ready
        assertThat(outcome.token).isEqualTo("cached-guided")
        assertThat(transport.calls).isEqualTo(0)
    }

    @Test
    fun `missing token enrolls and persists`() = runTest {
        val store = EphemeralPairingStore()
        store.save(pairing())
        val transport = ScriptedTransport { enrollReply() }
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        val outcome = auth.guidedToken() as GuidedTokenOutcome.Ready
        assertThat(outcome.token).isEqualTo("guided-secret")
        assertThat(transport.calls).isEqualTo(1)
        val saved = store.load()!!
        assertThat(saved.guidedToken).isEqualTo("guided-secret")
        assertThat(saved.guidedSourceId).isEqualTo("00000000-0000-4000-8000-000000000003")
        assertThat(saved.token).isEqualTo("legacy-secret")
    }

    @Test
    fun `token inside the margin re-enrolls`() = runTest {
        val store = EphemeralPairingStore()
        store.save(
            pairing().copy(
                guidedSourceId = "00000000-0000-4000-8000-000000000003",
                guidedToken = "stale-guided",
                guidedTokenExpiresAt = "2026-10-29T12:00:30Z",
            ),
        )
        val transport = ScriptedTransport { enrollReply() }
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        val outcome = auth.guidedToken() as GuidedTokenOutcome.Ready
        assertThat(outcome.token).isEqualTo("guided-secret")
        assertThat(transport.calls).isEqualTo(1)
    }

    @Test
    fun `unparseable expiry re-enrolls rather than trusting`() = runTest {
        val store = EphemeralPairingStore()
        store.save(
            pairing().copy(
                guidedSourceId = "00000000-0000-4000-8000-000000000003",
                guidedToken = "maybe-stale",
                guidedTokenExpiresAt = "not-a-time",
            ),
        )
        val transport = ScriptedTransport { enrollReply() }
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        val outcome = auth.guidedToken() as GuidedTokenOutcome.Ready
        assertThat(outcome.token).isEqualTo("guided-secret")
        assertThat(transport.calls).isEqualTo(1)
    }

    @Test
    fun `not-enrolled surfaces without touching the cache`() = runTest {
        val store = EphemeralPairingStore()
        store.save(pairing())
        val transport = ScriptedTransport {
            HttpResponse(
                403,
                """{"error":{"code":"not_enrolled","message":"ask the operator"}}"""
                    .toByteArray(Charsets.UTF_8),
            )
        }
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        val outcome = auth.guidedToken()
        assertThat(outcome).isInstanceOf(GuidedTokenOutcome.NeedsEnrollment::class.java)
        assertThat(store.load()!!.guidedToken).isEmpty()
    }

    @Test
    fun `spent exchange surfaces distinctly`() = runTest {
        val store = EphemeralPairingStore()
        store.save(pairing())
        val transport = ScriptedTransport {
            HttpResponse(
                403,
                """{"error":{"code":"already_exchanged","message":"spent"}}"""
                    .toByteArray(Charsets.UTF_8),
            )
        }
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        assertThat(auth.guidedToken()).isInstanceOf(GuidedTokenOutcome.ExchangeSpent::class.java)
    }

    @Test
    fun `refresh ignores the cache`() = runTest {
        val store = EphemeralPairingStore()
        store.save(
            pairing().copy(
                guidedSourceId = "00000000-0000-4000-8000-000000000003",
                guidedToken = "cached-guided",
                guidedTokenExpiresAt = "2026-11-28T12:01:00Z",
            ),
        )
        val transport = ScriptedTransport { enrollReply() }
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        val outcome = auth.refresh() as GuidedTokenOutcome.Ready
        assertThat(outcome.token).isEqualTo("guided-secret")
        assertThat(transport.calls).isEqualTo(1)
    }

    @Test
    fun `unpaired device fails without wire`() = runTest {
        val store = EphemeralPairingStore()
        val transport = ScriptedTransport()
        val auth = GuidedAuth(store, GuidedControlClient(transport)) { now }

        val outcome = auth.guidedToken()
        assertThat(outcome).isInstanceOf(GuidedTokenOutcome.Failed::class.java)
        assertThat(transport.calls).isEqualTo(0)
    }
}
