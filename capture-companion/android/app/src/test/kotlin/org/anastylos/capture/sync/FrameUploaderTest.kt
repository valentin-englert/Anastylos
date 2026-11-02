package org.anastylos.capture.sync

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.anastylos.capture.data.EvidenceRepository
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
import org.junit.Test

class FrameUploaderTest {
    private class FakeRepository : EvidenceRepository {
        val sessions = mutableMapOf<String, CaptureSession>()
        val observations = mutableMapOf<String, ComponentObservation>()
        val synced = mutableListOf<Triple<String, String, Instant>>()

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

        override suspend fun createObservation(
            sessionId: String,
            source: CaptureSource,
        ): ComponentObservation = TODO()

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
                synced += Triple(id, packageId, exportedAt)
                val current = observations[id] ?: continue
                observations[id] = current.copy(
                    exportHistory = current.exportHistory +
                        ExportRecord(packageId, exportedAt, Transport.SYNC),
                )
            }
        }
    }

    private class ScriptedTransport(
        private val script: MutableList<Any>,
    ) : HttpTransport {
        val requests = mutableListOf<HttpRequest>()

        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return when (val next = script.removeAt(0)) {
                is HttpResponse -> next
                is TransportException -> throw next
                else -> throw AssertionError("bad script entry: $next")
            }
        }
    }

    private val session = CaptureSession(
        sessionId = "11111111-1111-4111-8111-111111111111",
        name = "SNES teardown",
        createdAt = Instant.parse("2026-10-27T09:00:00.000Z"),
        updatedAt = Instant.parse("2026-10-27T09:00:00.000Z"),
        assemblyMemorySession = null,
        trayDefault = null,
    )

    private fun frame(
        observationId: String,
        capturedAt: String,
        bytes: ByteArray,
        history: List<ExportRecord> = emptyList(),
    ): ComponentObservation {
        val imageId = java.util.UUID.nameUUIDFromBytes("img:$observationId".toByteArray()).toString()
        return ComponentObservation(
            observationId = observationId,
            sessionId = session.sessionId,
            componentReference = null,
            trayReference = null,
            capturedAt = Instant.parse(capturedAt),
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
            exportHistory = history,
            images = listOf(
                EvidenceImage(
                    imageId = imageId,
                    observationId = observationId,
                    role = ImageRole.OTHER,
                    filename = "frame.jpg",
                    mime = "image/jpeg",
                    byteLength = bytes.size.toLong(),
                    sha256 = bytes.sha256Hex(),
                    capturedAt = Instant.parse(capturedAt),
                    crop = null,
                ),
            ),
        )
    }

    private fun ack(packageId: String, duplicate: Boolean, status: Int = 201) =
        HttpResponse(
            status,
            """{"duplicate": $duplicate, "report": {"package_id": "$packageId", "status": "valid"}}"""
                .toByteArray(Charsets.UTF_8),
        )

    private fun uploader(
        repo: FakeRepository,
        bytes: Map<String, ByteArray>,
        script: MutableList<Any>,
        sleeps: MutableList<Long> = mutableListOf(),
        maxAttempts: Int = 5,
    ): Pair<FrameUploader, ScriptedTransport> {
        val transport = ScriptedTransport(script)
        val client = ReceiverClient(transport, Dispatchers.Unconfined)
        return FrameUploader(
            repo = repo,
            resolveOriginal = { bytes[it] },
            client = client,
            token = "tok",
            appVersion = "0.1.0",
            clock = { Instant.parse("2026-10-27T09:05:00.000Z") },
            maxAttempts = maxAttempts,
            sleeper = { sleeps += it },
        ) to transport
    }

    @Test
    fun `uploads pending oldest-first and marks synced`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val late = frame("22222222-2222-4222-8222-222222222223", "2026-10-27T09:00:10.000Z", "b2".toByteArray())
        val early = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        repo.observations[late.observationId] = late
        repo.observations[early.observationId] = early
        val bytes = mapOf(
            late.images.single().imageId to "b2".toByteArray(),
            early.images.single().imageId to "b1".toByteArray(),
        )
        val script = mutableListOf<Any>(
            ack(framePackageId(early), false),
            ack(framePackageId(late), false),
        )
        val (uploader, transport) = uploader(repo, bytes, script)

        val run = uploader.uploadSession(session.sessionId)

        assertThat(transport.requests.map { it.headers["X-Package-Id"] }).containsExactly(
            framePackageId(early),
            framePackageId(late),
        ).inOrder()
        assertThat(repo.synced.map { it.first }).containsExactly(
            early.observationId,
            late.observationId,
        )
        assertThat(run.outcomes[early.observationId]).isInstanceOf(FrameOutcome.Synced::class.java)
        assertThat(run.outcomes[late.observationId]).isInstanceOf(FrameOutcome.Synced::class.java)
    }

    @Test
    fun `retry reuses the same package id`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val obs = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        repo.observations[obs.observationId] = obs
        val bytes = mapOf(obs.images.single().imageId to "b1".toByteArray())
        val sleeps = mutableListOf<Long>()
        val script = mutableListOf<Any>(
            TransportException("connection reset"),
            HttpResponse(500, "boom".toByteArray()),
            ack(framePackageId(obs), false),
        )
        val (uploader, transport) = uploader(repo, bytes, script, sleeps)

        val run = uploader.uploadSession(session.sessionId)

        assertThat(transport.requests).hasSize(3)
        assertThat(transport.requests.map { it.headers["X-Package-Id"] }.toSet())
            .containsExactly(framePackageId(obs))
        assertThat(sleeps).containsExactly(1_000L, 2_000L).inOrder()
        assertThat(repo.synced).hasSize(1)
        assertThat(run.outcomes[obs.observationId]).isInstanceOf(FrameOutcome.Synced::class.java)
    }

    @Test
    fun `duplicate acks mark synced`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val obs = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        repo.observations[obs.observationId] = obs
        val bytes = mapOf(obs.images.single().imageId to "b1".toByteArray())
        val script = mutableListOf<Any>(ack(framePackageId(obs), true, 200))
        val (uploader, _) = uploader(repo, bytes, script)

        val run = uploader.uploadSession(session.sessionId)

        val outcome = run.outcomes[obs.observationId] as FrameOutcome.Synced
        assertThat(outcome.duplicate).isTrue()
        assertThat(repo.synced.map { it.first }).containsExactly(obs.observationId)
    }

    @Test
    fun `rejected bytes are not retried and stay unmarked`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val obs = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        repo.observations[obs.observationId] = obs
        val bytes = mapOf(obs.images.single().imageId to "b1".toByteArray())
        val script = mutableListOf<Any>(
            HttpResponse(422, """{"report": {"status": "invalid"}}""".toByteArray()),
        )
        val (uploader, transport) = uploader(repo, bytes, script)

        val run = uploader.uploadSession(session.sessionId)

        assertThat(transport.requests).hasSize(1)
        assertThat(run.outcomes[obs.observationId]).isInstanceOf(FrameOutcome.Rejected::class.java)
        assertThat(repo.synced).isEmpty()
    }

    @Test
    fun `mismatched acks never mark and exhaust attempts`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val obs = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        repo.observations[obs.observationId] = obs
        val bytes = mapOf(obs.images.single().imageId to "b1".toByteArray())
        val wrong = ack("55555555-5555-5555-8555-555555555555", false)
        val script = mutableListOf<Any>(wrong, wrong, wrong)
        val (uploader, transport) = uploader(repo, bytes, script, maxAttempts = 3)

        val run = uploader.uploadSession(session.sessionId)

        assertThat(transport.requests).hasSize(3)
        assertThat(run.outcomes[obs.observationId]).isInstanceOf(FrameOutcome.Failed::class.java)
        assertThat(repo.synced).isEmpty()
    }

    @Test
    fun `unauthorized aborts the run and marks nothing`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val first = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        val second = frame("22222222-2222-4222-8222-222222222223", "2026-10-27T09:00:06.000Z", "b2".toByteArray())
        repo.observations[first.observationId] = first
        repo.observations[second.observationId] = second
        val bytes = mapOf(
            first.images.single().imageId to "b1".toByteArray(),
            second.images.single().imageId to "b2".toByteArray(),
        )
        val script = mutableListOf<Any>(HttpResponse(401, "{}".toByteArray()))
        val (uploader, transport) = uploader(repo, bytes, script)

        val run = uploader.uploadSession(session.sessionId)

        assertThat(transport.requests).hasSize(1)
        assertThat(run.outcomes[first.observationId]).isInstanceOf(FrameOutcome.Failed::class.java)
        assertThat(run.outcomes[second.observationId]).isInstanceOf(FrameOutcome.Failed::class.java)
        assertThat(repo.synced).isEmpty()
    }

    @Test
    fun `already-synced observations are skipped`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val done = frame(
            "22222222-2222-4222-8222-222222222222",
            "2026-10-27T09:00:05.000Z",
            "b1".toByteArray(),
            history = listOf(
                ExportRecord("pkg-old", Instant.parse("2026-10-27T09:01:00.000Z"), Transport.SYNC),
            ),
        )
        repo.observations[done.observationId] = done
        val (uploader, transport) = uploader(repo, emptyMap(), mutableListOf())

        val run = uploader.uploadSession(session.sessionId)

        assertThat(transport.requests).isEmpty()
        assertThat(run.outcomes).isEmpty()
    }

    @Test
    fun `missing originals block without network traffic`() = runTest {
        val repo = FakeRepository()
        repo.sessions[session.sessionId] = session
        val obs = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        repo.observations[obs.observationId] = obs
        val (uploader, transport) = uploader(repo, emptyMap(), mutableListOf())

        val run = uploader.uploadSession(session.sessionId)

        assertThat(transport.requests).isEmpty()
        assertThat(run.outcomes[obs.observationId]).isInstanceOf(FrameOutcome.Blocked::class.java)
        assertThat(repo.synced).isEmpty()
    }

    @Test
    fun `package ids are content-stable and edit-sensitive`() {
        val obs = frame("22222222-2222-4222-8222-222222222222", "2026-10-27T09:00:05.000Z", "b1".toByteArray())
        assertThat(framePackageId(obs)).isEqualTo(framePackageId(obs.copy()))
        assertThat(framePackageId(obs.copy(operatorNotes = "edited"))).isNotEqualTo(framePackageId(obs))
        val withHistory = obs.copy(
            exportHistory = listOf(
                ExportRecord("pkg-old", Instant.parse("2026-10-27T09:01:00.000Z"), Transport.SYNC),
            ),
        )
        assertThat(framePackageId(withHistory)).isEqualTo(framePackageId(obs))
    }
}
