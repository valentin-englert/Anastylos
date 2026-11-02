package org.anastylos.capture.ui.guided

import android.hardware.camera2.CameraMetadata
import androidx.camera.core.CameraSelector
import androidx.camera.view.PreviewView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import com.google.common.truth.Truth.assertThat
import java.io.File
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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
import org.anastylos.capture.camera.UsableCamera
import org.anastylos.capture.camera.AppliedSettings
import org.anastylos.capture.camera.CameraCapabilities
import org.anastylos.capture.camera.CameraSettings
import org.anastylos.capture.camera.DEFAULT_PROFILE
import org.anastylos.capture.camera.ExposureMode
import org.anastylos.capture.data.CameraPrefs
import org.anastylos.capture.data.CameraSettingsStore
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
import org.anastylos.capture.data.GuidedAssociation
import org.anastylos.capture.data.GuidedAssociationRepository
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionState
import org.anastylos.capture.guidance.WireRegistration
import org.anastylos.capture.sync.EphemeralPairingStore
import org.anastylos.capture.sync.GuidedAttemptInput
import org.anastylos.capture.sync.GuidedCapabilities
import org.anastylos.capture.sync.GuidedControlClient
import org.anastylos.capture.sync.GuidedDelivery
import org.anastylos.capture.sync.GuidedDeviceStatus
import org.anastylos.capture.sync.GuidedRemote
import org.anastylos.capture.sync.GuidedRemoteFactory
import org.anastylos.capture.sync.GuidedRemoteSession
import org.anastylos.capture.sync.GuidedSourceEvent
import org.anastylos.capture.sync.GuidedTarget
import org.anastylos.capture.sync.GuidedTokenOutcome
import org.anastylos.capture.sync.GuidedTransform
import org.anastylos.capture.sync.GuidedViewRequest
import org.anastylos.capture.sync.HttpRequest
import org.anastylos.capture.sync.HttpResponse
import org.anastylos.capture.sync.HttpTransport
import org.anastylos.capture.sync.ReceiverClient
import org.anastylos.capture.sync.RemoteAttemptOutcome
import org.anastylos.capture.sync.RemoteConsentOutcome
import org.anastylos.capture.sync.RemoteEventOutcome
import org.anastylos.capture.sync.RemoteSessionState
import org.anastylos.capture.sync.StoredPairing
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
        override var requestedCameraId: String? = null
        var cameras: List<UsableCamera> = emptyList()
        var effectiveId: String? = null
        var note: String? = null
        override val effectiveCameraId: String? get() = effectiveId
        override val cameraNote: String? get() = note
        override fun availableCameras(): List<UsableCamera> = cameras
        override var requestedStillSize: Pair<Int, Int>? = null
        val applied = mutableListOf<Pair<CameraSettings, CameraCapabilities>>()
        override fun applySettings(settings: CameraSettings, caps: CameraCapabilities): Boolean {
            applied += settings to caps
            return applyResult
        }
        var applyResult = true
        var refreshStillCalls = 0
        var refreshStillResult = true
        override suspend fun refreshStillOptions(): Boolean {
            refreshStillCalls++
            return refreshStillResult
        }
        var lastAppliedValue: AppliedSettings? = null
        override val lastApplied: AppliedSettings? get() = lastAppliedValue
        var lastBindSize: Pair<Int, Int>? = null

        var profileId: String = "measured:test-fixture"
        override fun currentIntrinsics(): PoseEstimator.Intrinsics? = PoseEstimator.Intrinsics(
            fx = 800.0, fy = 800.0, cx = 319.5, cy = 239.5,
            width = 640, height = 480,
            distortion = listOf(0.0, 0.0, 0.0, 0.0, 0.0),
            profileId = profileId,
        )

        override fun attachPreview(view: PreviewView?) = Unit
        override fun setBoard(board: PoseEstimator.BoardSpec?) {
            this.lastBoard = board
        }
        var geometry: GuidedCamera.StreamGeometry? = null
        override fun streamGeometry(): GuidedCamera.StreamGeometry? = geometry

        override suspend fun bindAsync(analysisWidth: Int, analysisHeight: Int): Boolean {
            bindCalls++
            lastBindSize = analysisWidth to analysisHeight
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

    private class FakeCameraPrefs : CameraPrefs {
        private val flow = MutableStateFlow<String?>(null)
        override val selectedCameraId: Flow<String?> = flow
        val saved = mutableListOf<String>()
        override suspend fun setSelectedCameraId(id: String) {
            saved += id
            flow.value = id
        }
    }

    private fun cam(id: String) = UsableCamera(
        id, "Back $id", CameraSelector.LENS_FACING_BACK, 5.2f, null, null, false, "LIMITED",
    )

    private fun cameraVm(
        prefs: FakeCameraPrefs,
        store: FakeSettingsStore = FakeSettingsStore(),
    ): GuidanceViewModel {
        val files = EvidenceFileStore(File(temporaryFolder.root, "evidence-cam"), dispatcher)
        val sampler = FrameSampler(repo, files, { nowUtc }, dispatcher)
        return GuidanceViewModel(
            repo = repo, sampler = sampler,
            cameraFactory = { _, _ -> camera },
            prefs = FakePrefs(ref = 60.0),
            nanoClock = { nowNs }, utcClock = { nowUtc }, armLog = {},
            ioDispatcher = dispatcher,
            cameraPrefs = prefs,
            settingsStore = store,
        )
    }

    private class FakeSettingsStore : CameraSettingsStore {
        private val flows = mutableMapOf<String, MutableStateFlow<CameraSettings>>()
        private val profileFlow = MutableStateFlow(DEFAULT_PROFILE)
        val updates = mutableListOf<Triple<String, String, CameraSettings>>()
        override fun settings(cameraId: String, profile: String): Flow<CameraSettings> =
            flows.getOrPut("$cameraId/$profile") { MutableStateFlow(CameraSettings()) }
        override suspend fun update(cameraId: String, profile: String, value: CameraSettings) {
            updates += Triple(cameraId, profile, value)
            flows.getOrPut("$cameraId/$profile") { MutableStateFlow(value) }.value = value
        }
        override val activeProfile: Flow<String> = profileFlow
        override suspend fun setActiveProfile(profile: String) { profileFlow.value = profile }
    }

    private fun fullCaps() = CameraCapabilities(
        aeModes = setOf(CameraMetadata.CONTROL_AE_MODE_OFF, CameraMetadata.CONTROL_AE_MODE_ON),
        isoRange = 50..3200,
        exposureNsRange = 1_000L..100_000_000L,
        maxFrameDurationNs = 200_000_000L,
        evStepsRange = -20..20,
        evStepSize = 0.1f,
        afModes = setOf(CameraMetadata.CONTROL_AF_MODE_OFF, CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE),
        minFocusDistance = 10f,
        awbModes = setOf(1, 2, 3, 5, 6),
        maxDigitalZoom = 8f,
        yuvSizes = listOf(1280 to 720, 960 to 720),
        jpegSizes = listOf(4000 to 3000, 1920 to 1080),
        flashAvailable = true,
        manualSensor = true,
    )

    private fun cappedCam(id: String, caps: CameraCapabilities) = UsableCamera(
        id, "Back $id", CameraSelector.LENS_FACING_BACK, 5.2f, null, null, true, "LIMITED", caps,
    )

    private class FakeRepo : EvidenceRepository {
        val sessions = mutableMapOf<String, CaptureSession>()
        val observations = mutableMapOf<String, ComponentObservation>()
        var nextSession = 0
        var nextObservation = 0

        override fun observeSessions(): Flow<List<CaptureSession>> = flowOf(sessions.values.toList())
        override fun observeSession(id: String): Flow<CaptureSession?> = flowOf(sessions[id])
        override suspend fun getSession(id: String): CaptureSession? = sessions[id]
        override suspend fun ensureSession(id: String, name: String): CaptureSession =
            sessions.getOrPut(id) {
                CaptureSession(
                    sessionId = id, name = name, createdAt = Instant.EPOCH, updatedAt = Instant.EPOCH,
                    assemblyMemorySession = null, trayDefault = null,
                )
            }
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

        private val cursors = mutableMapOf<String, MutableStateFlow<String>>()
        private val boards = mutableMapOf<String, MutableStateFlow<String?>>()
        override fun changeCursor(sessionId: String): Flow<String> =
            cursors.getOrPut(sessionId) { MutableStateFlow("0") }

        override suspend fun setChangeCursor(sessionId: String, cursor: String) {
            (cursors.getOrPut(sessionId) { MutableStateFlow("0") }).value = cursor
        }

        override fun boardFrameFor(boardUuid: String): Flow<String?> =
            boards.getOrPut(boardUuid) { MutableStateFlow(null) }

        override suspend fun setBoardFrame(boardUuid: String, frameId: String) {
            (boards.getOrPut(boardUuid) { MutableStateFlow(null) }).value = frameId
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
            nanoClock = { nowNs }, utcClock = { nowUtc }, armLog = {},
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

    private suspend fun kotlinx.coroutines.test.TestScope.feedStableDwell(
        frames: Int = 15,
        stepMs: Long = 100L,
        center: List<Double> = listOf(198.0, 136.5, -450.0),
    ) {
        // Flush the collector between emits so each estimate is
        // processed with its contemporaneous clock (production
        // processes frames in real time; without this every pose
        // would look stale against the final clock). The clock runs
        // monotonically across feeds: production time never rewinds.
        repeat(frames) { index ->
            val atNs = feedBaseNs + (index + 1) * stepMs * 1_000_000L
            nowNs = atNs + 40_000_000L
            camera.estimateFlow.emit(validEstimate(atNs, center))
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
    fun markersSeenMirrorsDetectorOutput() = runTest(dispatcher) {
        // HARDWARE-03: the UI must distinguish "detector saw
        // nothing" from "saw markers but registration failed".
        armWithTarget()
        nowNs = feedBaseNs + 140_000_000L
        camera.estimateFlow.emit(validEstimate(feedBaseNs + 100_000_000L))
        advanceUntilIdle()
        assertThat(viewModel.state.value.markersSeen).containsExactly(10, 11, 12, 13, 14)
        nowNs += 100_000_000L
        camera.estimateFlow.emit(
            validEstimate(nowNs - 40_000_000L).copy(
                tBC = null, status = WireRegistration.LOST,
                markerIdsSeen = emptyList(), cornerCount = 0,
                rmsPx = null, cornersPx = emptyList(),
            ),
        )
        advanceUntilIdle()
        assertThat(viewModel.state.value.markersSeen).isEmpty()
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
        feedStableDwell(frames = 3)
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
        feedStableDwell(frames = 3)
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

    // ---------- remote Core control ----------

    private class FakeRemoteSession : GuidedRemoteSession {
        val backing = MutableStateFlow(RemoteSessionState())
        override val state: StateFlow<RemoteSessionState> = backing
        var attachedId: String? = null
        var armOutcome: RemoteConsentOutcome =
            RemoteConsentOutcome.Applied(SessionState.ACTIVE, 3, "grant-1")
        var disarmCalls = 0
        val events = mutableListOf<GuidedSourceEvent>()
        var eventOutcome: RemoteEventOutcome = RemoteEventOutcome.Sent(1, 3)
        var attemptOutcome: RemoteAttemptOutcome = RemoteAttemptOutcome.Reserved(
            attemptId = "attempt-1", attemptNumber = 1, remainingExposures = 19, status = "reserved",
        )
        var attemptCalls = 0
        var token: GuidedTokenOutcome = GuidedTokenOutcome.Ready("source-1", "guided-secret")

        override fun attach(sessionId: String) {
            attachedId = sessionId
            backing.value = backing.value.copy(attached = true, sessionId = sessionId)
        }

        override fun detach() {
            attachedId = null
        }

        override fun close() = Unit

        var polling = false
        override fun startPolling() {
            polling = true
        }

        override fun stopPolling() {
            polling = false
        }

        override suspend fun arm(
            cameraId: String,
            maxImages: Int,
            expiresAt: String,
            allowAutoUpload: Boolean,
        ): RemoteConsentOutcome = armOutcome

        override suspend fun disarm(cameraId: String, maxImages: Int, expiresAt: String): RemoteConsentOutcome {
            disarmCalls++
            return RemoteConsentOutcome.Applied(SessionState.CANCELLED, 4, null)
        }

        override suspend fun postEvent(event: GuidedSourceEvent): RemoteEventOutcome {
            events += event
            return eventOutcome
        }

        override suspend fun postStatus(status: GuidedDeviceStatus): RemoteEventOutcome = eventOutcome

        override suspend fun reserveAttempt(input: GuidedAttemptInput): RemoteAttemptOutcome {
            attemptCalls++
            return attemptOutcome
        }

        val published = mutableListOf<GuidedCapabilities>()
        override suspend fun publishCapabilities(caps: GuidedCapabilities): RemoteEventOutcome {
            published += caps
            return eventOutcome
        }

        override suspend fun resolveToken(): GuidedTokenOutcome = token

        override fun buildEvent(
            kind: String,
            requestId: String?,
            requestRevision: Int?,
            attemptId: String?,
            reason: String?,
        ): GuidedSourceEvent = GuidedSourceEvent(
            eventId = "event-${events.size}",
            sessionId = attachedId ?: "",
            sourceId = "source-1",
            requestId = requestId,
            requestRevision = requestRevision,
            kind = kind,
            occurredAt = "2026-10-29T12:01:10Z",
            monotonicNs = "1",
            attemptId = attemptId,
            reason = reason,
        )
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

    private class FakeTransport : HttpTransport {
        val requests = mutableListOf<HttpRequest>()
        var handler: (HttpRequest) -> HttpResponse = { HttpResponse(500, ByteArray(0)) }
        override fun exchange(request: HttpRequest): HttpResponse {
            requests += request
            return handler(request)
        }
    }

    private lateinit var remoteSession: FakeRemoteSession
    private lateinit var remoteAssociations: FakeAssociations
    private lateinit var remoteTransport: FakeTransport
    private lateinit var remotePrefs: FakePrefs
    private lateinit var remoteViewModel: GuidanceViewModel

    private fun buildRemoteViewModel(): GuidanceViewModel {
        val store = EphemeralPairingStore()
        store.save(
            StoredPairing(
                baseUrl = "https://ws:8443", workstationId = "ws-1", token = "legacy-secret",
                pinnedSpkiSha256Hex = "ab".repeat(32),
                guidedSourceId = "source-1", guidedToken = "guided-secret",
                guidedTokenExpiresAt = "2026-11-28T12:01:00Z",
            ),
        )
        remoteSession = FakeRemoteSession()
        remoteAssociations = FakeAssociations()
        remoteTransport = FakeTransport()
        remotePrefs = FakePrefs(ref = 60.0)
        val files = EvidenceFileStore(File(temporaryFolder.root, "evidence-remote"), dispatcher)
        val sampler = FrameSampler(repo, files, { nowUtc }, dispatcher)
        remoteViewModel = GuidanceViewModel(
            repo = repo, sampler = sampler,
            cameraFactory = { _, _ -> camera },
            prefs = remotePrefs,
            nanoClock = { nowNs }, utcClock = { nowUtc }, armLog = {},
            ioDispatcher = dispatcher,
            pairingStore = store,
            remoteFactory = GuidedRemoteFactory { _ ->
                GuidedRemote(
                    remoteSession,
                    GuidedDelivery(
                        GuidedControlClient(remoteTransport, dispatcher),
                        ReceiverClient(remoteTransport, dispatcher),
                        repo, remoteAssociations,
                        readOriginal = { _, _, _ -> null },
                        appVersion = "0.1.0-test",
                        clock = { nowUtc }, uuid = { "fixed-uuid" },
                        sleeper = {}, ioDispatcher = dispatcher,
                    ),
                )
            },
            associations = remoteAssociations,
            appVersion = "0.1.0-test",
            uuid = { remoteUuids.removeFirst() },
        )
        return remoteViewModel
    }

    private val remoteUuids = ArrayDeque(
        listOf(
            "00000000-0000-4000-8000-0000000000a1",
            "00000000-0000-4000-8000-0000000000a2",
            "00000000-0000-4000-8000-0000000000a3",
            "00000000-0000-4000-8000-0000000000a4",
            "00000000-0000-4000-8000-0000000000a5",
            "00000000-0000-4000-8000-0000000000a6",
        ),
    )

    private fun remoteRequest(
        requestId: String = "request-1",
        status: String = "QUEUED",
        revision: Int = 1,
        boardId: String = "board-uuid-1",
    ) = GuidedViewRequest(
        requestId = requestId,
        sessionId = "00000000-0000-4000-8000-000000000011",
        sourceId = "source-1",
        revision = revision,
        operationId = "op-1",
        createdAt = "2026-10-29T12:00:00Z",
        expiresAt = "2026-10-29T12:05:00Z",
        targetLabel = "upper-left",
        boardId = boardId,
        calibrationId = "calibration-1",
        target = GuidedTarget(
            kind = "pose",
            tbc = GuidedTransform(
                from = "board", to = "camera", units = "mm",
                matrix = listOf(
                    1.0, 0.0, 0.0, 198.0,
                    0.0, 1.0, 0.0, 136.5,
                    0.0, 0.0, 1.0, -450.0,
                    0.0, 0.0, 0.0, 1.0,
                ),
            ),
            translationToleranceMm = listOf(20.0, 20.0, 25.0),
            rotationToleranceDeg = 6.0,
        ),
        qualityProfileId = "guidance-hypothesis-1",
        status = status,
        boardRegistrationEpochId = "board-epoch-1",
        objectPoseEpochId = "object-epoch-1",
        reason = "",
    )

    private suspend fun kotlinx.coroutines.test.TestScope.attachRemote(
        sessionId: String = "00000000-0000-4000-8000-000000000011",
    ) {
        remoteViewModel.attachCamera(owner(), null)
        advanceUntilIdle()
        remoteViewModel.setForeground(true)
        remoteViewModel.setPermissionGranted(true)
        remoteViewModel.attachRemote(sessionId)
        advanceUntilIdle()
    }

    @Test
    fun attachRejectsBadUuidAndMissingPairing() = runTest(dispatcher) {
        buildRemoteViewModel()
        remoteViewModel.attachRemote("not-a-uuid")
        advanceUntilIdle()
        assertThat(remoteViewModel.state.value.remoteAttached).isFalse()
        assertThat(remoteViewModel.state.value.remoteError).contains("uuid")
        assertThat(remoteSession.attachedId).isNull()
    }

    @Test
    fun attachPublishesCapabilitiesWithoutInventingACalibration() = runTest(dispatcher) {
        buildRemoteViewModel()
        attachRemote()
        val caps = remoteSession.published.single()
        assertThat(caps.sourceId).isEqualTo("source-1")
        assertThat(caps.targetKinds).containsExactly("pose")
        assertThat(caps.qualityProfileIds).containsExactly("guidance-hypothesis-1", "guidance-hypothesis-2")
        assertThat(caps.protocolVersion).isEqualTo(1)
        // The fake camera has no measured profile, so no calibration id is offered.
        assertThat(caps.calibrationIds).isEmpty()
    }

    @Test
    fun armConsentsThenAdoptsTheMappedRequest() = runTest(dispatcher) {
        buildRemoteViewModel()
        attachRemote()
        remotePrefs.setBoardFrame("board-uuid-1", "surface_a3")
        advanceUntilIdle()

        remoteViewModel.arm()
        advanceUntilIdle()
        assertThat(remoteViewModel.state.value.session).isEqualTo(SessionState.ACTIVE)

        remoteSession.backing.value = remoteSession.backing.value.copy(
            session = SessionState.ACTIVE,
            requests = mapOf("request-1" to remoteRequest()),
            activeRequestId = "request-1",
        )
        advanceUntilIdle()

        val state = remoteViewModel.state.value
        assertThat(state.remoteRequest?.requestId).isEqualTo("request-1")
        assertThat(state.target?.id).isEqualTo("remote-request-1")
        assertThat(state.target?.boardId).isEqualTo("surface_a3")
        assertThat(state.request).isEqualTo(RequestState.ACCEPTED)
        assertThat(remoteSession.events.map { it.kind }).containsAtLeast("armed", "accepted")
    }

    @Test
    fun recordedRemoteRequestAutoShuttersThroughTheGatesAndBindsTheAttempt() = runTest(dispatcher) {
        val wire = javaClass.classLoader!!.getResourceAsStream("remote-view-request-back-shell.json")!!
            .bufferedReader().use { it.readText() }
        val request = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
            .decodeFromString(GuidedViewRequest.serializer(), wire)
        buildRemoteViewModel()
        attachRemote(request.sessionId)
        remotePrefs.setBoardFrame(request.boardId, "surface_a3")
        remoteViewModel.arm()
        advanceUntilIdle()
        remoteSession.backing.value = remoteSession.backing.value.copy(
            session = SessionState.ACTIVE,
            requests = mapOf(request.requestId to request),
            activeRequestId = request.requestId,
        )
        advanceUntilIdle()

        val card = requestCardOf(remoteViewModel.state.value)!!
        assertThat(card.label).isEqualTo("Back shell, inner side up — hands off")
        assertThat(card.outcome).isNull()
        camera.geometry = GuidedCamera.StreamGeometry(1280, 720, 0, 1280, 720)

        feedStableDwell(frames = 15)
        advanceUntilIdle()

        val state = remoteViewModel.state.value
        assertThat(camera.stillsFired).isEqualTo(1)
        assertThat(state.request).isEqualTo(RequestState.CAPTURED_LOCAL)
        assertThat(state.captureResult!!.automatic).isTrue()
        // Core accepts a guided package only when its manifest names the guided session.
        assertThat(repo.getObservation(state.captureResult!!.observationId)!!.sessionId)
            .isEqualTo(request.sessionId)
        val bound = remoteAssociations.rows.getValue(state.captureResult!!.imageId)
        assertThat(bound.requestId).isEqualTo(request.requestId)
        assertThat(bound.requestRevision).isEqualTo(1)
        assertThat(bound.attemptId).isEqualTo("attempt-1")
        assertThat(remoteSession.events.map { it.kind }).containsAtLeast("accepted", "captured_local").inOrder()
        assertThat(requestCardOf(state)!!.hint).isEqualTo("Captured.")
    }

    @Test
    fun workstationRequestShootsWhenTrackedAndStillEvenOffTarget() = runTest(dispatcher) {
        val wire = javaClass.classLoader!!.getResourceAsStream("remote-view-request-back-shell.json")!!
            .bufferedReader().use { it.readText() }
        val recorded = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
            .decodeFromString(GuidedViewRequest.serializer(), wire)
        // Workstation @ d4eec96b: wide tolerance around the part region.
        val request = recorded.copy(
            target = recorded.target.copy(translationToleranceMm = listOf(100.0, 100.0, 150.0), rotationToleranceDeg = 20.0),
        )
        buildRemoteViewModel()
        attachRemote(request.sessionId)
        remotePrefs.setBoardFrame(request.boardId, "surface_a3")
        remoteViewModel.arm()
        advanceUntilIdle()
        remoteSession.backing.value = remoteSession.backing.value.copy(
            session = SessionState.ACTIVE,
            requests = mapOf(request.requestId to request),
            activeRequestId = request.requestId,
        )
        advanceUntilIdle()
        val target = remoteViewModel.state.value.target!!
        assertThat(target.posTolMm).isEqualTo(org.anastylos.capture.guidance.Vec3(100.0, 100.0, 150.0))
        assertThat(target.angTolDeg).isEqualTo(20.0)
        camera.geometry = GuidedCamera.StreamGeometry(1280, 720, 0, 1280, 720)

        // 250 mm beside the target: outside even the wide tolerance.
        feedStableDwell(frames = 15, center = listOf(448.0, 136.5, -450.0))
        advanceUntilIdle()

        assertThat(remoteViewModel.state.value.cues).isNotEmpty()
        assertThat(camera.stillsFired).isEqualTo(1)
        assertThat(remoteViewModel.state.value.request).isEqualTo(RequestState.CAPTURED_LOCAL)
    }

    @Test
    fun unmappedBoardBlocksAdoptionUntilMapped() = runTest(dispatcher) {
        buildRemoteViewModel()
        attachRemote()
        remoteViewModel.arm()
        advanceUntilIdle()

        remoteSession.backing.value = remoteSession.backing.value.copy(
            session = SessionState.ACTIVE,
            requests = mapOf("request-1" to remoteRequest()),
            activeRequestId = "request-1",
        )
        advanceUntilIdle()

        assertThat(remoteViewModel.state.value.remoteRequest).isNull()
        assertThat(remoteViewModel.state.value.remoteError).contains("map it")

        remoteViewModel.selectRemoteBoard("surface_a3")
        advanceUntilIdle()

        assertThat(remoteViewModel.state.value.remoteRequest?.requestId).isEqualTo("request-1")
        assertThat(remoteViewModel.state.value.request).isEqualTo(RequestState.ACCEPTED)
    }

    @Test
    fun remoteCancelMirrorsIntoTheLocalRequest() = runTest(dispatcher) {
        buildRemoteViewModel()
        attachRemote()
        remotePrefs.setBoardFrame("board-uuid-1", "surface_a3")
        remoteViewModel.arm()
        advanceUntilIdle()
        remoteSession.backing.value = remoteSession.backing.value.copy(
            session = SessionState.ACTIVE,
            requests = mapOf("request-1" to remoteRequest()),
            activeRequestId = "request-1",
        )
        advanceUntilIdle()
        assertThat(remoteViewModel.state.value.request).isEqualTo(RequestState.ACCEPTED)

        remoteSession.backing.value = remoteSession.backing.value.copy(
            requests = mapOf("request-1" to remoteRequest(status = "CANCELLED", revision = 2)),
        )
        advanceUntilIdle()

        assertThat(remoteViewModel.state.value.request).isEqualTo(RequestState.CANCELLED)
    }

    @Test
    fun disarmPostsRemoteDisarmAndEvent() = runTest(dispatcher) {
        buildRemoteViewModel()
        attachRemote()
        remoteViewModel.arm()
        advanceUntilIdle()
        assertThat(remoteViewModel.state.value.session).isEqualTo(SessionState.ACTIVE)

        remoteViewModel.disarm()
        advanceUntilIdle()

        assertThat(remoteSession.disarmCalls).isEqualTo(1)
        assertThat(remoteSession.events.map { it.kind }).contains("disarmed")
    }

    @Test
    fun shutterHoldsWithoutAReservedAttempt() = runTest(dispatcher) {
        buildRemoteViewModel()
        attachRemote()
        remotePrefs.setBoardFrame("board-uuid-1", "surface_a3")
        remoteViewModel.arm()
        advanceUntilIdle()
        remoteSession.backing.value = remoteSession.backing.value.copy(
            session = SessionState.ACTIVE,
            requests = mapOf("request-1" to remoteRequest()),
            activeRequestId = "request-1",
        )
        advanceUntilIdle()
        remoteSession.attemptOutcome = RemoteAttemptOutcome.Failed("Core busy")
        camera.geometry = GuidedCamera.StreamGeometry(1280, 720, 0, 1280, 720)

        feedStableDwell(frames = 15)
        advanceUntilIdle()

        assertThat(remoteViewModel.state.value.request).isEqualTo(RequestState.HOLD_STILL)
        assertThat(remoteViewModel.state.value.attemptReady).isFalse()
        assertThat(remoteSession.attemptCalls).isEqualTo(1)
        // No shutter without the attempt: still idle, nothing captured.
        assertThat(camera.stillsFired).isEqualTo(0)
        assertThat(remoteViewModel.state.value.captureResult).isNull()

        remoteSession.attemptOutcome = RemoteAttemptOutcome.Reserved(
            attemptId = "attempt-1", attemptNumber = 1, remainingExposures = 19, status = "reserved",
        )
        remoteViewModel.retryAttempt()
        advanceUntilIdle()
        assertThat(remoteViewModel.state.value.attemptReady).isTrue()

        feedStableDwell(frames = 3)
        advanceUntilIdle()
        assertThat(camera.stillsFired).isEqualTo(1)
        assertThat(remoteViewModel.state.value.captureResult).isNotNull()
    }

    @Test
    fun selectCameraPersistsAndRebindsOnNewSensor() = runTest {
        val prefs = FakeCameraPrefs()
        val vm = cameraVm(prefs)
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        assertThat(camera.bindCalls).isEqualTo(1)
        camera.cameras = listOf(cam("0"), cam("2"))
        vm.selectCamera("2")
        advanceUntilIdle()
        assertThat(prefs.saved).containsExactly("2")
        assertThat(vm.state.value.selectedCameraId).isEqualTo("2")
        assertThat(camera.requestedCameraId).isEqualTo("2")
        assertThat(camera.bindCalls).isEqualTo(2)
        assertThat(camera.unbindCalls).isEqualTo(1)
    }

    @Test
    fun attachSurfacesInventoryAndFallbackNote() = runTest {
        val prefs = FakeCameraPrefs()
        val vm = cameraVm(prefs)
        camera.cameras = listOf(cam("0"))
        camera.effectiveId = "0"
        camera.note = "Camera 9 unavailable, using 0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        assertThat(vm.state.value.availableCameras).hasSize(1)
        assertThat(vm.state.value.effectiveCameraId).isEqualTo("0")
        assertThat(vm.state.value.cameraNote).contains("unavailable")
    }

    @Test
    fun updateSettingsPersistsAndApplies() = runTest {
        val store = FakeSettingsStore()
        val vm = cameraVm(FakeCameraPrefs(), store)
        camera.cameras = listOf(cappedCam("0", fullCaps()))
        camera.effectiveId = "0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        camera.applied.clear()
        vm.updateSettings { it.copy(exposureMode = ExposureMode.MANUAL, iso = 800) }
        advanceUntilIdle()
        assertThat(store.updates).hasSize(1)
        assertThat(store.updates[0].third.iso).isEqualTo(800)
        // Direct apply plus the store echo both fire with identical values (idempotent).
        assertThat(camera.applied).isNotEmpty()
        assertThat(camera.applied.last().first.exposureMode).isEqualTo(ExposureMode.MANUAL)
        assertThat(camera.applied.last().first.iso).isEqualTo(800)
        assertThat(vm.state.value.settingsNote).isNull()
    }

    @Test
    fun updateSettingsRefreshesStillOptions() = runTest {
        val vm = cameraVm(FakeCameraPrefs())
        camera.cameras = listOf(cappedCam("0", fullCaps()))
        camera.effectiveId = "0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        camera.refreshStillCalls = 0
        vm.updateSettings { it.copy(iso = 800) }
        advanceUntilIdle()
        assertThat(camera.refreshStillCalls).isAtLeast(1)
    }

    @Test
    fun stillRefreshFailureSurfacesNote() = runTest {
        val vm = cameraVm(FakeCameraPrefs())
        camera.cameras = listOf(cappedCam("0", fullCaps()))
        camera.effectiveId = "0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        camera.refreshStillResult = false
        vm.updateSettings { it.copy(iso = 800) }
        advanceUntilIdle()
        assertThat(vm.state.value.settingsNote).isEqualTo("still capture options not refreshed")
    }

    @Test
    fun unsupportedManualFallsBackToAuto() = runTest {
        val vm = cameraVm(FakeCameraPrefs())
        camera.cameras = listOf(cappedCam("0", CameraCapabilities()))
        camera.effectiveId = "0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        camera.applied.clear()
        vm.updateSettings {
            it.copy(exposureMode = ExposureMode.MANUAL, torch = true)
        }
        advanceUntilIdle()
        val applied = camera.applied.last().first
        assertThat(applied.exposureMode).isEqualTo(ExposureMode.AUTO)
        assertThat(applied.torch).isFalse()
    }

    @Test
    fun analysisSizeChangeRebindsWithNewSize() = runTest {
        val vm = cameraVm(FakeCameraPrefs())
        camera.cameras = listOf(cappedCam("0", fullCaps()))
        camera.effectiveId = "0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        assertThat(camera.lastBindSize).isEqualTo(1280 to 720)
        vm.updateSettings { it.copy(analysisSize = 960 to 720) }
        advanceUntilIdle()
        assertThat(camera.bindCalls).isEqualTo(2)
        assertThat(camera.lastBindSize).isEqualTo(960 to 720)
    }

    @Test
    fun storedAnalysisSizeMismatchRebindsAfterColdStart() = runTest {
        val caps = fullCaps().copy(yuvSizes = listOf(1920 to 1080, 1280 to 720, 960 to 720))
        val store = FakeSettingsStore()
        store.update("0", DEFAULT_PROFILE, CameraSettings(analysisSize = 1920 to 1080))
        val vm = cameraVm(FakeCameraPrefs(), store)
        camera.cameras = listOf(cappedCam("0", caps))
        camera.effectiveId = "0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        assertThat(camera.bindCalls).isEqualTo(2)
        assertThat(camera.lastBindSize).isEqualTo(1920 to 1080)
        assertThat(vm.state.value.settings.analysisSize).isEqualTo(1920 to 1080)
    }

    @Test
    fun storedMatchingSizeBindsOnce() = runTest {
        val vm = cameraVm(FakeCameraPrefs(), FakeSettingsStore())
        camera.cameras = listOf(cappedCam("0", fullCaps()))
        camera.effectiveId = "0"
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        assertThat(camera.bindCalls).isEqualTo(1)
        assertThat(camera.lastBindSize).isEqualTo(1280 to 720)
    }

    @Test
    fun hackathonMotionJitterStillFiresOnce() = runTest(dispatcher) {
        armWithTarget()
        repeat(12) { index ->
            val atNs = (index + 1) * 80L * 1_000_000L
            nowNs = atNs + 40_000_000L
            val jumped = validEstimate(atNs).let { estimate ->
                estimate.copy(
                    cornersPx = estimate.cornersPx.map { marker ->
                        marker.copy(
                            corners = marker.corners.map { listOf(it[0] + index * 25.0, it[1]) },
                        )
                    },
                )
            }
            camera.estimateFlow.emit(jumped)
            advanceUntilIdle()
        }
        assertThat(viewModel.state.value.motionWarning).isTrue()
        assertThat(camera.stillsFired).isEqualTo(1)
    }

    @Test
    fun hackathonStaleFrameDoesNotWipeDwell() = runTest(dispatcher) {
        armWithTarget()
        // Five fresh samples span 320 ms: short of the 500 ms dwell.
        repeat(5) { index ->
            val atNs = index * 80L * 1_000_000L
            nowNs = atNs + 40_000_000L
            camera.estimateFlow.emit(validEstimate(atNs))
            advanceUntilIdle()
        }
        assertThat(camera.stillsFired).isEqualTo(0)
        // Late publication of an in-between frame. It must not count and must not reset.
        val staleNs = 360L * 1_000_000L
        nowNs = staleNs + 400_000_000L
        camera.estimateFlow.emit(validEstimate(staleNs))
        advanceUntilIdle()
        assertThat(camera.stillsFired).isEqualTo(0)
        repeat(8) { index ->
            val atNs = (400L + index * 80L) * 1_000_000L
            nowNs = atNs + 40_000_000L
            camera.estimateFlow.emit(validEstimate(atNs))
            advanceUntilIdle()
        }
        assertThat(camera.stillsFired).isEqualTo(1)
    }

    @Test
    fun hackathonOffTargetDoesNotFire() = runTest(dispatcher) {
        armWithTarget()
        repeat(12) { index ->
            val atNs = (index + 1) * 80L * 1_000_000L
            nowNs = atNs + 40_000_000L
            camera.estimateFlow.emit(validEstimate(atNs, center = listOf(0.0, 0.0, -200.0)))
            advanceUntilIdle()
        }
        assertThat(camera.stillsFired).isEqualTo(0)
    }

    @Test
    fun hackathonUncalibratedProfileDoesNotAutoFire() = runTest(dispatcher) {
        camera.profileId = "factory-test-uncalibrated"
        armWithTarget()
        repeat(12) { index ->
            val atNs = (index + 1) * 80L * 1_000_000L
            nowNs = atNs + 40_000_000L
            camera.estimateFlow.emit(validEstimate(atNs))
            advanceUntilIdle()
        }
        assertThat(viewModel.state.value.uncalibrated).isTrue()
        assertThat(camera.stillsFired).isEqualTo(0)
    }

    @Test
    fun appliedSampleSurfacesAsLine() = runTest {
        val vm = cameraVm(FakeCameraPrefs())
        vm.attachCamera(owner(), null)
        advanceUntilIdle()
        camera.lastAppliedValue = AppliedSettings(20_000_000L, 400, null, 1, 4, 1, 0)
        camera.estimateFlow.emit(validEstimate(feedBaseNs + 100_000_000L))
        advanceUntilIdle()
        assertThat(vm.state.value.appliedLine).contains("ISO400")
        assertThat(vm.state.value.appliedLine).contains("1/50")
    }
}
