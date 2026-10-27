package org.anastylos.capture.pairing

import com.google.common.truth.Truth.assertThat
import java.io.IOException
import java.security.cert.CertificateExpiredException
import java.time.Instant
import javax.net.ssl.SSLHandshakeException
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.anastylos.capture.sync.HttpRequest
import org.anastylos.capture.sync.HttpResponse
import org.anastylos.capture.sync.HttpTransport
import org.anastylos.capture.sync.QrPairPayload
import org.anastylos.capture.sync.SpkiMismatchException
import org.anastylos.capture.sync.TransportException
import org.junit.Test

class HttpQrPairClientTest {
    private val now = Instant.parse("2026-10-27T17:00:00Z")

    private fun payload() = QrPairPayload(
        workstationId = "9f2b7a01c3d44e5f8a0b1c2d3e4f5061",
        baseUrl = "https://192.168.1.20:8443",
        host = "192.168.1.20",
        port = 8443,
        spkiSha256 = ByteArray(32) { it.toByte() },
        pairingCode = "anchor banana cherry desert eagle frost",
        expiresAt = now.plusSeconds(300),
    )

    private class ScriptedTransport(vararg steps: Any) : HttpTransport {
        val requests = mutableListOf<HttpRequest>()
        private val queue = ArrayDeque(steps.toList())

        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return when (val step = queue.removeFirst()) {
                is HttpResponse -> step
                is TransportException -> throw step
                else -> throw AssertionError("bad script step: $step")
            }
        }
    }

    private fun TestScope.client(
        transport: HttpTransport,
        clock: () -> Instant = { now },
        pollIntervalMillis: Long = 2_000,
        pollMaxConsecutiveFailures: Int = 5,
    ): HttpQrPairClient {
        val testDispatcher = StandardTestDispatcher(testScheduler)
        return HttpQrPairClient(
            transportFactory = { transport },
            ioDispatcher = testDispatcher,
            clock = clock,
            pollIntervalMillis = pollIntervalMillis,
            pollMaxConsecutiveFailures = pollMaxConsecutiveFailures,
        )
    }

    private fun jsonResponse(status: Int, body: String, headers: Map<String, String> = emptyMap()) =
        HttpResponse(status, body.toByteArray(Charsets.UTF_8), headers)

    private fun redeemReply(requestId: String = "a".repeat(32), expiresAt: Double = 1793122800.5) =
        "{\"status\":\"pending\",\"request_id\":\"$requestId\",\"expires_at\":$expiresAt}"

    @Test
    fun `redeem sends code and label and parses the pending request`() = runTest {
        val transport = ScriptedTransport(jsonResponse(202, redeemReply()))
        val outcome = client(transport).redeem(payload(), "Test Phone")

        val request = transport.requests.single()
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.path).isEqualTo("/v1/pair/qr")
        val body = Json.parseToJsonElement(request.body!!.toString(Charsets.UTF_8)).jsonObject
        assertThat(body["pairing_code"]!!.jsonPrimitive.content)
            .isEqualTo("anchor banana cherry desert eagle frost")
        assertThat(body["device_label"]!!.jsonPrimitive.content).isEqualTo("Test Phone")

        assertThat(outcome).isInstanceOf(RedeemOutcome.Pending::class.java)
        val pending = (outcome as RedeemOutcome.Pending).request
        assertThat(pending.requestId).isEqualTo("a".repeat(32))
        assertThat(pending.expiresAt).isEqualTo(Instant.ofEpochSecond(1793122800, 500_000_000))
    }

    @Test
    fun `redeem maps failures`() = runTest {
        val forbidden = ScriptedTransport(
            jsonResponse(403, "{\"error\":{\"code\":\"pairing_failed\",\"message\":\"Pairing failed.\"}}"),
        )
        assertThat(client(forbidden).redeem(payload(), "T"))
            .isInstanceOf(RedeemOutcome.CodeInvalid::class.java)

        val limited = ScriptedTransport(jsonResponse(429, "{}", mapOf("Retry-After" to "93")))
        val rate = client(limited).redeem(payload(), "T") as RedeemOutcome.RateLimited
        assertThat(rate.retryAfterSeconds).isEqualTo(93)

        val bad = ScriptedTransport(jsonResponse(400, "{}"))
        assertThat(client(bad).redeem(payload(), "T"))
            .isInstanceOf(RedeemOutcome.ServerError::class.java)

        val malformed = ScriptedTransport(jsonResponse(202, "{\"status\":\"pending\"}"))
        assertThat(client(malformed).redeem(payload(), "T"))
            .isInstanceOf(RedeemOutcome.ServerError::class.java)

        val evilId = ScriptedTransport(jsonResponse(202, redeemReply(requestId = "../../x")))
        assertThat(client(evilId).redeem(payload(), "T"))
            .isInstanceOf(RedeemOutcome.ServerError::class.java)

        val down = ScriptedTransport(TransportException("connect timed out", IOException("timeout")))
        assertThat(client(down).redeem(payload(), "T"))
            .isInstanceOf(RedeemOutcome.NetworkError::class.java)

        val pin = ScriptedTransport(
            TransportException("handshake failed", SSLHandshakeException("x").initCause(SpkiMismatchException())),
        )
        assertThat(client(pin).redeem(payload(), "T"))
            .isInstanceOf(RedeemOutcome.PinMismatch::class.java)

        val expired = ScriptedTransport(
            TransportException(
                "handshake failed",
                SSLHandshakeException("x").initCause(CertificateExpiredException("gone")),
            ),
        )
        assertThat(client(expired).redeem(payload(), "T"))
            .isInstanceOf(RedeemOutcome.ServerError::class.java)
    }

    @Test
    fun `poll walks pending to approved`() = runTest {
        val transport = ScriptedTransport(
            jsonResponse(200, "{\"status\":\"pending\"}"),
            jsonResponse(200, "{\"status\":\"pending\"}"),
            jsonResponse(200, "{\"status\":\"approved\",\"workstation_id\":\"ws-1\",\"token\":\"tok-1\"}"),
        )
        val request = PendingRequest("b".repeat(32), now.plusSeconds(300))
        val outcome = client(transport).awaitApproval(payload(), request)

        assertThat(outcome).isEqualTo(ApprovalOutcome.Approved("tok-1", "ws-1"))
        assertThat(transport.requests).hasSize(3)
        assertThat(transport.requests[0].path).isEqualTo("/v1/pair/qr/${"b".repeat(32)}")
    }

    @Test
    fun `poll maps terminal replies`() = runTest {
        val gone = ScriptedTransport(jsonResponse(404, "{}"))
        assertThat(client(gone).awaitApproval(payload(), PendingRequest("c".repeat(32), now.plusSeconds(60))))
            .isInstanceOf(ApprovalOutcome.NotApproved::class.java)

        val tokenless = ScriptedTransport(jsonResponse(200, "{\"status\":\"approved\"}"))
        assertThat(client(tokenless).awaitApproval(payload(), PendingRequest("c".repeat(32), now.plusSeconds(60))))
            .isInstanceOf(ApprovalOutcome.ServerError::class.java)

        val weird = ScriptedTransport(jsonResponse(200, "{\"status\":\"splendid\"}"))
        assertThat(client(weird).awaitApproval(payload(), PendingRequest("c".repeat(32), now.plusSeconds(60))))
            .isInstanceOf(ApprovalOutcome.ServerError::class.java)

        val broken = ScriptedTransport(jsonResponse(500, "{}"))
        assertThat(client(broken).awaitApproval(payload(), PendingRequest("c".repeat(32), now.plusSeconds(60))))
            .isInstanceOf(ApprovalOutcome.ServerError::class.java)
    }

    @Test
    fun `poll gives up after repeated transport failures`() = runTest {
        val failing = TransportException("unreachable", IOException("down"))
        val transport = ScriptedTransport(failing, failing, failing, failing, failing)
        val outcome = client(transport).awaitApproval(
            payload(),
            PendingRequest("d".repeat(32), now.plusSeconds(300)),
        )
        assertThat(outcome).isInstanceOf(ApprovalOutcome.NetworkError::class.java)
        assertThat(transport.requests).hasSize(5)
    }

    @Test
    fun `one good poll resets the failure count`() = runTest {
        val failing = TransportException("unreachable", IOException("down"))
        val transport = ScriptedTransport(
            failing, failing, failing, failing,
            jsonResponse(200, "{\"status\":\"pending\"}"),
            jsonResponse(200, "{\"status\":\"approved\",\"workstation_id\":\"ws\",\"token\":\"tok\"}"),
        )
        val outcome = client(transport).awaitApproval(
            payload(),
            PendingRequest("e".repeat(32), now.plusSeconds(300)),
        )
        assertThat(outcome).isEqualTo(ApprovalOutcome.Approved("tok", "ws"))
        assertThat(transport.requests).hasSize(6)
    }

    @Test
    fun `poll honors retry-after without counting a failure`() = runTest {
        val transport = ScriptedTransport(
            jsonResponse(429, "{}", mapOf("Retry-After" to "1")),
            jsonResponse(200, "{\"status\":\"approved\",\"workstation_id\":\"ws\",\"token\":\"tok\"}"),
        )
        val outcome = client(transport).awaitApproval(
            payload(),
            PendingRequest("f".repeat(32), now.plusSeconds(300)),
        )
        assertThat(outcome).isEqualTo(ApprovalOutcome.Approved("tok", "ws"))
        assertThat(transport.requests).hasSize(2)
    }

    @Test
    fun `poll stops at the request deadline`() = runTest {
        val transport = ScriptedTransport(
            jsonResponse(200, "{\"status\":\"pending\"}"),
            jsonResponse(200, "{\"status\":\"pending\"}"),
            jsonResponse(200, "{\"status\":\"approved\",\"workstation_id\":\"ws\",\"token\":\"tok\"}"),
        )
        val request = PendingRequest("0".repeat(32), now.plusSeconds(3))
        // The operator never approves: after the first poll the clock
        // jumps past the deadline, so the late approval is never seen.
        val lapsing = client(transport, clock = {
            if (transport.requests.isEmpty()) now else now.plusSeconds(99)
        })
        val outcome = lapsing.awaitApproval(payload(), request)
        assertThat(outcome).isInstanceOf(ApprovalOutcome.NotApproved::class.java)
        assertThat(transport.requests).hasSize(1)
    }

    @Test
    fun `verify sends the bearer token and parses the echo`() = runTest {
        val transport = ScriptedTransport(
            jsonResponse(200, "{\"workstation_id\":\"ws-7\",\"staged_packages\":0}"),
        )
        val outcome = client(transport).verify(payload(), "tok-abc")
        assertThat(outcome).isEqualTo(VerifyOutcome.Verified("ws-7"))
        val request = transport.requests.single()
        assertThat(request.method).isEqualTo("GET")
        assertThat(request.path).isEqualTo("/v1/status")
        assertThat(request.headers["Authorization"]).isEqualTo("Bearer tok-abc")
    }

    @Test
    fun `verify maps failures`() = runTest {
        val refused = ScriptedTransport(jsonResponse(401, "{}"))
        assertThat(client(refused).verify(payload(), "tok"))
            .isEqualTo(VerifyOutcome.Unauthorized)

        val tokenless = ScriptedTransport(jsonResponse(200, "{\"staged_packages\":0}"))
        assertThat(client(tokenless).verify(payload(), "tok"))
            .isInstanceOf(VerifyOutcome.ServerError::class.java)

        val broken = ScriptedTransport(jsonResponse(500, "{}"))
        assertThat(client(broken).verify(payload(), "tok"))
            .isInstanceOf(VerifyOutcome.ServerError::class.java)

        val down = ScriptedTransport(TransportException("unreachable", IOException("down")))
        assertThat(client(down).verify(payload(), "tok"))
            .isInstanceOf(VerifyOutcome.NetworkError::class.java)

        val pin = ScriptedTransport(
            TransportException("handshake failed", SpkiMismatchException()),
        )
        assertThat(client(pin).verify(payload(), "tok"))
            .isInstanceOf(VerifyOutcome.PinMismatch::class.java)
    }

    @Test
    fun `verify refuses blank tokens without touching the wire`() = runTest {
        val transport = ScriptedTransport(jsonResponse(200, "{\"workstation_id\":\"ws\"}"))
        try {
            client(transport).verify(payload(), "  ")
            throw AssertionError("expected token refusal")
        } catch (e: IllegalArgumentException) {
            assertThat(e.message).contains("token")
        }
        assertThat(transport.requests).isEmpty()
    }

    @Test
    fun `pin mismatch aborts the poll immediately`() = runTest {
        val transport = ScriptedTransport(
            TransportException("handshake failed", SpkiMismatchException()),
        )
        val outcome = client(transport).awaitApproval(
            payload(),
            PendingRequest("1".repeat(32), now.plusSeconds(300)),
        )
        assertThat(outcome).isInstanceOf(ApprovalOutcome.PinMismatch::class.java)
        assertThat(transport.requests).hasSize(1)
    }

    @Test
    fun `malformed request ids never reach the wire`() = runTest {
        val transport = ScriptedTransport(jsonResponse(200, "{\"status\":\"pending\"}"))
        try {
            client(transport).awaitApproval(payload(), PendingRequest("../x", now.plusSeconds(60)))
            throw AssertionError("expected id refusal")
        } catch (e: IllegalArgumentException) {
            assertThat(e.message).contains("request id")
        }
        assertThat(transport.requests).isEmpty()
    }

    @Test
    fun `cancellation aborts the poll`() = runTest {
        val transport = ScriptedTransport(jsonResponse(200, "{\"status\":\"pending\"}"))
        val client = client(transport)
        val request = PendingRequest("2".repeat(32), now.plusSeconds(300))
        var outcome: ApprovalOutcome? = null
        val job = launch { outcome = client.awaitApproval(payload(), request) }
        runCurrent()
        assertThat(transport.requests).hasSize(1)
        job.cancel()
        advanceUntilIdle()
        job.join() // Returns normally for a cancelled job; assert the state instead.
        assertThat(job.isCancelled).isTrue()
        assertThat(outcome).isNull()
        // No second poll was ever attempted.
        assertThat(transport.requests).hasSize(1)
    }

    @Test
    fun `poll delay uses virtual time`() = runTest {
        val transport = ScriptedTransport(
            jsonResponse(200, "{\"status\":\"pending\"}"),
            jsonResponse(200, "{\"status\":\"approved\",\"workstation_id\":\"ws\",\"token\":\"tok\"}"),
        )
        val client = client(transport, pollIntervalMillis = 2_000)
        var outcome: ApprovalOutcome? = null
        val job = launch {
            outcome = client.awaitApproval(payload(), PendingRequest("3".repeat(32), now.plusSeconds(300)))
        }
        runCurrent()
        // One poll done, second waits on the interval.
        assertThat(transport.requests).hasSize(1)
        assertThat(outcome).isNull()
        advanceTimeBy(1_999)
        runCurrent()
        assertThat(transport.requests).hasSize(1)
        advanceTimeBy(1)
        runCurrent()
        assertThat(transport.requests).hasSize(2)
        advanceUntilIdle()
        assertThat(outcome).isEqualTo(ApprovalOutcome.Approved("tok", "ws"))
        job.join()
    }
}
