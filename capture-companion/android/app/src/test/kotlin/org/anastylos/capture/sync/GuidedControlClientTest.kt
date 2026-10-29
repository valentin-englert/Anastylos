package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.io.File
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Test

/**
 * Contract tests for [GuidedControlClient] against the nine frozen
 * Core fixtures (`capture-companion/receiver/tests/fixtures/`).
 * The fixtures are consumed read-only from the module directory —
 * never copied — so drift fails loudly here.
 */
class GuidedControlClientTest {
    private val json = Json { ignoreUnknownKeys = true }

    private class ScriptedTransport(
        var handler: (HttpRequest) -> HttpResponse = {
            HttpResponse(500, ByteArray(0))
        },
    ) : HttpTransport {
        val requests = mutableListOf<HttpRequest>()
        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return handler(request)
        }
    }

    private fun fixture(name: String): String {
        // Unit-test working directory varies (module dir vs project
        // dir); walk up until the receiver fixtures resolve.
        var dir: File? = File(System.getProperty("user.dir"))
        var found: File? = null
        repeat(6) {
            val base = dir ?: return@repeat
            for (relative in arrayOf(
                "receiver/tests/fixtures",
                "capture-companion/receiver/tests/fixtures",
                "../receiver/tests/fixtures",
            )) {
                val candidate = File(base, "$relative/$name")
                if (candidate.isFile) {
                    found = candidate
                    return@repeat
                }
            }
            dir = base.parentFile
        }
        assertThat(found).isNotNull()
        return found!!.readText(Charsets.UTF_8)
    }

    private fun fixturePart(name: String, part: String): String =
        json.parseToJsonElement(fixture(name)).jsonObject.getValue(part).toString()

    private fun okJson(body: String) = HttpResponse(
        200, body.toByteArray(Charsets.UTF_8),
        mapOf("Content-Type" to "application/json"),
    )

    @Test
    fun `consent arm fixture round-trips to an ACTIVE session`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val requestJson = fixturePart("guided-wire-consent.json", "request")
        val responseJson = fixturePart("guided-wire-consent.json", "response")
        transport.handler = { okJson(responseJson) }

        val input = json.decodeFromString(GuidedConsentInput.serializer(), requestJson)
        val outcome = client.consent("guided-token", input.sessionId, input)

        val session = (outcome as GuidedOutcome.Success).value
        assertThat(session.state).isEqualTo("ACTIVE")
        assertThat(session.grantId).isNotNull()
        assertThat(session.revision).isEqualTo(3)
        assertThat(session.consentRevision).isEqualTo(1)
        val sent = transport.requests.single()
        assertThat(sent.method).isEqualTo("POST")
        assertThat(sent.path).isEqualTo("/v1/guided/sessions/${input.sessionId}/consent")
        assertThat(sent.headers["Authorization"]).isEqualTo("Bearer guided-token")
        val echoed = json.decodeFromString(
            GuidedConsentInput.serializer(), sent.body!!.toString(Charsets.UTF_8),
        )
        assertThat(echoed.action).isEqualTo("arm")
        assertThat(echoed.cameraId).isEqualTo("camera:0")
    }

    @Test
    fun `request view fixture parses with target tolerances`() {
        val requestJson = fixturePart("guided-wire-request-view.json", "response")
        val request = json.decodeFromString(
            GuidedViewRequest.serializer(),
            json.parseToJsonElement(requestJson).jsonObject.getValue("request").toString(),
        )
        assertThat(request.status).isEqualTo("QUEUED")
        assertThat(request.target.kind).isEqualTo("pose")
        assertThat(request.target.tbc.matrix).hasSize(16)
        assertThat(request.target.tbc.units).isEqualTo("mm")
        assertThat(request.target.translationToleranceMm).containsExactly(15.0, 15.0, 20.0)
        assertThat(request.target.rotationToleranceDeg).isEqualTo(5.0)
    }

    @Test
    fun `source event fixture is acknowledged`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val eventJson = fixturePart("guided-wire-source-event.json", "event")
        val ackJson = fixturePart("guided-wire-source-event.json", "ack")
        transport.handler = { okJson(ackJson) }

        val event = json.decodeFromString(GuidedSourceEvent.serializer(), eventJson)
        val outcome = client.postEvent("guided-token", event.sessionId, event)

        val ack = (outcome as GuidedOutcome.Success).value
        assertThat(ack.acceptedEventId).isEqualTo(event.eventId)
        assertThat(ack.eventSeq).isEqualTo(1)
        assertThat(ack.sessionRevision).isEqualTo(3)
        assertThat(transport.requests.single().path)
            .isEqualTo("/v1/guided/sessions/${event.sessionId}/events")
    }

    @Test
    fun `changes fixture yields an ordered page with cursor`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val responseJson = fixturePart("guided-wire-changes.json", "response")
        transport.handler = { okJson(responseJson) }

        val outcome = client.pollChanges(
            "guided-token", "00000000-0000-4000-8000-000000000011", "0",
        )

        val page = (outcome as GuidedOutcome.Success).value
        assertThat(page.events).hasSize(3)
        assertThat(page.nextCursor).isEqualTo("3")
        assertThat(page.snapshotRequired).isFalse()
        assertThat(page.events.map { it.kind }).containsExactly(
            "session_changed", "request_created", "source_event",
        ).inOrder()
        val created = page.events[1]
        assertThat(created.request?.status).isEqualTo("QUEUED")
        assertThat(transport.requests.single().path).endsWith("/changes?after=0")
    }

    @Test
    fun `finalize fixture parses as a capture set`() {
        val responseJson = fixturePart("guided-wire-finalize.json", "response")
        val set = json.decodeFromString(GuidedCaptureSet.serializer(), responseJson)
        assertThat(set.status).isEqualTo("incomplete")
        assertThat(set.manifestSha256).hasLength(64)
        assertThat(set.requiredRequests).hasSize(1)
        assertThat(set.omissions).hasSize(1)
    }

    @Test
    fun `attempt fixture reserves with remaining exposures`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val requestJson = fixturePart("guided-wire-attempt.json", "request")
        val responseJson = fixturePart("guided-wire-attempt.json", "response")
        transport.handler = { okJson(responseJson) }

        val input = json.decodeFromString(GuidedAttemptInput.serializer(), requestJson)
        val outcome = client.registerAttempt("guided-token", input.sessionId, input)

        val ack = (outcome as GuidedOutcome.Success).value
        assertThat(ack.status).isEqualTo("reserved")
        assertThat(ack.attemptNumber).isEqualTo(1)
        assertThat(ack.remainingExposures).isEqualTo(19)
        assertThat(transport.requests.single().path)
            .isEqualTo("/v1/guided/sessions/${input.sessionId}/attempts")
    }

    @Test
    fun `binding fixture acknowledges the asset`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val requestJson = fixturePart("guided-wire-binding.json", "request")
        val responseJson = fixturePart("guided-wire-binding.json", "response")
        transport.handler = { okJson(responseJson) }

        val binding = json.decodeFromString(GuidedAssetBinding.serializer(), requestJson)
        val outcome = client.bindAsset("guided-token", binding.attemptId, binding)

        val ack = (outcome as GuidedOutcome.Success).value
        assertThat(ack.status).isEqualTo("asset_bound")
        assertThat(transport.requests.single().path)
            .isEqualTo("/v1/guided/attempts/${binding.attemptId}/asset-binding")
        val echoed = json.decodeFromString(
            GuidedAssetBinding.serializer(),
            transport.requests.single().body!!.toString(Charsets.UTF_8),
        )
        assertThat(echoed.imageSha256).isEqualTo(binding.imageSha256)
        assertThat(echoed.packageSha256).isEqualTo(binding.packageSha256)
    }

    @Test
    fun `sidecar fixture lands SPATIAL_PENDING without a receipt`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val requestJson = fixturePart("guided-wire-sidecar-pending.json", "request")
        val responseJson = fixturePart("guided-wire-sidecar-pending.json", "response")
        transport.handler = { okJson(responseJson) }

        val request = json.parseToJsonElement(requestJson).jsonObject
        val sidecar = request.getValue("sidecar").toString()
        val outcome = client.uploadSidecar(
            token = "guided-token",
            attemptId = "00000000-0000-4000-8000-000000000047",
            operationId = request.getValue("operation_id").toString().trim('"'),
            expectedSidecarRevision = 0,
            sidecarJson = sidecar,
        )

        val delivery = (outcome as GuidedOutcome.Success).value
        assertThat(delivery.state).isEqualTo("SPATIAL_PENDING")
        assertThat(delivery.receiptStatus).isNull()
        val sent = transport.requests.single()
        assertThat(sent.path).isEqualTo(
            "/v1/guided/attempts/00000000-0000-4000-8000-000000000047/spatial",
        )
        assertThat(sent.body!!.toString(Charsets.UTF_8)).contains("\"expected_sidecar_revision\":0")
    }

    @Test
    fun `open session fixture parses as DISARMED`() {
        val responseJson = fixturePart("guided-wire-open-session.json", "response")
        val session = json.decodeFromString(GuidedSession.serializer(), responseJson)
        assertThat(session.state).isEqualTo("DISARMED")
        assertThat(session.revision).isEqualTo(1)
        assertThat(session.capabilities.stale).isTrue()
    }

    @Test
    fun `event ack for another id fails closed`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val eventJson = fixturePart("guided-wire-source-event.json", "event")
        transport.handler = {
            okJson("""{"accepted_event_id":"ffffffff-ffff-4fff-8fff-ffffffffffff","event_seq":9,"session_revision":9}""")
        }

        val event = json.decodeFromString(GuidedSourceEvent.serializer(), eventJson)
        val outcome = client.postEvent("guided-token", event.sessionId, event)

        assertThat(outcome).isInstanceOf(GuidedOutcome.Failed::class.java)
    }

    @Test
    fun `unordered changes page fails closed`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        transport.handler = {
            okJson(
                """{"events":[{"change_seq":2,"kind":"session_changed","session_revision":2,"request":null,"source_event":null,"receipt":null},{"change_seq":1,"kind":"session_changed","session_revision":1,"request":null,"source_event":null,"receipt":null}],"next_cursor":"2","snapshot_required":false,"server_time":"2026-10-29T12:01:00Z","lease_expires_at":null,"lease_remaining_ms":0}""",
            )
        }

        val outcome = client.pollChanges(
            "guided-token", "00000000-0000-4000-8000-000000000011", "0",
        )

        assertThat(outcome).isInstanceOf(GuidedOutcome.Failed::class.java)
    }

    @Test
    fun `revision conflict carries the current revision`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        transport.handler = {
            HttpResponse(
                409,
                """{"error":{"code":"revision_conflict","message":"stale session revision","current_revision":7}}"""
                    .toByteArray(Charsets.UTF_8),
            )
        }

        val outcome = client.pollChanges(
            "guided-token", "00000000-0000-4000-8000-000000000011", "0",
        )

        val conflict = outcome as GuidedOutcome.Conflict
        assertThat(conflict.code).isEqualTo("revision_conflict")
        assertThat(conflict.currentRevision).isEqualTo(7)
    }

    @Test
    fun `expired rate-limited unauthorized and not-found map distinctly`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val sessionId = "00000000-0000-4000-8000-000000000011"

        transport.handler = {
            HttpResponse(410, """{"error":{"code":"expired","message":"grant expired"}}""".toByteArray(Charsets.UTF_8))
        }
        assertThat(client.getSession("t", sessionId)).isInstanceOf(GuidedOutcome.Expired::class.java)

        transport.handler = {
            HttpResponse(429, ByteArray(0), mapOf("Retry-After" to "3"))
        }
        val limited = client.getSession("t", sessionId) as GuidedOutcome.RateLimited
        assertThat(limited.retryAfterSeconds).isEqualTo(3)

        transport.handler = { HttpResponse(401, ByteArray(0)) }
        assertThat(client.getSession("t", sessionId)).isInstanceOf(GuidedOutcome.Unauthorized::class.java)

        transport.handler = { HttpResponse(404, ByteArray(0)) }
        assertThat(client.getSession("t", sessionId)).isInstanceOf(GuidedOutcome.NotFound::class.java)

        transport.handler = { HttpResponse(418, ByteArray(0)) }
        assertThat(client.getSession("t", sessionId)).isInstanceOf(GuidedOutcome.Failed::class.java)
    }

    @Test
    fun `malformed JSON fails closed`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        transport.handler = { okJson("not json{{{") }

        val outcome = client.getSession("t", "00000000-0000-4000-8000-000000000011")
        assertThat(outcome).isInstanceOf(GuidedOutcome.Failed::class.java)
    }

    @Test
    fun `url and body session mismatch never reaches the wire`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val eventJson = fixturePart("guided-wire-source-event.json", "event")
        val event = json.decodeFromString(GuidedSourceEvent.serializer(), eventJson)

        val outcome = client.postEvent(
            "guided-token", "ffffffff-ffff-4fff-8fff-ffffffffffff", event,
        )

        assertThat(outcome).isInstanceOf(GuidedOutcome.Failed::class.java)
        assertThat(transport.requests).isEmpty()
    }

    @Test
    fun `non-uuid identities never reach the wire`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)

        assertThat(client.getSession("t", "not-a-uuid"))
            .isInstanceOf(GuidedOutcome.Failed::class.java)
        assertThat(transport.requests).isEmpty()
    }

    @Test
    fun `blank token is a programmer error`() = runTest {
        val client = GuidedControlClient(ScriptedTransport())
        var thrown = false
        try {
            client.getSession("", "00000000-0000-4000-8000-000000000011")
        } catch (_: IllegalArgumentException) {
            thrown = true
        }
        assertThat(thrown).isTrue()
    }

    @Test
    fun `bogus consent action never reaches the wire`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        val requestJson = fixturePart("guided-wire-consent.json", "request")
        val input = json.decodeFromString(GuidedConsentInput.serializer(), requestJson)
            .copy(action = "launch")

        val outcome = client.consent("t", input.sessionId, input)

        assertThat(outcome).isInstanceOf(GuidedOutcome.Failed::class.java)
        assertThat(transport.requests).isEmpty()
    }

    @Test
    fun `enroll exchanges a legacy token for a source token`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        transport.handler = {
            HttpResponse(
                201,
                """{"source_id":"00000000-0000-4000-8000-000000000003","token":"guided-secret","expires_at":"2026-11-28T12:01:00Z"}"""
                    .toByteArray(Charsets.UTF_8),
            )
        }

        val outcome = client.enroll("legacy-secret")

        val enrolled = outcome as EnrollOutcome.Enrolled
        assertThat(enrolled.sourceId).isEqualTo("00000000-0000-4000-8000-000000000003")
        assertThat(enrolled.token).isEqualTo("guided-secret")
        val sent = transport.requests.single()
        assertThat(sent.path).isEqualTo("/v1/guided/enroll")
        assertThat(sent.headers["Authorization"]).isEqualTo("Bearer legacy-secret")
        assertThat(sent.body).isNull()
    }

    @Test
    fun `enroll distinguishes not-enrolled from already-exchanged`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)

        transport.handler = {
            HttpResponse(
                403,
                """{"error":{"code":"not_enrolled","message":"no enrollment"}}"""
                    .toByteArray(Charsets.UTF_8),
            )
        }
        assertThat(client.enroll("legacy")).isInstanceOf(EnrollOutcome.NotEnrolled::class.java)

        transport.handler = {
            HttpResponse(
                403,
                """{"error":{"code":"already_exchanged","message":"spent"}}"""
                    .toByteArray(Charsets.UTF_8),
            )
        }
        assertThat(client.enroll("legacy")).isInstanceOf(EnrollOutcome.AlreadyExchanged::class.java)
    }

    @Test
    fun `transport exceptions propagate to the caller`() = runTest {
        val transport = ScriptedTransport()
        val client = GuidedControlClient(transport)
        transport.handler = { throw TransportException("down") }

        var thrown = false
        try {
            client.getSession("t", "00000000-0000-4000-8000-000000000011")
        } catch (e: TransportException) {
            thrown = true
        }
        assertThat(thrown).isTrue()
    }
}
