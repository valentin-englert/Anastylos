package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.GuidedAssociation
import org.anastylos.capture.data.GuidedAssociationRepository
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ExportRecord
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.ReviewState
import org.anastylos.capture.domain.Transport
import org.anastylos.capture.domain.sha256Hex
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind
import org.junit.Test

class GuidedDeliveryTest {
    private val json = Json { ignoreUnknownKeys = true }
    private val stamp = Instant.parse("2026-10-29T12:00:00Z")
    private val guidedSessionId = "00000000-0000-4000-8000-000000000041"

    private class ScriptedTransport(
        var handler: (HttpRequest) -> HttpResponse = { HttpResponse(500, ByteArray(0)) },
    ) : HttpTransport {
        val requests = mutableListOf<HttpRequest>()
        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return handler(request)
        }
    }

    private class FakeRepository : EvidenceRepository {
        val sessions = mutableMapOf<String, CaptureSession>()
        val observations = mutableMapOf<String, ComponentObservation>()
        val synced = mutableListOf<Pair<String, String>>()

        override fun observeSessions(): Flow<List<CaptureSession>> = flowOf(sessions.values.toList())
        override fun observeSession(id: String): Flow<CaptureSession?> = flowOf(sessions[id])
        override suspend fun getSession(id: String): CaptureSession? = sessions[id]
        override suspend fun createSession(name: String): CaptureSession = TODO()
        override suspend fun ensureSession(id: String, name: String): CaptureSession = TODO()
        override suspend fun renameSession(id: String, name: String) = TODO()
        override suspend fun deleteSession(id: String) = TODO()
        override fun observeObservations(sessionId: String): Flow<List<ComponentObservation>> =
            flowOf(observations.values.filter { it.sessionId == sessionId })

        override fun observeObservation(id: String): Flow<ComponentObservation?> =
            flowOf(observations[id])

        override suspend fun getObservation(id: String): ComponentObservation? = observations[id]
        override suspend fun getAllObservations(): List<ComponentObservation> =
            observations.values.toList()

        override suspend fun createObservation(sessionId: String, source: CaptureSource) = TODO()
        override suspend fun saveObservation(obs: ComponentObservation) {
            observations[obs.observationId] = obs
        }

        override suspend fun deleteObservation(id: String) {
            observations.remove(id)
        }

        override suspend fun recordImage(image: EvidenceImage) = TODO()
        override suspend fun markSynced(
            observationIds: List<String>,
            packageId: String,
            exportedAt: Instant,
        ) {
            for (id in observationIds) {
                synced += id to packageId
                val current = observations[id] ?: continue
                observations[id] = current.copy(
                    exportHistory = current.exportHistory +
                        ExportRecord(packageId, exportedAt, Transport.SYNC),
                )
            }
        }
    }

    private class FakeAssociations : GuidedAssociationRepository {
        val rows = mutableMapOf<String, GuidedAssociation>()
        override suspend fun recordGuidedAssociation(association: GuidedAssociation) {
            rows[association.imageId] = association
        }

        override suspend fun getGuidedAssociation(imageId: String): GuidedAssociation? = rows[imageId]

        override fun observeGuidedAssociation(imageId: String): Flow<GuidedAssociation?> =
            kotlinx.coroutines.flow.MutableStateFlow(rows[imageId])
    }

    private val session = CaptureSession(
        sessionId = "11111111-1111-4111-8111-111111111111",
        name = "Guided capture",
        createdAt = stamp,
        updatedAt = stamp,
        assemblyMemorySession = null,
        trayDefault = null,
    )

    private fun obsUuid(label: String) =
        UUID.nameUUIDFromBytes("obs:$label".toByteArray()).toString()

    private fun attemptUuid(label: String) =
        UUID.nameUUIDFromBytes("attempt:$label".toByteArray()).toString()

    private fun requestUuid(label: String) =
        UUID.nameUUIDFromBytes("request:$label".toByteArray()).toString()

    private fun frame(label: String, bytes: ByteArray): ComponentObservation {
        val observationId = obsUuid(label)
        val imageId = UUID.nameUUIDFromBytes("img:$label".toByteArray()).toString()
        return ComponentObservation(
            observationId = observationId,
            sessionId = session.sessionId,
            componentReference = null,
            trayReference = null,
            capturedAt = stamp,
            captureSource = CaptureSource.CAMERA,
            reviewState = ReviewState.UNREVIEWED,
            operatorNotes = null,
            markings = emptyList(),
            category = ConfirmedField(null, EpistemicState.UNKNOWN),
            measurements = emptyList(),
            quantity = ConfirmedField(null, EpistemicState.UNKNOWN),
            candidates = emptyList(),
            appVersion = "0.1.0",
            userAgent = null,
            exportHistory = emptyList(),
            images = listOf(
                EvidenceImage(
                    imageId = imageId,
                    observationId = observationId,
                    role = ImageRole.OTHER,
                    filename = "still.jpg",
                    mime = "image/jpeg",
                    byteLength = bytes.size.toLong(),
                    sha256 = bytes.sha256Hex(),
                    capturedAt = stamp,
                    crop = null,
                ),
            ),
        )
    }

    private fun association(obs: ComponentObservation, attemptId: String? = attemptUuid("att-1")) =
        GuidedAssociation(
            imageId = obs.images.single().imageId,
            observationId = obs.observationId,
            packageId = null,
            guidedSessionId = guidedSessionId,
            requestId = requestUuid("req-1"),
            requestRevision = 1,
            attemptId = attemptId,
            attemptNumber = 1,
            sidecarRevision = 0,
            deliveryState = DeliveryState.PENDING_PACKAGE,
            receiptStatus = null,
            updatedAt = stamp,
        )

    private fun ack(packageId: String, duplicate: Boolean = false, status: Int = 201, receipt: String? = null) =
        HttpResponse(
            status,
            ("""{"duplicate": $duplicate, "report": {"package_id": "$packageId", "status": "valid"}""" +
                (receipt?.let { ""","receipt": {"status": "$it"}}""" } ?: "}"))
                .toByteArray(Charsets.UTF_8),
        )

    private fun bindAck(
        status: String = "asset_bound",
        attemptId: String = attemptUuid("att-1"),
    ) =
        HttpResponse(
            200,
            """{"attempt_id":"$attemptId","attempt_number":1,"remaining_exposures":19,"grant_id":"g-1","session_revision":4,"status":"$status"}"""
                .toByteArray(Charsets.UTF_8),
        )

    private fun sidecarAck(
        state: String = "SPATIAL_PENDING",
        receipt: String? = null,
        attemptId: String = attemptUuid("att-1"),
        requestId: String = requestUuid("req-1"),
    ) =
        HttpResponse(
            200,
            """{"attempt_id":"$attemptId","request_id":"$requestId","request_revision":1,"state":"$state","receipt_status":${receipt?.let { "\"$it\"" } ?: "null"}}"""
                .toByteArray(Charsets.UTF_8),
        )

    private fun sidecarInput(): SpatialSidecar.Input {
        val sha = "ab".repeat(32)
        return SpatialSidecar.Input(
            evidenceId = "00000000-0000-4000-8000-000000000001",
            sessionId = guidedSessionId,
            sourceId = "00000000-0000-4000-8000-000000000003",
            requestId = "00000000-0000-4000-8000-000000000004",
            requestRevision = 1,
            attemptId = "00000000-0000-4000-8000-000000000005",
            createdAt = "2026-10-29T12:01:10Z",
            image = SpatialSidecar.Image(
                packageId = "00000000-0000-4000-8000-000000000006",
                observationId = "00000000-0000-4000-8000-000000000007",
                imageId = "00000000-0000-4000-8000-000000000008",
                assetId = "00000000-0000-4000-8000-000000000009",
                sha256 = sha,
                byteLength = 259,
                mime = "image/jpeg",
                widthPx = 4000,
                heightPx = 3000,
                exifOrientation = 1,
            ),
            camera = SpatialSidecar.Camera(cameraId = "camera:0", lensFacing = "back", sensorMode = "default"),
            calibration = SpatialSidecar.Calibration(
                nominalDescriptor = "factory",
                layoutSha256 = "ef".repeat(32),
                boardFrameId = "surface_a3",
                metricValidity = "nominal_only",
            ),
            analysisWidthPx = 1280,
            analysisHeightPx = 720,
            analysisRotationDeg = 0,
            transformEpoch = "00000000-0000-4000-8000-00000000000c",
            captureTime = SpatialSidecar.CaptureTime(
                callbackUtc = "2026-10-29T12:01:10Z",
                callbackMonotonicNs = "987654321",
                bootId = "00000000-0000-4000-8000-00000000000d",
            ),
            pose = SpatialSidecar.Pose(
                tBC = listOf(1.0, 0.0, 0.0, 120.0, 0.0, 1.0, 0.0, 70.0, 0.0, 0.0, 1.0, -450.0, 0.0, 0.0, 0.0, 1.0),
                estimatorStatus = org.anastylos.capture.guidance.WireRegistration.VALID,
                rmsPx = 0.8,
                detectedMarkerIds = listOf(10, 11, 12, 13),
                residualReferenceWidthPx = 1280,
                sampleAgeMs = 42.0,
                imageSha256 = sha,
            ),
            calibrated = false,
            target = SpatialSidecar.Target(
                goalBytes = """{"kind":"pose"}""".toByteArray(Charsets.UTF_8),
                roiVerticesMm = listOf(listOf(0.0, 0.0, 0.0), listOf(1.0, 0.0, 0.0)),
            ),
            quality = SpatialSidecar.Quality(
                rmsPx = 0.8, rmsGatePx = 1.2,
                motionMedianPx = 0.3, motionP90Px = 0.9,
                motionMedianGatePx = 0.7, motionP90GatePx = 1.5,
                sharpness = 120.0, sharpnessReference = 100.0, sharpnessRatioGate = 0.7,
                darkFraction = 0.01, saturatedFraction = 0.01, badExposureGate = 0.05,
                visibility = "visible", inFrame = true,
                thresholdConfigJson = """{"profile":"guidance-hypothesis-1"}""",
            ),
            appVersion = "0.1.0",
            buildSha = "0.1.0+1",
            algorithmConfigJson = """{"solver":"iterative"}""",
        )
    }

    private fun delivery(
        repo: FakeRepository,
        associations: FakeAssociations,
        bytes: Map<String, ByteArray>,
        transport: ScriptedTransport,
        sleeps: MutableList<Long> = mutableListOf(),
    ) = GuidedDelivery(
        guided = GuidedControlClient(transport, Dispatchers.Unconfined),
        packages = ReceiverClient(transport, Dispatchers.Unconfined),
        repo = repo,
        associations = associations,
        readOriginal = { _, imageId, _ -> bytes[imageId] },
        appVersion = "0.1.0",
        clock = { stamp },
        uuid = { "00000000-0000-4000-8000-0000000000ff" },
        maxAttempts = 3,
        sleeper = { sleeps += it },
        ioDispatcher = Dispatchers.Unconfined,
    )

    @Test
    fun `happy path uploads binds and lands the sidecar`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs))
        val transport = ScriptedTransport()
        val expectedPackage = framePackageId(obs)
        transport.handler = { request ->
            when {
                request.path == "/v1/packages" -> ack(expectedPackage, receipt = "package_only")
                request.path.endsWith("/asset-binding") -> bindAck()
                request.path.endsWith("/spatial") -> sidecarAck()
                else -> HttpResponse(500, ByteArray(0))
            }
        }
        val underTest = delivery(repo, associations, mapOf(obs.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = sidecarInput(),
            ),
        ) as DeliveryOutcome.Delivered

        assertThat(outcome.deliveryState).isEqualTo(DeliveryState.SPATIAL_PENDING)
        assertThat(outcome.sidecarUncertain).isFalse()
        assertThat(repo.synced).hasSize(1)
        val link = associations.getGuidedAssociation(obs.images.single().imageId)!!
        assertThat(link.packageId).isEqualTo(expectedPackage)
        assertThat(link.sidecarRevision).isEqualTo(1)
        // The binding carries the true content hashes.
        val bindingBody = transport.requests
            .first { it.path.endsWith("/asset-binding") }.body!!.toString(Charsets.UTF_8)
        val binding = json.decodeFromString(GuidedAssetBinding.serializer(), bindingBody)
        assertThat(binding.imageSha256).isEqualTo(bytes.sha256Hex())
        assertThat(binding.packageId).isEqualTo(expectedPackage)
        // G0 order: bind, then the guided upload that joins the receipt, then the sidecar.
        assertThat(transport.requests.map { it.path.substringAfterLast('/') })
            .containsExactly("asset-binding", "packages", "spatial").inOrder()
        val upload = transport.requests.single { it.path == "/v1/packages" }
        assertThat(upload.headers["Authorization"]).isEqualTo("Bearer guided")
        assertThat(upload.headers["X-Guided-Attempt-Id"]).isEqualTo(attemptUuid("att-1"))
        assertThat(upload.headers["X-Guided-Request-Id"]).isEqualTo(requestUuid("req-1"))
        assertThat(upload.headers["X-Guided-Request-Revision"]).isEqualTo("1")
        assertThat(upload.headers["X-Guided-Session-Id"]).isEqualTo(guidedSessionId)
        assertThat(link.receiptStatus).isEqualTo(ReceiptKind.PACKAGE_ONLY)
        val sidecarBody = transport.requests
            .first { it.path.endsWith("/spatial") }.body!!.toString(Charsets.UTF_8)
        assertThat(json.parseToJsonElement(sidecarBody).jsonObject.getValue("sidecar").jsonObject)
            .isNotNull()
    }

    @Test
    fun `package path runs without a pose`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs))
        val transport = ScriptedTransport()
        transport.handler = { request ->
            when {
                request.path == "/v1/packages" -> ack(framePackageId(obs))
                request.path.endsWith("/asset-binding") -> bindAck()
                else -> HttpResponse(500, ByteArray(0))
            }
        }
        val underTest = delivery(repo, associations, mapOf(obs.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = null,
            ),
        ) as DeliveryOutcome.Delivered

        assertThat(outcome.deliveryState).isEqualTo(DeliveryState.PACKAGE_ACCEPTED)
        assertThat(outcome.sidecarSkippedReason).isNotNull()
        assertThat(transport.requests.none { it.path.endsWith("/spatial") }).isTrue()
    }

    @Test
    fun `manual capture without attempt skips binding`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs, attemptId = null))
        val transport = ScriptedTransport { ack(framePackageId(obs)) }
        val underTest = delivery(repo, associations, mapOf(obs.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = sidecarInput(),
            ),
        ) as DeliveryOutcome.Delivered

        assertThat(outcome.deliveryState).isEqualTo(DeliveryState.PACKAGE_ACCEPTED)
        assertThat(transport.requests.none { it.path.contains("/guided/") }).isTrue()
    }

    @Test
    fun `rejected guided upload is terminal`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs))
        val transport = ScriptedTransport { request ->
            if (request.path.endsWith("/asset-binding")) bindAck() else HttpResponse(422, ByteArray(0))
        }
        val underTest = delivery(repo, associations, mapOf(obs.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = sidecarInput(),
            ),
        )

        assertThat(outcome).isInstanceOf(DeliveryOutcome.PackageRejected::class.java)
        assertThat(repo.synced).isEmpty()
        assertThat(associations.getGuidedAssociation(obs.images.single().imageId)!!.deliveryState)
            .isEqualTo(DeliveryState.REJECTED)
        assertThat(transport.requests.map { it.path.substringAfterLast('/') })
            .containsExactly("asset-binding", "packages").inOrder()
    }

    @Test
    fun `flaky upload recovers within budget`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs, attemptId = null))
        val transport = ScriptedTransport()
        var calls = 0
        transport.handler = {
            calls++
            if (calls == 1) throw TransportException("down") else ack(framePackageId(obs))
        }
        val sleeps = mutableListOf<Long>()
        val underTest = delivery(
            repo, associations, mapOf(obs.images.single().imageId to bytes), transport, sleeps,
        )

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = null,
            ),
        ) as DeliveryOutcome.Delivered

        assertThat(outcome.deliveryState).isEqualTo(DeliveryState.PACKAGE_ACCEPTED)
        assertThat(sleeps).hasSize(1)
    }

    @Test
    fun `unauthorized upload marks nothing`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs))
        val transport = ScriptedTransport { HttpResponse(401, ByteArray(0)) }
        val underTest = delivery(repo, associations, mapOf(obs.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = sidecarInput(),
            ),
        )

        assertThat(outcome).isInstanceOf(DeliveryOutcome.AuthLost::class.java)
        assertThat(repo.synced).isEmpty()
    }

    @Test
    fun `binding conflict fails terminally`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs))
        val transport = ScriptedTransport()
        var binds = 0
        transport.handler = { request ->
            when {
                request.path == "/v1/packages" -> ack(framePackageId(obs))
                request.path.endsWith("/asset-binding") -> {
                    binds++
                    HttpResponse(
                        409,
                        """{"error":{"code":"unarmed","message":"grant revoked"}}"""
                            .toByteArray(Charsets.UTF_8),
                    )
                }
                else -> HttpResponse(500, ByteArray(0))
            }
        }
        val underTest = delivery(repo, associations, mapOf(obs.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = sidecarInput(),
            ),
        )

        assertThat(outcome).isInstanceOf(DeliveryOutcome.BindingFailed::class.java)
        assertThat(binds).isEqualTo(1)
        assertThat(transport.requests.none { it.path.endsWith("/spatial") }).isTrue()
    }

    @Test
    fun `sidecar revision conflict lands uncertain`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        repo.observations[obs.observationId] = obs
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(association(obs))
        val transport = ScriptedTransport()
        transport.handler = { request ->
            when {
                request.path == "/v1/packages" -> ack(framePackageId(obs))
                request.path.endsWith("/asset-binding") -> bindAck()
                request.path.endsWith("/spatial") -> HttpResponse(
                    409,
                    """{"error":{"code":"revision_conflict","message":"stale","current_revision":1}}"""
                        .toByteArray(Charsets.UTF_8),
                )
                else -> HttpResponse(500, ByteArray(0))
            }
        }
        val underTest = delivery(repo, associations, mapOf(obs.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = obs.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = sidecarInput(),
            ),
        ) as DeliveryOutcome.Delivered

        assertThat(outcome.sidecarUncertain).isTrue()
        assertThat(outcome.deliveryState).isEqualTo(DeliveryState.SPATIAL_PENDING)
    }

    @Test
    fun `a package marked by an earlier upload still joins its receipt by guided upload`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val bytes = "still-bytes".toByteArray()
        val obs = frame("obs-1", bytes)
        val packageId = framePackageId(obs)
        val marked = obs.copy(
            exportHistory = listOf(ExportRecord(packageId, stamp, Transport.SYNC)),
        )
        repo.observations[marked.observationId] = marked
        val associations = FakeAssociations()
        associations.recordGuidedAssociation(
            association(marked).copy(packageId = packageId, deliveryState = DeliveryState.PACKAGE_ACCEPTED),
        )
        val transport = ScriptedTransport()
        transport.handler = { request ->
            when {
                request.path.endsWith("/asset-binding") -> bindAck()
                request.path == "/v1/packages" -> ack(packageId, duplicate = true, status = 200, receipt = "package_only")
                request.path.endsWith("/spatial") -> sidecarAck(receipt = "package_only")
                else -> HttpResponse(500, ByteArray(0))
            }
        }
        val underTest = delivery(repo, associations, mapOf(marked.images.single().imageId to bytes), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = marked.images.single().imageId,
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = sidecarInput(),
            ),
        ) as DeliveryOutcome.Delivered

        assertThat(transport.requests.single { it.path == "/v1/packages" }.headers["X-Guided-Attempt-Id"])
            .isEqualTo(attemptUuid("att-1"))
        assertThat(outcome.deliveryState).isEqualTo(DeliveryState.PACKAGE_ACCEPTED)
        assertThat(outcome.receiptStatus).isEqualTo(ReceiptKind.PACKAGE_ONLY)
    }

    @Test
    fun `unknown image is blocked without wire`() = runTest {
        val repo = FakeRepository()
        val associations = FakeAssociations()
        val transport = ScriptedTransport()
        val underTest = delivery(repo, associations, emptyMap(), transport)

        val outcome = underTest.deliver(
            DeliveryInput(
                imageId = "nope",
                legacyToken = "legacy",
                guidedToken = "guided",
                guidedSessionId = guidedSessionId,
                sidecar = null,
            ),
        )

        assertThat(outcome).isInstanceOf(DeliveryOutcome.Blocked::class.java)
        assertThat(transport.requests).isEmpty()
    }
}
