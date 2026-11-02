package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.io.File
import java.time.Instant
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.anastylos.capture.data.GuidedAssociation
import org.anastylos.capture.data.GuidedAssociationRepository
import org.anastylos.capture.data.GuidedPrefs
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionState
import org.junit.Test

class GuidedSessionControllerTest {
    private val json = Json { ignoreUnknownKeys = true }
    private val sessionId = "00000000-0000-4000-8000-000000000011"
    private val sourceId = "00000000-0000-4000-8000-000000000003"

    private class ScriptedTransport(
        var handler: (HttpRequest) -> HttpResponse = { HttpResponse(500, ByteArray(0)) },
    ) : HttpTransport {
        val requests = mutableListOf<HttpRequest>()
        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return handler(request)
        }
    }

    private class FakeAssociations : GuidedAssociationRepository {
        val rows = mutableMapOf<String, GuidedAssociation>()
        override suspend fun recordGuidedAssociation(association: GuidedAssociation) {
            rows[association.imageId] = association
        }

        override suspend fun getGuidedAssociation(imageId: String): GuidedAssociation? = rows[imageId]

        override fun observeGuidedAssociation(imageId: String): Flow<GuidedAssociation?> =
            MutableStateFlow(rows[imageId])
    }

    private class FakePrefs : GuidedPrefs {
        override val sharpnessReference: Flow<Double?> = MutableStateFlow(null)
        override suspend fun setSharpnessReference(value: Double) = Unit
        override suspend fun clearSharpnessReference() = Unit
        private val cursors = mutableMapOf<String, MutableStateFlow<String>>()
        private val boards = mutableMapOf<String, MutableStateFlow<String?>>()
        override fun changeCursor(sessionId: String): Flow<String> =
            cursors.getOrPut(sessionId) { MutableStateFlow("0") }

        override suspend fun setChangeCursor(sessionId: String, cursor: String) {
            cursors.getOrPut(sessionId) { MutableStateFlow("0") }.value = cursor
        }

        override fun boardFrameFor(boardUuid: String): Flow<String?> =
            boards.getOrPut(boardUuid) { MutableStateFlow(null) }

        override suspend fun setBoardFrame(boardUuid: String, frameId: String) {
            boards.getOrPut(boardUuid) { MutableStateFlow(null) }.value = frameId
        }
    }

    private fun pairing() = StoredPairing(
        baseUrl = "https://ws:8443",
        workstationId = "ws-1",
        token = "legacy-secret",
        pinnedSpkiSha256Hex = "ab".repeat(32),
        guidedSourceId = sourceId,
        guidedToken = "guided-secret",
        guidedTokenExpiresAt = "2026-11-28T12:01:00Z",
    )

    private fun fixture(name: String): String {
        var dir: File? = File(System.getProperty("user.dir"))
        repeat(6) {
            val base = dir ?: return@repeat
            for (relative in arrayOf(
                "receiver/tests/fixtures",
                "capture-companion/receiver/tests/fixtures",
                "../receiver/tests/fixtures",
            )) {
                val candidate = File(base, "$relative/$name")
                if (candidate.isFile) return candidate.readText(Charsets.UTF_8)
            }
            dir = base.parentFile
        }
        throw AssertionError("fixture not found: $name")
    }

    private fun part(name: String, key: String): String =
        json.parseToJsonElement(fixture(name)).jsonObject.getValue(key).toString()

    /**
     * The frozen consent fixture belongs to session `...041`; the
     * client's echo rule rejects a reply naming another session, so
     * tests retarget the shape at the attached session id.
     */
    private fun consentSessionFor(sessionId: String): String =
        part("guided-wire-consent.json", "response")
            .replace("00000000-0000-4000-8000-000000000041", sessionId)

    private fun okJson(body: String) = HttpResponse(200, body.toByteArray(Charsets.UTF_8))

    private fun emptyPage(cursor: String) = okJson(
        """{"events":[],"next_cursor":"$cursor","snapshot_required":false,"server_time":"2026-10-29T12:01:00Z","lease_expires_at":null,"lease_remaining_ms":0}""",
    )

    /**
     * Runs [block] with polling started; the controller always
     * closes (even on failure) so the poll loop can never wedge
     * the test scheduler.
     */
    private suspend fun TestScope.withPolling(
        handler: (HttpRequest) -> HttpResponse,
        prefs: FakePrefs = FakePrefs(),
        associations: FakeAssociations = FakeAssociations(),
        block: suspend TestScope.(controller: GuidedSessionController, transport: ScriptedTransport) -> Unit,
    ) {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val transport = ScriptedTransport(handler)
        val store = EphemeralPairingStore()
        store.save(pairing())
        val controller = GuidedSessionController(
            store, transport, associations, prefs,
            ioDispatcher = dispatcher, pollIntervalMillis = 50,
        )
        try {
            controller.attach(sessionId)
            controller.startPolling()
            block(controller, transport)
        } finally {
            controller.close()
        }
    }

    private suspend fun TestScope.withDetached(
        handler: (HttpRequest) -> HttpResponse,
        block: suspend TestScope.(controller: GuidedSessionController, transport: ScriptedTransport) -> Unit,
    ) {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val transport = ScriptedTransport(handler)
        val store = EphemeralPairingStore()
        store.save(pairing())
        val controller = GuidedSessionController(
            store, transport, FakeAssociations(), FakePrefs(), ioDispatcher = dispatcher,
        )
        try {
            controller.attach(sessionId)
            block(controller, transport)
        } finally {
            controller.close()
        }
    }

    @Test
    fun `attach polls from the stored cursor and applies the page`() = runTest {
        val changesPage = part("guided-wire-changes.json", "response")
        val prefs = FakePrefs()
        prefs.setChangeCursor(sessionId, "0")
        var polls = 0
        withPolling(
            handler = {
                polls++
                if (polls == 1) okJson(changesPage) else emptyPage("3")
            },
            prefs = prefs,
        ) { controller, transport ->
            runCurrent()
            advanceTimeBy(120)
            runCurrent()

            val state = controller.state.value
            assertThat(state.attached).isTrue()
            assertThat(state.requests).hasSize(1)
            val request = state.requests.values.single()
            assertThat(request.status).isEqualTo("QUEUED")
            assertThat(request.target.translationToleranceMm).containsExactly(15.0, 15.0, 20.0)
            assertThat(state.cursor).isEqualTo("3")
            assertThat(prefs.changeCursor(sessionId).first()).isEqualTo("3")
            assertThat(transport.requests.first().path).endsWith("/changes?after=0")
        }
    }

    @Test
    fun `older revisions never overwrite newer ones`() = runTest {
        val requestId = "00000000-0000-4000-8000-000000000013"
        fun page(revision: Int, cursor: String) = okJson(
            """{"events":[{"change_seq":$revision,"kind":"request_revised","session_revision":$revision,"request":{"request_id":"$requestId","session_id":"$sessionId","source_id":"$sourceId","revision":$revision,"operation_id":"00000000-0000-4000-8000-000000000012","created_at":"2026-10-29T12:01:00Z","expires_at":"2026-10-29T12:03:00Z","target_label":"t","board_id":"00000000-0000-4000-8000-000000000005","calibration_id":"00000000-0000-4000-8000-000000000006","target":{"kind":"pose","T_B_C":{"from":"a","to":"b","units":"mm","matrix":[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]},"translation_tolerance_mm":[1,1,1],"rotation_tolerance_deg":1},"quality_profile_id":"q","status":"QUEUED","board_registration_epoch_id":"00000000-0000-4000-8000-000000000007","object_pose_epoch_id":"00000000-0000-4000-8000-000000000008","cad_context":null,"reason":"r"},"source_event":null,"receipt":null}],"next_cursor":"$cursor","snapshot_required":false,"server_time":"2026-10-29T12:01:00Z","lease_expires_at":null,"lease_remaining_ms":0}""",
        )
        var polls = 0
        withPolling(
            handler = {
                polls++
                when (polls) {
                    1 -> page(2, "2")
                    2 -> page(1, "2")
                    else -> emptyPage("2")
                }
            },
        ) { controller, _ ->
            runCurrent()
            advanceTimeBy(200)
            runCurrent()

            assertThat(controller.state.value.requests[requestId]?.revision).isEqualTo(2)
        }
    }

    @Test
    fun `unknown spellings are skipped with a visible error`() = runTest {
        val oddPage = okJson(
            """{"events":[{"change_seq":1,"kind":"request_created","session_revision":1,"request":{"request_id":"00000000-0000-4000-8000-000000000013","session_id":"$sessionId","source_id":"$sourceId","revision":1,"operation_id":"00000000-0000-4000-8000-000000000012","created_at":"2026-10-29T12:01:00Z","expires_at":"2026-10-29T12:03:00Z","target_label":"t","board_id":"00000000-0000-4000-8000-000000000005","calibration_id":"00000000-0000-4000-8000-000000000006","target":{"kind":"pose","T_B_C":{"from":"a","to":"b","units":"mm","matrix":[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]},"translation_tolerance_mm":[1,1,1],"rotation_tolerance_deg":1},"quality_profile_id":"q","status":"FUTURE_STATUS","board_registration_epoch_id":"00000000-0000-4000-8000-000000000007","object_pose_epoch_id":"00000000-0000-4000-8000-000000000008","cad_context":null,"reason":"r"},"source_event":null,"receipt":null},{"change_seq":2,"kind":"future_kind","session_revision":1,"request":null,"source_event":null,"receipt":null}],"next_cursor":"2","snapshot_required":false,"server_time":"2026-10-29T12:01:00Z","lease_expires_at":null,"lease_remaining_ms":0}""",
        )
        var polls = 0
        withPolling(
            handler = {
                polls++
                if (polls == 1) oddPage else emptyPage("2")
            },
        ) { controller, _ ->
            runCurrent()

            // Cursor still advances (fail closed, not stuck), nothing
            // applies, and the skip is visible on the page that skipped.
            assertThat(controller.state.value.requests).isEmpty()
            assertThat(controller.state.value.cursor).isEqualTo("2")
            assertThat(controller.state.value.lastError).contains("future_kind")
            assertThat(controller.state.value.lastError).contains("FUTURE_STATUS")

            // A later clean page clears the note again.
            advanceTimeBy(120)
            runCurrent()
            assertThat(controller.state.value.lastError).isNull()
            assertThat(controller.state.value.cursor).isEqualTo("2")
        }
    }

    @Test
    fun `receipts advance the linked association`() = runTest {
        val associations = FakeAssociations()
        val stamp = Instant.parse("2026-10-29T12:00:00Z")
        associations.recordGuidedAssociation(
            GuidedAssociation(
                imageId = "img-1", observationId = "obs-1", packageId = "pkg-1",
                guidedSessionId = sessionId, requestId = "req-1", requestRevision = 1,
                attemptId = "att-1", attemptNumber = 1, sidecarRevision = 0,
                deliveryState = DeliveryState.SPATIAL_PENDING, receiptStatus = null,
                updatedAt = stamp,
            ),
        )
        val receiptPage = okJson(
            """{"events":[{"change_seq":1,"kind":"receipt_created","session_revision":5,"request":null,"source_event":null,"receipt":{"receipt_id":"rc-1","session_id":"$sessionId","source_id":"$sourceId","request_id":"req-1","request_revision":1,"attempt_id":"att-1","package_id":"pkg-1","observation_id":"obs-1","image_id":"img-1","image_sha256":"${"ab".repeat(32)}","package_sha256":"${"cd".repeat(32)}","sidecar_sha256":null,"sidecar_revision":0,"accepted_at":"2026-10-29T12:02:00Z","status":"qualified","reason_codes":[]}}],"next_cursor":"1","snapshot_required":false,"server_time":"2026-10-29T12:01:00Z","lease_expires_at":null,"lease_remaining_ms":0}""",
        )
        var polls = 0
        withPolling(
            handler = {
                polls++
                if (polls == 1) receiptPage else emptyPage("1")
            },
            associations = associations,
        ) { controller, _ ->
            runCurrent()
            advanceTimeBy(120)
            runCurrent()

            assertThat(controller.state.value.receipts).hasSize(1)
            val link = associations.getGuidedAssociation("img-1")!!
            assertThat(link.deliveryState).isEqualTo(DeliveryState.ACKNOWLEDGED)
            assertThat(link.receiptStatus).isEqualTo(ReceiptKind.QUALIFIED)
        }
    }

    @Test
    fun `receipts for another attempt leave the link alone`() = runTest {
        val associations = FakeAssociations()
        val stamp = Instant.parse("2026-10-29T12:00:00Z")
        associations.recordGuidedAssociation(
            GuidedAssociation(
                imageId = "img-1", observationId = "obs-1", packageId = "pkg-1",
                guidedSessionId = sessionId, requestId = "req-1", requestRevision = 1,
                attemptId = "att-1", attemptNumber = 1, sidecarRevision = 0,
                deliveryState = DeliveryState.SPATIAL_PENDING, receiptStatus = null,
                updatedAt = stamp,
            ),
        )
        val receiptPage = okJson(
            """{"events":[{"change_seq":1,"kind":"receipt_created","session_revision":5,"request":null,"source_event":null,"receipt":{"receipt_id":"rc-1","session_id":"$sessionId","source_id":"$sourceId","request_id":"req-1","request_revision":1,"attempt_id":"att-OTHER","package_id":"pkg-1","observation_id":"obs-1","image_id":"img-1","image_sha256":"${"ab".repeat(32)}","package_sha256":"${"cd".repeat(32)}","sidecar_sha256":null,"sidecar_revision":0,"accepted_at":"2026-10-29T12:02:00Z","status":"rejected","reason_codes":["X"]}}],"next_cursor":"1","snapshot_required":false,"server_time":"2026-10-29T12:01:00Z","lease_expires_at":null,"lease_remaining_ms":0}""",
        )
        var polls = 0
        withPolling(
            handler = {
                polls++
                if (polls == 1) receiptPage else emptyPage("1")
            },
            associations = associations,
        ) { controller, _ ->
            runCurrent()
            advanceTimeBy(120)
            runCurrent()

            assertThat(associations.getGuidedAssociation("img-1")!!.deliveryState)
                .isEqualTo(DeliveryState.SPATIAL_PENDING)
        }
    }

    @Test
    fun `snapshot flag refetches the full session`() = runTest {
        val consentSession = consentSessionFor(sessionId)
        val flagPage = okJson(
            """{"events":[],"next_cursor":"0","snapshot_required":true,"server_time":"2026-10-29T12:01:00Z","lease_expires_at":null,"lease_remaining_ms":0}""",
        )
        val snapshot = okJson(
            """{"session":$consentSession,"requests":[],"latest_status":null,"receipts":[]}""",
        )
        var polls = 0
        withPolling(
            handler = { request ->
                if (request.path.endsWith("/changes?after=0") && polls++ == 0) {
                    flagPage
                } else if (request.path == "/v1/guided/sessions/$sessionId") {
                    snapshot
                } else {
                    emptyPage("0")
                }
            },
        ) { controller, _ ->
            runCurrent()
            advanceTimeBy(120)
            runCurrent()

            assertThat(controller.state.value.session).isEqualTo(SessionState.ACTIVE)
            assertThat(controller.state.value.grantId).isNotNull()
        }
    }

    @Test
    fun `unauthorized forces one re-exchange then continues`() = runTest {
        withPolling(
            handler = { request ->
                if (request.path == "/v1/guided/enroll") {
                    HttpResponse(
                        201,
                        """{"source_id":"$sourceId","token":"guided-fresh","expires_at":"2026-11-28T12:01:00Z"}"""
                            .toByteArray(Charsets.UTF_8),
                    )
                } else {
                    HttpResponse(401, ByteArray(0))
                }
            },
        ) { controller, transport ->
            runCurrent()
            advanceTimeBy(300)
            runCurrent()

            // Re-exchange succeeded, so polling continues; the loop
            // only stops when the refresh itself fails.
            assertThat(controller.state.value.authLost).isFalse()
            assertThat(transport.requests.map { it.path }).contains("/v1/guided/enroll")
        }
    }

    @Test
    fun `failed refresh stops the loop with auth lost`() = runTest {
        withPolling(
            handler = { request ->
                if (request.path == "/v1/guided/enroll") {
                    HttpResponse(
                        403,
                        """{"error":{"code":"not_enrolled","message":"no enrollment"}}"""
                            .toByteArray(Charsets.UTF_8),
                    )
                } else {
                    HttpResponse(401, ByteArray(0))
                }
            },
        ) { controller, transport ->
            runCurrent()
            advanceTimeBy(300)
            runCurrent()

            assertThat(controller.state.value.authLost).isTrue()
            val pollsAfterStop = transport.requests.size
            advanceTimeBy(300)
            runCurrent()
            assertThat(transport.requests.size).isEqualTo(pollsAfterStop)
        }
    }

    @Test
    fun `unknown session stops the loop`() = runTest {
        withPolling(
            handler = { HttpResponse(404, ByteArray(0)) },
        ) { controller, _ ->
            runCurrent()
            advanceTimeBy(200)
            runCurrent()

            assertThat(controller.state.value.sessionGone).isTrue()
        }
    }

    @Test
    fun `first arm fetches the session revision and sends every consent field`() = runTest {
        val consentResponse = consentSessionFor(sessionId)
        // Core opens a session DISARMED at revision 1, consent revision 0.
        val opened = consentResponse.replace("\"revision\":3", "\"revision\":1")
            .replace("\"consent_revision\":1", "\"consent_revision\":0")
            .replace("\"state\":\"ACTIVE\"", "\"state\":\"DISARMED\"")
        withDetached(
            handler = { request ->
                if (request.path.endsWith("/consent")) {
                    okJson(consentResponse)
                } else {
                    okJson("""{"session":$opened,"requests":[],"latest_status":null,"receipts":[]}""")
                }
            },
        ) { controller, transport ->
            val outcome = controller.arm(
                cameraId = "camera:0", maxImages = 20,
                expiresAt = "2026-10-29T12:21:00Z", allowAutoUpload = true,
            ) as RemoteConsentOutcome.Applied

            assertThat(outcome.session).isEqualTo(SessionState.ACTIVE)
            assertThat(outcome.revision).isEqualTo(3)
            assertThat(controller.state.value.consentRevision).isEqualTo(1)
            assertThat(transport.requests.map { it.method }).containsExactly("GET", "POST").inOrder()
            // The raw body, not a decode of it: decoding fills absent fields with
            // defaults and hid that the encoder dropped them.
            val sent = json.parseToJsonElement(transport.requests.last().body!!.toString(Charsets.UTF_8)).jsonObject
            val frozen = json.parseToJsonElement(part("guided-wire-consent.json", "request")).jsonObject
            assertThat(sent.keys).containsExactlyElementsIn(frozen.keys)
            assertThat(sent.getValue("expected_session_revision").toString()).isEqualTo("1")
            assertThat(sent.getValue("consent_revision").toString()).isEqualTo("1")
        }
    }

    @Test
    fun `attempt and binding bodies carry every frozen field even at default values`() = runTest {
        val transport = ScriptedTransport { HttpResponse(500, ByteArray(0)) }
        val client = GuidedControlClient(transport, StandardTestDispatcher(testScheduler))
        val attemptId = "00000000-0000-4000-8000-0000000000a1"
        client.registerAttempt("guided-secret", sessionId, GuidedAttemptInput(sessionId = sessionId, attemptId = attemptId))
        client.bindAsset("guided-secret", attemptId, GuidedAssetBinding(attemptId = attemptId))
        val (attempt, binding) = transport.requests.map {
            json.parseToJsonElement(it.body!!.toString(Charsets.UTF_8)).jsonObject.keys
        }
        assertThat(attempt).containsExactlyElementsIn(
            json.parseToJsonElement(part("guided-wire-attempt.json", "request")).jsonObject.keys,
        )
        assertThat(binding).containsExactlyElementsIn(
            json.parseToJsonElement(part("guided-wire-binding.json", "request")).jsonObject.keys,
        )
    }

    @Test
    fun `consent conflict refreshes and reports the revision`() = runTest {
        val consentResponse = consentSessionFor(sessionId)
        val snapshot = okJson(
            """{"session":$consentResponse,"requests":[],"latest_status":null,"receipts":[]}""",
        )
        withDetached(
            handler = { request ->
                if (request.path.endsWith("/consent")) {
                    HttpResponse(
                        409,
                        """{"error":{"code":"revision_conflict","message":"stale","current_revision":7}}"""
                            .toByteArray(Charsets.UTF_8),
                    )
                } else {
                    snapshot
                }
            },
        ) { controller, _ ->
            val outcome = controller.arm(
                cameraId = "camera:0", maxImages = 20,
                expiresAt = "2026-10-29T12:21:00Z", allowAutoUpload = true,
            ) as RemoteConsentOutcome.Conflict

            assertThat(outcome.currentRevision).isEqualTo(7)
            // The refresh applied the snapshot meanwhile.
            assertThat(controller.state.value.session).isEqualTo(SessionState.ACTIVE)
        }
    }

    @Test
    fun `outbound calls without attach fail without wire`() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val transport = ScriptedTransport()
        val store = EphemeralPairingStore()
        store.save(pairing())
        val controller = GuidedSessionController(
            store, transport, FakeAssociations(), FakePrefs(), ioDispatcher = dispatcher,
        )
        try {
            val event = GuidedSourceEvent(eventId = "e", sessionId = sessionId, sourceId = sourceId)
            assertThat(controller.postEvent(event)).isInstanceOf(RemoteEventOutcome.Failed::class.java)
            assertThat(transport.requests).isEmpty()
        } finally {
            controller.close()
        }
    }

    @Test
    fun `event builder binds session and source`() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        val store = EphemeralPairingStore()
        store.save(pairing())
        val controller = GuidedSessionController(
            store, ScriptedTransport(), FakeAssociations(), FakePrefs(),
            ioDispatcher = dispatcher,
            uuid = { "fixed-event-id" },
            utcClock = { Instant.parse("2026-10-29T12:01:10Z") },
            nanoClock = { 987654321L },
        )
        try {
            controller.attach(sessionId)
            val event = controller.buildEvent(
                kind = "accepted", requestId = "req-1", requestRevision = 1,
            )!!

            assertThat(event.eventId).isEqualTo("fixed-event-id")
            assertThat(event.sessionId).isEqualTo(sessionId)
            assertThat(event.sourceId).isEqualTo(sourceId)
            assertThat(event.monotonicNs).isEqualTo("987654321")
        } finally {
            controller.close()
        }
    }

    @Test
    fun `wire mappings fail closed on unknown spellings`() {
        assertThat(mapSessionState("ACTIVE")).isEqualTo(SessionState.ACTIVE)
        assertThat(mapSessionState("BOGUS")).isNull()
        assertThat(mapRequestStatus("HOLD_STILL")).isEqualTo(RequestState.HOLD_STILL)
        assertThat(mapRequestStatus("BOGUS")).isNull()
        assertThat(mapReceiptToDelivery("qualified")).isEqualTo(DeliveryState.ACKNOWLEDGED)
        assertThat(mapReceiptToDelivery("package_only")).isEqualTo(DeliveryState.PACKAGE_ACCEPTED)
        assertThat(mapReceiptToDelivery("rejected")).isEqualTo(DeliveryState.REJECTED)
        assertThat(mapReceiptToDelivery("late_after_cancel")).isEqualTo(DeliveryState.REJECTED)
        assertThat(mapReceiptToDelivery("bogus")).isNull()
    }

}
