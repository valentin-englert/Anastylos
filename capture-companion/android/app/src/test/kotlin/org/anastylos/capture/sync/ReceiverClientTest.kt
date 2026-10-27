package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.runTest
import org.junit.Test

class ReceiverClientTest {
    private class FakeTransport(
        private val handler: (HttpRequest) -> HttpResponse,
    ) : HttpTransport {
        val requests = mutableListOf<HttpRequest>()

        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return handler(request)
        }
    }

    private fun json(status: Int, body: String, headers: Map<String, String> = emptyMap()) =
        HttpResponse(status, body.toByteArray(Charsets.UTF_8), headers)

    private fun ack(packageId: String, duplicate: Boolean, status: Int = 201) = json(
        status,
        """{"duplicate": $duplicate, "report": {"package_id": "$packageId", "status": "valid"}}""",
    )

    private fun pkg(id: String) = BuiltFramePackage(
        packageId = id,
        manifestJson = "{}",
        bytes = "zip-bytes".toByteArray(Charsets.UTF_8),
        fileCount = 1,
        observationIds = emptyList(),
        packageSha256 = "ab".repeat(32),
    )

    @Test
    fun `pair posts the code and parses the token`() = runTest {
        val transport = FakeTransport {
            json(200, """{"workstation_id": "ws-1", "token": "tok-abc"}""")
        }
        val outcome = ReceiverClient(transport, Dispatchers.Unconfined).pair("alpha bravo")
        assertThat(outcome).isEqualTo(PairOutcome.Paired(Pairing("ws-1", "tok-abc")))
        val request = transport.requests.single()
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.path).isEqualTo("/v1/pair")
        assertThat(request.body!!.toString(Charsets.UTF_8)).contains("alpha bravo")
    }

    @Test
    fun `pair maps failures without leaking the token`() = runTest {
        suspend fun pairAs(
            status: Int,
            body: String,
            headers: Map<String, String> = emptyMap(),
        ): PairOutcome {
            val transport = FakeTransport { json(status, body, headers) }
            return ReceiverClient(transport, Dispatchers.Unconfined).pair("code")
        }
        assertThat(pairAs(403, """{"error": {"code": "pairing_failed", "message": "nope"}}"""))
            .isEqualTo(PairOutcome.Rejected("nope"))
        assertThat(pairAs(429, "{}", mapOf("Retry-After" to "42")))
            .isEqualTo(PairOutcome.RateLimited(42))
        assertThat(pairAs(200, "not json")).isEqualTo(
            PairOutcome.Failed("pairing reply was not valid JSON"),
        )
        assertThat(pairAs(200, """{"workstation_id": "", "token": ""}"""))
            .isEqualTo(PairOutcome.Failed("pairing reply missed workstation_id or token"))
    }

    @Test
    fun `upload sends auth and package headers`() = runTest {
        val transport = FakeTransport { ack("44444444-4444-4444-8444-444444444444", false) }
        val client = ReceiverClient(transport, Dispatchers.Unconfined)
        val outcome = client.uploadPackage("tok", pkg("44444444-4444-4444-8444-444444444444"))
        assertThat(outcome).isInstanceOf(UploadOutcome.Staged::class.java)
        val request = transport.requests.single()
        assertThat(request.headers["Authorization"]).isEqualTo("Bearer tok")
        assertThat(request.headers["X-Package-Id"]).isEqualTo("44444444-4444-4444-8444-444444444444")
        assertThat(request.headers["X-Package-Sha256"]).isEqualTo("ab".repeat(32))
    }

    @Test
    fun `upload distinguishes fresh staging from duplicates`() = runTest {
        val id = "44444444-4444-4444-8444-444444444444"
        val fresh = ReceiverClient(FakeTransport { ack(id, false) }, Dispatchers.Unconfined)
        assertThat(fresh.uploadPackage("t", pkg(id)))
            .isEqualTo(UploadOutcome.Staged(false, ack(id, false).bodyText()))
        val dup = ReceiverClient(FakeTransport { ack(id, true, 200) }, Dispatchers.Unconfined)
        val outcome = dup.uploadPackage("t", pkg(id)) as UploadOutcome.Staged
        assertThat(outcome.duplicate).isTrue()
    }

    @Test
    fun `upload refuses acks that reference another package`() = runTest {
        val transport = FakeTransport { ack("55555555-5555-5555-8555-555555555555", false) }
        val outcome = ReceiverClient(transport, Dispatchers.Unconfined)
            .uploadPackage("t", pkg("44444444-4444-4444-8444-444444444444"))
        assertThat(outcome).isEqualTo(
            UploadOutcome.Failed("acceptance report referenced another package id"),
        )
    }

    @Test
    fun `upload maps workstation verdicts`() = runTest {
        suspend fun uploadAs(status: Int, body: String): UploadOutcome {
            val transport = FakeTransport { json(status, body) }
            return ReceiverClient(transport, Dispatchers.Unconfined)
                .uploadPackage("t", pkg("44444444-4444-4444-8444-444444444444"))
        }
        val rejected = uploadAs(422, """{"report": {"status": "invalid"}}""")
        assertThat(rejected).isInstanceOf(UploadOutcome.Rejected::class.java)
        assertThat(uploadAs(401, """{"error": {"code": "x", "message": "bad token"}}"""))
            .isEqualTo(UploadOutcome.Unauthorized("bad token"))
        assertThat(uploadAs(413, "{}")).isEqualTo(UploadOutcome.TooLarge("package too large"))
        assertThat(uploadAs(429, "{}",)).isEqualTo(UploadOutcome.RateLimited(null))
        assertThat(uploadAs(500, "boom")).isEqualTo(UploadOutcome.Failed("upload failed: HTTP 500"))
        assertThat(uploadAs(201, "not json")).isEqualTo(
            UploadOutcome.Failed("acceptance reply was not valid JSON"),
        )
        assertThat(uploadAs(201, """{"duplicate": true}""")).isEqualTo(
            UploadOutcome.Failed("acceptance reply missed its report"),
        )
    }

    @Test
    fun `feed parses staged packages`() = runTest {
        val transport = FakeTransport {
            json(
                200,
                """{"packages": [{
                  "package_id": "44444444-4444-4444-8444-444444444444",
                  "staged_at": "2026-10-27T09:01:00.000Z",
                  "zip_sha256": "${"cd".repeat(32)}",
                  "status": "valid",
                  "session_ids": ["11111111-1111-4111-8111-111111111111"],
                  "observation_ids": ["22222222-2222-4222-8222-222222222222"],
                  "accepted_for_review": ["22222222-2222-4222-8222-222222222222"],
                  "duplicate_deliveries": 2,
                  "future_field": "ignored"
                }]}""",
            )
        }
        val outcome = ReceiverClient(transport, Dispatchers.Unconfined)
            .sessionFeed("t", "11111111-1111-4111-8111-111111111111")
        val feed = outcome as FeedOutcome.Feed
        assertThat(feed.packages).hasSize(1)
        val first = feed.packages.single()
        assertThat(first.packageId).isEqualTo("44444444-4444-4444-8444-444444444444")
        assertThat(first.sessionIds).containsExactly("11111111-1111-4111-8111-111111111111")
        assertThat(first.duplicateDeliveries).isEqualTo(2)
        assertThat(transport.requests.single().path)
            .isEqualTo("/v1/packages?session_id=11111111-1111-4111-8111-111111111111")
    }

    @Test
    fun `feed maps auth and filter failures`() = runTest {
        suspend fun feedAs(status: Int, body: String): FeedOutcome {
            val transport = FakeTransport { json(status, body) }
            return ReceiverClient(transport, Dispatchers.Unconfined).sessionFeed("t", "s")
        }
        assertThat(feedAs(401, "{}")).isEqualTo(FeedOutcome.Unauthorized("unauthorized"))
        assertThat(feedAs(400, """{"error": {"code": "x", "message": "bad uuid"}}"""))
            .isEqualTo(FeedOutcome.BadFilter("bad uuid"))
        assertThat(feedAs(200, "nope")).isEqualTo(
            FeedOutcome.Failed("feed reply was not valid JSON"),
        )
    }
}
