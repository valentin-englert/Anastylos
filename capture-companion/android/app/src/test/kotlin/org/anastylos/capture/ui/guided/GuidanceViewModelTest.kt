package org.anastylos.capture.ui.guided

import androidx.camera.view.PreviewView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import com.google.common.truth.Truth.assertThat
import java.io.File
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.anastylos.capture.camera.CapturedStill
import org.anastylos.capture.camera.FrameSampler
import org.anastylos.capture.camera.GuidedCamera
import org.anastylos.capture.camera.GuidedCameraSource
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.GuidedPrefs
import org.anastylos.capture.data.files.EvidenceFileStore
import org.anastylos.capture.domain.CaptureSession
import org.anastylos.capture.domain.CaptureSource
import org.anastylos.capture.domain.ComponentObservation
import org.anastylos.capture.domain.ConfirmedField
import org.anastylos.capture.domain.EpistemicState
import org.anastylos.capture.domain.EvidenceImage
import org.anastylos.capture.domain.ReviewState
import org.anastylos.capture.guidance.DwellState
import org.anastylos.capture.guidance.FrameQuality
import org.anastylos.capture.guidance.PoseEstimator
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionState
import org.anastylos.capture.guidance.WireRegistration
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * Guidance flow on the JVM: fake camera/estimator stream, real
 * reducers/dwell/gates, real sampler + file store. Covers arm,
 * navigate→align→hold→fire, loss/cancel holds, duplicate
 * suppression and the manual fallback.
 */
class GuidanceViewModelTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    private val dispatcher = StandardTestDispatcher()
    private var nowNs = 0L
    private var nowUtc = Instant.parse("2026-10-29T12:00:00Z")

    private class FakeCamera : GuidedCamera {
        val estimateFlow = MutableSharedFlow<PoseEstimator.Estimate>(extraBufferCapacity = 128)
        override val estimates: SharedFlow<PoseEstimator.Estimate> = estimateFlow
        var bound = false
        var bindCalls = 0
        var unbindCalls = 0
        var lastBoard: PoseEstimator.BoardSpec? = null
        var stillsFired = 0
        var stillBytes = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xD9.toByte())

        override fun currentIntrinsics(): PoseEstimator.Intrinsics? = PoseEstimator.Intrinsics(
            fx = 800.0, fy = 800.0, cx = 319.5, cy = 239.5,
            width = 640, height = 480,
            distortion = listOf(0.0, 0.0, 0.0, 0.0, 0.0),
            profileId = "factory-test-uncalibrated",
        )

        override fun attachPreview(view: PreviewView?) = Unit
        override fun setBoard(board: PoseEstimator.BoardSpec?) {
            this.lastBoard = board
        }
        var geometry: GuidedCamera.StreamGeometry? = null
        override fun streamGeometry(): GuidedCamera.StreamGeometry? = geometry

        override suspend fun bindAsync(analysisWidth: Int, analysisHeight: Int): Boolean {
            bindCalls++
            bound = true
            return true
        }

        override fun unbind() {
            unbindCalls++
            bound = false
        }

        override suspend fun takeGuidedStill(): GuidedCameraSource.GuidedStill {
            stillsFired++
            return GuidedCameraSource.GuidedStill(
                bytes = stillBytes, capturedAt = Instant.parse("2026-10-29T12:00:01Z"),
                nearestEstimateMonotonicNs = 100L,
                associationNote = "test still; spatial sidecar unqualified",
            )
        }
    }

    private class FakeRepo : EvidenceRepository {
        val sessions = mutableMapOf<String, CaptureSession>()
        val observations = mutableMapOf<String, ComponentObservation>()
        var nextSession = 0
        var nextObservation = 0

        override fun observeSessions(): Flow<List<CaptureSession>> = flowOf(sessions.values.toList())
        override fun observeSession(id: String): Flow<CaptureSession?> = flowOf(sessions[id])
        override suspend fun getSession(id: String): CaptureSession? = sessions[id]
        override suspend fun createSession(name: String): CaptureSession {
            val session = CaptureSession(
                sessionId = "session-${++nextSession}", name = name,
                createdAt = Instant.EPOCH, updatedAt = Instant.EPOCH,
                assemblyMemorySession = null, trayDefault = null,
            )
            sessions[session.sessionId] = session
            return session
        }

        override suspend fun renameSession(id: String, name: String) = Unit
        override suspend fun deleteSession(id: String) {
            sessions.remove(id)
        }

        override fun observeObservations(sessionId: String): Flow<List<ComponentObservation>> =
            flowOf(observations.values.filter { it.sessionId == sessionId })

        override fun observeObservation(id: String): Flow<ComponentObservation?> = flowOf(observations[id])
        override suspend fun getObservation(id: String): ComponentObservation? = observations[id]
        override suspend fun getAllObservations(): List<ComponentObservation> = observations.values.toList()

        override suspend fun createObservation(sessionId: String, source: CaptureSource): ComponentObservation {
            val obs = ComponentObservation(
                observationId = "obs-${++nextObservation}", sessionId = sessionId,
                componentReference = null, trayReference = null,
                capturedAt = Instant.EPOCH, captureSource = source,
                reviewState = ReviewState.UNREVIEWED, operatorNotes = null,
                markings = emptyList(), category = ConfirmedField(null, EpistemicState.UNKNOWN),
                measurements = emptyList(), quantity = ConfirmedField(null, EpistemicState.UNKNOWN),
                candidates = emptyList(), appVersion = "test", userAgent = null,
                exportHistory = emptyList(), images = emptyList(),
            )
            observations[obs.observationId] = obs
            return obs
        }

        override suspend fun saveObservation(obs: ComponentObservation) {
            observations[obs.observationId] = obs
        }

        override suspend fun deleteObservation(id: String) {
            observations.remove(id)
        }

        override suspend fun recordImage(image: EvidenceImage) {
            val obs = observations[image.observationId] ?: return
            observations[image.observationId] = obs.copy(images = obs.images + image)
        }

        override suspend fun markSynced(observationIds: List<String>, packageId: String, exportedAt: Instant) = Unit
    }

    private class FakePrefs(ref: Double? = null) : GuidedPrefs {
        private val flow = MutableStateFlow(ref)
        override val sharpnessReference: Flow<Double?> = flow
        override suspend fun setSharpnessReference(value: Double) {
            flow.value = value
        }

        override suspend fun clearSharpnessReference() {
            flow.value = null
        }
    }

    private fun owner(): LifecycleOwner = object : LifecycleOwner {
        private val registry = LifecycleRegistry(this)
        override val lifecycle: Lifecycle get() = registry
    }

    private lateinit var camera: FakeCamera
    private lateinit var repo: FakeRepo
    private lateinit var viewModel: GuidanceViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        camera = FakeCamera()
        repo = FakeRepo()
        val files = EvidenceFileStore(File(temporaryFolder.root, "evidence"), dispatcher)
        val sampler = FrameSampler(repo, files, { nowUtc }, dispatcher)
        viewModel = GuidanceViewModel(
            repo = repo, sampler = sampler,
            cameraFactory = { _, _ -> camera },
            prefs = FakePrefs(ref = 60.0),
            nanoClock = { nowNs }, utcClock = { nowUtc },
            ioDispatcher = dispatcher,
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun validEstimate(
        atNs: Long,
        center: List<Double> = listOf(198.0, 136.5, -450.0),
    ): PoseEstimator.Estimate {
        val corners = (10..14).map { id ->
            PoseEstimator.MarkerCorners(
                id,
                listOf(
                    listOf(100.0 + id, 100.0), listOf(140.0 + id, 100.0),
                    listOf(140.0 + id, 140.0), listOf(100.0 + id, 140.0),
                ),
            )
        }
        return PoseEstimator.Estimate(
            tBC = listOf(
                1.0, 0.0, 0.0, center[0],
                0.0, 1.0, 0.0, center[1],
                0.0, 0.0, 1.0, center[2],
                0.0, 0.0, 0.0, 1.0,
            ),
            markerIdsSeen = (10..14).toList(), cornerCount = 20,
            rmsPx = 0.3, status = WireRegistration.VALID,
            sensorTimestampNs = atNs, monotonicNs = atNs,
            cornersPx = corners,
            frameQuality = FrameQuality.Measurement(
                darkFraction = 0.01, saturatedFraction = 0.01, sharpness = 50.0,
            ),
        )
    }

    private var feedBaseNs = 0L

    private suspend fun kotlinx.coroutines.test.TestScope.feedStableDwell(frames: Int = 15, stepMs: Long = 100L) {
        // Flush the collector between emits so each estimate is
        // processed with its contemporaneous clock (production
        // processes frames in real time; without this every pose
        // would look stale against the final clock). The clock runs
        // monotonically across feeds: production time never rewinds.
        repeat(frames) { index ->
            val atNs = feedBaseNs + (index + 1) * stepMs * 1_000_000L
            nowNs = atNs + 40_000_000L
            camera.estimateFlow.emit(validEstimate(atNs))
            advanceUntilIdle()
        }
        feedBaseNs += frames * stepMs * 1_000_000L
    }

    private suspend fun kotlinx.coroutines.test.TestScope.armWithTarget() {
        viewModel.attachCamera(owner(), null)
        advanceUntilIdle()
        viewModel.setForeground(true)
        viewModel.setPermissionGranted(true)
        viewModel.selectTarget(LOCAL_TARGETS[0])
        viewModel.arm()
        advanceUntilIdle()
    }

    @Test
    fun armRequiresPermissionAndForeground() = runTest(dispatcher) {
        viewModel.attachCamera(owner(), null)
        advanceUntilIdle()
        viewModel.arm()
        advanceUntilIdle()
        assertThat(viewModel.state.value.session).isEqualTo(SessionState.DISARMED)
        viewModel.setForeground(true)
        viewModel.setPermissionGranted(true)
        viewModel.arm()
        advanceUntilIdle()
        assertThat(viewModel.state.value.session).isEqualTo(SessionState.ACTIVE)
        assertThat(viewModel.state.value.request).isEqualTo(RequestState.ACCEPTED)
    }

    @Test
    fun fullFlowFiresOnceAndPersists() = runTest(dispatcher) {
        armWithTarget()
        feedStableDwell()
        advanceUntilIdle()
        val state = viewModel.state.value
        assertThat(state.registration).isEqualTo(RegistrationState.ACQUIRED)
        assertThat(state.request).isEqualTo(RequestState.CAPTURED_LOCAL)
        assertThat(state.dwell).isEqualTo(DwellState.Ready)
        assertThat(camera.stillsFired).isEqualTo(1)
        val result = state.captureResult!!
        assertThat(result.automatic).isTrue()
        assertThat(result.sha256).hasLength(64)
        // Bytes + hash persisted through the sampler.
        val obs = repo.getObservation(result.observationId)!!
        assertThat(obs.images).hasSize(1)
        assertThat(obs.images[0].sha256).isEqualTo(result.sha256)
        // A second dwell window must not double-fire for the same arming.
        feedStableDwell()
        advanceUntilIdle()
        assertThat(camera.stillsFired).isEqualTo(1)
    }

    @Test
    fun trackingLossDuringHoldAbortsWithoutFiring() = runTest(dispatcher) {
        armWithTarget()
        feedStableDwell(frames = 6)
        advanceUntilIdle()
        assertThat(viewModel.state.value.request).isEqualTo(RequestState.HOLD_STILL)
        // Markers vanish mid-hold.
        nowNs += 100_000_000L
        camera.estimateFlow.emit(
            validEstimate(nowNs - 40_000_000L).copy(
                tBC = null, status = WireRegistration.LOST,
                markerIdsSeen = emptyList(), cornerCount = 0,
                rmsPx = null, cornersPx = emptyList(),
            ),
        )
        advanceUntilIdle()
        val state = viewModel.state.value
        assertThat(state.registration).isEqualTo(RegistrationState.LOST)
        assertThat(state.request).isEqualTo(RequestState.ALIGNING)
        assertThat(camera.stillsFired).isEqualTo(0)
    }

    @Test
    fun cancelDuringDwellHoldsWithoutFiring() = runTest(dispatcher) {
        armWithTarget()
        feedStableDwell(frames = 6)
        advanceUntilIdle()
        viewModel.cancelRequest()
        feedStableDwell(frames = 15)
        advanceUntilIdle()
        val state = viewModel.state.value
        assertThat(state.request).isEqualTo(RequestState.CANCELLED)
        assertThat(camera.stillsFired).isEqualTo(0)
        assertThat(state.captureResult).isNull()
    }

    @Test
    fun disarmAndRearmStartsFresh() = runTest(dispatcher) {
        armWithTarget()
        feedStableDwell()
        advanceUntilIdle()
        assertThat(camera.stillsFired).isEqualTo(1)
        // Explicit disarm ends the session (CANCELLED is terminal);
        // starting over needs a fresh session lifecycle.
        viewModel.disarm()
        advanceUntilIdle()
        assertThat(viewModel.state.value.session).isEqualTo(SessionState.CANCELLED)
        viewModel.arm()
        advanceUntilIdle()
        assertThat(viewModel.state.value.session).isEqualTo(SessionState.CANCELLED)
        viewModel.newSession()
        viewModel.arm()
        advanceUntilIdle()
        assertThat(viewModel.state.value.session).isEqualTo(SessionState.ACTIVE)
        assertThat(viewModel.state.value.request).isEqualTo(RequestState.ACCEPTED)
        feedStableDwell()
        advanceUntilIdle()
        assertThat(camera.stillsFired).isEqualTo(2)
        assertThat(viewModel.state.value.request).isEqualTo(RequestState.CAPTURED_LOCAL)
    }

    @Test
    fun secondRequestInsideLiveSession() = runTest(dispatcher) {
        armWithTarget()
        feedStableDwell()
        advanceUntilIdle()
        assertThat(camera.stillsFired).isEqualTo(1)
        // Session stays ACTIVE; a second request (new token) may fire
        // again: two consecutive captures, one shutter each.
        viewModel.arm()
        advanceUntilIdle()
        assertThat(viewModel.state.value.request).isEqualTo(RequestState.ACCEPTED)
        feedStableDwell()
        advanceUntilIdle()
        assertThat(camera.stillsFired).isEqualTo(2)
        assertThat(viewModel.state.value.request).isEqualTo(RequestState.CAPTURED_LOCAL)
    }

    @Test
    fun manualCaptureAlwaysAvailableWhenBound() = runTest(dispatcher) {
        viewModel.attachCamera(owner(), null)
        advanceUntilIdle()
        viewModel.setForeground(true)
        viewModel.setPermissionGranted(true)
        viewModel.captureManual()
        advanceUntilIdle()
        val state = viewModel.state.value
        assertThat(camera.stillsFired).isEqualTo(1)
        assertThat(state.captureResult!!.automatic).isFalse()
    }

    @Test
    fun detachDisarmsAndUnbinds() = runTest(dispatcher) {
        armWithTarget()
        viewModel.detachCamera()
        advanceUntilIdle()
        assertThat(viewModel.state.value.session).isEqualTo(SessionState.DISARMED)
        assertThat(viewModel.state.value.cameraBound).isFalse()
        assertThat(camera.unbindCalls).isEqualTo(1)
    }

    @Test
    fun boardSwitchResetsDwell() = runTest(dispatcher) {
        armWithTarget()
        feedStableDwell(frames = 6)
        advanceUntilIdle()
        viewModel.selectBoard("surface_a4")
        advanceUntilIdle()
        val state = viewModel.state.value
        assertThat(state.boardId).isEqualTo("surface_a4")
        assertThat(state.dwell).isEqualTo(DwellState.Idle)
        assertThat(camera.lastBoard!!.boardId).isEqualTo("surface_a4")
    }
}
