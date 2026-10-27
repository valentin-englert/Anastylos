package org.anastylos.capture.data

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.anastylos.capture.data.local.CaptureDatabase
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ImageRole
import org.anastylos.capture.domain.Marking
import org.anastylos.capture.domain.Measurement
import org.anastylos.capture.domain.MeasurementProvenance
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class EvidenceRepositoryTest {
    private lateinit var db: CaptureDatabase
    private lateinit var deletedFiles: MutableList<String>
    private lateinit var repository: EvidenceRepository

    private val stamp = Instant.parse("2026-01-15T10:30:00Z")

    @Before
    fun setUp() {
        db = CaptureDatabase.openInMemory(RuntimeEnvironment.getApplication())
        deletedFiles = mutableListOf()
        val port = object : EvidenceFileStorePort {
            override fun deleteObservationFiles(observationId: String) {
                deletedFiles += observationId
            }
        }
        repository = RoomEvidenceRepository(db, port, clock = { stamp }, appVersion = "0.1.0-test")
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun `sessions round-trip with newest first`() = runTest {
        val first = repository.createSession("First")
        repository.createSession("Second")
        val sessions = repository.observeSessions().first()
        assertThat(sessions.map { it.name }).containsExactly("First", "Second")
        assertThat(repository.getSession(first.sessionId)?.name).isEqualTo("First")
    }

    @Test
    fun `blank session names are refused`() = runTest {
        try {
            repository.createSession("   ")
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
            // Expected.
        }
        assertThat(repository.observeSessions().first()).isEmpty()
    }

    @Test
    fun `rename updates name and timestamp`() = runTest {
        val session = repository.createSession("Old")
        repository.renameSession(session.sessionId, "New")
        assertThat(repository.getSession(session.sessionId)?.name).isEqualTo("New")
    }

    @Test
    fun `observations persist with images joined and JSON intact`() = runTest {
        val session = repository.createSession("S")
        val created = repository.createObservation(session.sessionId, CaptureSource.CAMERA)
        val edited = created.copy(
            componentReference = "R12",
            markings = listOf(Marking("10k")),
            measurements = listOf(Measurement("length", 12.0, "mm", MeasurementProvenance.OPERATOR_MEASURED)),
            quantity = ConfirmedField(4L, EpistemicState.CONFIRMED),
        )
        repository.saveObservation(edited)
        repository.recordImage(
            EvidenceImage(
                imageId = "33333333-3333-4333-8333-333333333333",
                observationId = created.observationId,
                role = ImageRole.FRONT,
                filename = "front.jpg",
                mime = "image/jpeg",
                byteLength = 259,
                sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                capturedAt = stamp,
                crop = null,
            ),
        )

        val loaded = repository.getObservation(created.observationId)!!
        assertThat(loaded.componentReference).isEqualTo("R12")
        assertThat(loaded.markings).containsExactly(Marking("10k"))
        assertThat(loaded.measurements.single().unit).isEqualTo("mm")
        assertThat(loaded.quantity.state).isEqualTo(EpistemicState.CONFIRMED)
        assertThat(loaded.quantity.value).isEqualTo(4L)
        assertThat(loaded.images).hasSize(1)

        val listed = repository.observeObservations(session.sessionId).first()
        assertThat(listed.map { it.observationId }).containsExactly(created.observationId)
    }

    @Test
    fun `invalid observations are refused before any write`() = runTest {
        val session = repository.createSession("S")
        val created = repository.createObservation(session.sessionId, CaptureSource.CAMERA)
        val bad = created.copy(quantity = ConfirmedField(4L, EpistemicState.UNKNOWN))
        try {
            repository.saveObservation(bad)
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
            // Expected.
        }
        assertThat(repository.getObservation(created.observationId)?.quantity?.value).isNull()
    }

    @Test
    fun `images for unknown observations are refused`() = runTest {
        try {
            repository.recordImage(
                EvidenceImage(
                    imageId = "33333333-3333-4333-8333-333333333333",
                    observationId = "99999999-9999-4999-8999-999999999999",
                    role = ImageRole.FRONT,
                    filename = "front.jpg",
                    mime = "image/jpeg",
                    byteLength = 10,
                    sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    capturedAt = stamp,
                    crop = null,
                ),
            )
            throw AssertionError("expected IllegalArgumentException")
        } catch (_: IllegalArgumentException) {
            // Expected.
        }
    }

    @Test
    fun `markSynced appends once per package`() = runTest {
        val session = repository.createSession("S")
        val obs = repository.createObservation(session.sessionId, CaptureSource.CAMERA)
        repository.markSynced(listOf(obs.observationId), "pkg-1", stamp)
        repository.markSynced(listOf(obs.observationId), "pkg-1", stamp)
        val history = repository.getObservation(obs.observationId)!!.exportHistory
        assertThat(history).hasSize(1)
        assertThat(history.single().packageId).isEqualTo("pkg-1")
    }

    @Test
    fun `deleting a session removes rows first and files after`() = runTest {
        val session = repository.createSession("S")
        val obs = repository.createObservation(session.sessionId, CaptureSource.CAMERA)
        repository.deleteSession(session.sessionId)
        assertThat(repository.getSession(session.sessionId)).isNull()
        assertThat(repository.getObservation(obs.observationId)).isNull()
        assertThat(deletedFiles).containsExactly(obs.observationId)
    }
}
