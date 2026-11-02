package org.anastylos.capture.camera

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.files.EvidenceFileStore
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.ReviewState
import org.anastylos.capture.domain.isSafeFilename
import org.anastylos.capture.domain.newId
import org.anastylos.capture.domain.sha256Hex
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class FrameSamplerTest {
    @get:Rule
    val temp = TemporaryFolder()

    private class FakeRepository : EvidenceRepository {
        val observations = mutableMapOf<String, ComponentObservation>()
        val images = mutableListOf<EvidenceImage>()
        val deleted = mutableListOf<String>()
        var failRecording: Boolean = false

        override fun observeSessions(): Flow<List<CaptureSession>> = flowOf(emptyList())
        override fun observeSession(id: String): Flow<CaptureSession?> = flowOf(null)
        override suspend fun getSession(id: String): CaptureSession? = null
        override suspend fun createSession(name: String): CaptureSession = TODO()
        override suspend fun ensureSession(id: String, name: String): CaptureSession = TODO()
        override suspend fun renameSession(id: String, name: String) = TODO()
        override suspend fun deleteSession(id: String) = TODO()
        override fun observeObservations(sessionId: String): Flow<List<ComponentObservation>> =
            flowOf(emptyList())

        override fun observeObservation(id: String): Flow<ComponentObservation?> =
            flowOf(observations[id])

        override suspend fun getObservation(id: String): ComponentObservation? = observations[id]
        override suspend fun getAllObservations(): List<ComponentObservation> =
            observations.values.toList()

        override suspend fun createObservation(
            sessionId: String,
            source: CaptureSource,
        ): ComponentObservation {
            val obs = ComponentObservation(
                observationId = newId(),
                sessionId = sessionId,
                componentReference = null,
                trayReference = null,
                capturedAt = Instant.parse("2026-10-27T09:00:00.000Z"),
                captureSource = source,
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
                images = emptyList(),
            )
            observations[obs.observationId] = obs
            return obs
        }

        override suspend fun saveObservation(obs: ComponentObservation) {
            observations[obs.observationId] = obs
        }

        override suspend fun deleteObservation(id: String) {
            observations.remove(id)
            deleted += id
        }

        override suspend fun recordImage(image: EvidenceImage) {
            if (failRecording) throw IllegalStateException("simulated record failure")
            images += image
        }

        override suspend fun markSynced(
            observationIds: List<String>,
            packageId: String,
            exportedAt: Instant,
        ) = TODO()
    }

    private fun jpeg(payload: String = "still"): ByteArray =
        byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte()) + payload.toByteArray()

    @Test
    fun `adopted frames land with bytes rows and hashes`() = runTest {
        val repo = FakeRepository()
        val files = EvidenceFileStore(temp.root)
        val sampler = FrameSampler(repo, files, clock = { Instant.parse("2026-10-27T09:00:05.000Z") })
        val bytes = jpeg("frame-one")

        val event = sampler.adoptFrame(
            "session-1",
            CapturedStill(bytes, Instant.parse("2026-10-27T09:00:05.000Z")),
            sequence = 1,
        )

        val image = repo.images.single()
        assertThat(image.observationId).isEqualTo(event.observationId)
        assertThat(image.mime).isEqualTo("image/jpeg")
        assertThat(image.role).isEqualTo(ImageRole.OTHER)
        assertThat(image.byteLength).isEqualTo(bytes.size.toLong())
        assertThat(image.sha256).isEqualTo(bytes.sha256Hex())
        assertThat(isSafeFilename(image.filename)).isTrue()
        val stored = files.originalFile(event.observationId, event.imageId, "jpg")
        assertThat(stored.readBytes()).isEqualTo(bytes)
        assertThat(event.byteLength).isEqualTo(bytes.size.toLong())
    }

    @Test
    fun `adopt refuses empty oversized and non-jpeg captures`() = runTest {
        val repo = FakeRepository()
        val sampler = FrameSampler(repo, EvidenceFileStore(temp.root))
        val stamp = Instant.parse("2026-10-27T09:00:05.000Z")

        suspend fun refuses(bytes: ByteArray, fragment: String) {
            try {
                sampler.adoptFrame("session-1", CapturedStill(bytes, stamp), 1)
                throw AssertionError("expected refusal containing $fragment")
            } catch (e: FrameAdoptException) {
                assertThat(e.message).contains(fragment)
            }
        }
        refuses(ByteArray(0), "empty")
        refuses(ByteArray(25 * 1024 * 1024 + 1), "per-photo cap")
        refuses(byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47), "not a JPEG")
        assertThat(repo.observations).isEmpty()
    }

    @Test
    fun `failed adopts leave no row and no bytes`() = runTest {
        val repo = FakeRepository().also { it.failRecording = true }
        val files = EvidenceFileStore(temp.root)
        val sampler = FrameSampler(repo, files)
        val stamp = Instant.parse("2026-10-27T09:00:05.000Z")

        try {
            sampler.adoptFrame("session-1", CapturedStill(jpeg(), stamp), 1)
            throw AssertionError("expected the recording failure")
        } catch (_: IllegalStateException) {
            // Expected.
        }
        assertThat(repo.observations).isEmpty()
        assertThat(repo.deleted).hasSize(1)
        val leftovers = temp.root.walkTopDown().filter { it.isFile }.toList()
        assertThat(leftovers).isEmpty()
    }

    @Test
    fun `sampling loop stores frames until cancelled`() = runTest {
        val repo = FakeRepository()
        val sampler = FrameSampler(repo, EvidenceFileStore(temp.root))
        val events = mutableListOf<SamplerEvent>()
        var ticks = 0
        lateinit var job: Job
        job = launch {
            sampler.runSampling(
                sessionId = "session-1",
                config = SamplingConfig(intervalSeconds = 5),
                captureStill = {
                    ticks++
                    CapturedStill(jpeg("tick-$ticks"), Instant.parse("2026-10-27T09:00:0${ticks}.000Z"))
                },
                onEvent = { event ->
                    events += event
                    if (events.size >= 3) job.cancel(CancellationException("test done"))
                },
            )
        }
        try { job.join() } catch (_: CancellationException) { }

        assertThat(events).hasSize(3)
        assertThat(events.all { it is SamplerEvent.FrameStored }).isTrue()
        assertThat(repo.images).hasSize(3)
        assertThat(repo.observations).hasSize(3)
    }

    @Test
    fun `sampling loop survives capture and adopt failures`() = runTest {
        val repo = FakeRepository()
        val sampler = FrameSampler(repo, EvidenceFileStore(temp.root))
        val events = mutableListOf<SamplerEvent>()
        var ticks = 0
        lateinit var job: Job
        job = launch {
            sampler.runSampling(
                sessionId = "session-1",
                config = SamplingConfig(intervalSeconds = 1),
                captureStill = {
                    ticks++
                    when (ticks) {
                        1 -> throw IllegalStateException("camera busy")
                        2 -> CapturedStill(ByteArray(0), Instant.parse("2026-10-27T09:00:02.000Z"))
                        else -> CapturedStill(jpeg("recovered"), Instant.parse("2026-10-27T09:00:03.000Z"))
                    }
                },
                onEvent = { event ->
                    events += event
                    if (events.size >= 3) job.cancel(CancellationException("test done"))
                },
            )
        }
        try { job.join() } catch (_: CancellationException) { }

        assertThat(events).hasSize(3)
        assertThat(events[0]).isInstanceOf(SamplerEvent.FrameMissed::class.java)
        assertThat((events[0] as SamplerEvent.FrameMissed).reason).contains("camera busy")
        assertThat(events[1]).isInstanceOf(SamplerEvent.FrameMissed::class.java)
        assertThat(events[2]).isInstanceOf(SamplerEvent.FrameStored::class.java)
        assertThat(repo.images).hasSize(1)
    }

    @Test
    fun `sampling interval is bounded`() {
        SamplingConfig(intervalSeconds = 1)
        SamplingConfig(intervalSeconds = 300)
        for (bad in listOf(0, -5, 301)) {
            try {
                SamplingConfig(intervalSeconds = bad)
                throw AssertionError("expected rejection of $bad")
            } catch (_: IllegalArgumentException) {
                // Expected.
            }
        }
    }

    @Test
    fun `frame filenames are safe sortable and unique`() {
        val stamp = Instant.parse("2026-10-27T09:00:05.123Z")
        val first = frameFilename(stamp, 1, "aaaaaaaa")
        val second = frameFilename(stamp, 1, "bbbbbbbb")
        assertThat(isSafeFilename(first)).isTrue()
        assertThat(first).isNotEqualTo(second)
        assertThat(first).startsWith("frame-2026-10-27T090005123Z-0001-aaaaaaaa")
        assertThat(frameFilename(stamp.plusSeconds(1), 2, "aaaaaaaa"))
            .isGreaterThan(frameFilename(stamp, 9999, "zzzzzzzz"))
    }
}
