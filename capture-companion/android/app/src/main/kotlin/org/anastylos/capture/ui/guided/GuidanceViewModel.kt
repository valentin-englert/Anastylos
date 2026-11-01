package org.anastylos.capture.ui.guided

import android.graphics.BitmapFactory
import android.media.ExifInterface
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.view.PreviewView
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.serialization.json.Json
import org.anastylos.capture.domain.sha256Hex
import org.anastylos.capture.domain.toIsoUtc
import org.anastylos.capture.sync.framePackageId
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.anastylos.capture.camera.CapturedStill
import org.anastylos.capture.camera.FrameSampler
import org.anastylos.capture.camera.SamplerEvent
import org.anastylos.capture.camera.GuidedCamera
import org.anastylos.capture.camera.UsableCamera
import org.anastylos.capture.camera.AppliedSettings
import org.anastylos.capture.camera.CameraCapabilities
import org.anastylos.capture.camera.CameraSettings
import org.anastylos.capture.camera.CalibratedOptics
import org.anastylos.capture.camera.FocusMode
import org.anastylos.capture.camera.MeasuredProfileSource
import org.anastylos.capture.camera.DEFAULT_PROFILE
import org.anastylos.capture.camera.formatApplied
import org.anastylos.capture.camera.sanitizeSettings
import org.anastylos.capture.data.CameraPrefs
import org.anastylos.capture.data.CameraSettingsStore
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.GuidedAssociation
import org.anastylos.capture.data.GuidedAssociationRepository
import org.anastylos.capture.data.GuidedPrefs
import org.anastylos.capture.sync.DeliveryInput
import org.anastylos.capture.sync.DeliveryOutcome
import org.anastylos.capture.sync.GuidedAttemptInput
import org.anastylos.capture.sync.GuidedRemote
import org.anastylos.capture.sync.GuidedRemoteFactory
import org.anastylos.capture.sync.GuidedRemoteSession
import org.anastylos.capture.sync.GuidedTarget
import org.anastylos.capture.sync.GuidedTokenOutcome
import org.anastylos.capture.sync.GuidedViewRequest
import org.anastylos.capture.sync.HttpsTransport
import org.anastylos.capture.sync.PairingStore
import org.anastylos.capture.sync.RemoteAttemptOutcome
import org.anastylos.capture.sync.RemoteConsentOutcome
import org.anastylos.capture.sync.RemoteEventOutcome
import org.anastylos.capture.sync.RemoteSessionState
import org.anastylos.capture.sync.SpatialSidecar
import org.anastylos.capture.sync.tlsForPairing
import org.anastylos.capture.guidance.BoardGeometry
import org.anastylos.capture.guidance.CalibrationProfile
import org.anastylos.capture.guidance.DIAG_LOG_TAG
import org.anastylos.capture.guidance.TimingAccumulator
import org.anastylos.capture.guidance.CameraPoseB
import org.anastylos.capture.guidance.CueAxis
import org.anastylos.capture.guidance.DwellAccumulator
import org.anastylos.capture.guidance.HackathonMvp
import org.anastylos.capture.guidance.DwellState
import org.anastylos.capture.guidance.GuidanceHypothesis1
import org.anastylos.capture.guidance.ImageQualityInput
import org.anastylos.capture.guidance.ImageQualityVerdict
import org.anastylos.capture.guidance.LateMark
import org.anastylos.capture.guidance.MovementCue
import org.anastylos.capture.guidance.PhoneRotation
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.PoseEstimator
import org.anastylos.capture.guidance.Vec3
import org.anastylos.capture.guidance.remoteTargetView
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.RequestEvent
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionEvent
import org.anastylos.capture.guidance.SessionState
import org.anastylos.capture.guidance.ShutterGate
import org.anastylos.capture.guidance.SpeedTracker
import org.anastylos.capture.guidance.StabilitySample
import org.anastylos.capture.guidance.WireRegistration
import org.anastylos.capture.guidance.autoCaptureEligible
import org.anastylos.capture.guidance.computeCues
import org.anastylos.capture.guidance.eulerYXZDeg
import org.anastylos.capture.guidance.mapWireRegistration
import org.anastylos.capture.guidance.reduceRegistration
import org.anastylos.capture.guidance.reduceRequest
import org.anastylos.capture.guidance.reduceSession
import org.anastylos.capture.guidance.RegistrationEvent as WirePoseEvent
import org.anastylos.capture.guidance.evaluateImageQuality
import org.anastylos.capture.guidance.rotationOfRowMajor
import org.anastylos.capture.guidance.translationOfRowMajor

/**
 * One target viewpoint: local presets carry no tolerances (the
 * frozen profile applies); Core-adopted targets carry the wire
 * tolerances verbatim.
 */
data class TargetViewpoint(
    val id: String,
    val label: String,
    val boardId: String,
    val pose: CameraPoseB,
    val posTolMm: Vec3? = null,
    val angTolDeg: Double? = null,
)

val LOCAL_TARGETS = listOf(
    TargetViewpoint(
        id = "a3-top-center",
        label = "A3 top-down center",
        boardId = "surface_a3",
        pose = CameraPoseB(centerMm = Vec3(198.0, 136.5, -450.0)),
    ),
    TargetViewpoint(
        id = "a3-oblique-left",
        label = "A3 oblique left",
        boardId = "surface_a3",
        pose = CameraPoseB(centerMm = Vec3(90.0, 136.5, -380.0), yawDeg = -18.0, pitchDeg = 12.0),
    ),
    TargetViewpoint(
        id = "a4-top-center",
        label = "A4 top-down center",
        boardId = "surface_a4",
        pose = CameraPoseB(centerMm = Vec3(105.0, 148.5, -400.0)),
    ),
)

data class CaptureResult(
    val observationId: String,
    val imageId: String,
    val sha256: String,
    val automatic: Boolean,
    val associationNote: String,
)

data class GuidanceUiState(
    val cameraBound: Boolean = false,
    val cameraError: String? = null,
    val permissionGranted: Boolean = false,
    val session: SessionState = SessionState.DISARMED,
    val sessionReason: String? = null,
    val request: RequestState = RequestState.QUEUED,
    val lateMark: LateMark? = null,
    val requestId: String? = null,
    val requestRevision: Int = 0,
    val registration: RegistrationState = RegistrationState.UNREGISTERED,
    val wireReason: String? = null,
    val dwell: DwellState = DwellState.Idle,
    val cues: List<MovementCue> = emptyList(),
    val phoneRotation: PhoneRotation = PhoneRotation.R0,
    val poseAgeMs: Long? = null,
    val rmsPx: Double? = null,
    val cornersPx: List<PoseEstimator.MarkerCorners> = emptyList(),
    /** Raw detected marker IDs (empty when the detector saw nothing). */
    val markersSeen: List<Int> = emptyList(),
    val overlayGeometry: GuidedCamera.StreamGeometry? = null,
    val qualityVerdict: ImageQualityVerdict = ImageQualityVerdict.Unknown,
    /** Hackathon MVP: pixel-motion rejection alone does not block the shutter. */
    val motionWarning: Boolean = false,
    val measuredSharpness: Double? = null,
    val sharpnessReference: Double? = null,
    val orientationOk: Boolean = false,
    val distanceOk: Boolean = false,
    val boardId: String = "surface_a3",
    val target: TargetViewpoint? = null,
    val holdReason: ShutterGate.ShutterHoldReason? = null,
    val captureResult: CaptureResult? = null,
    val captureError: String? = null,
    val shutterBusy: Boolean = false,
    val intrinsicsLabel: String? = null,
    val uncalibrated: Boolean = true,
    val foreground: Boolean = false,
    val remoteAttached: Boolean = false,
    val remoteSessionId: String? = null,
    val remoteSession: SessionState? = null,
    val remoteError: String? = null,
    val remoteRequest: GuidedViewRequest? = null,
    val attemptReady: Boolean = false,
    val reservingAttempt: Boolean = false,
    val delivery: GuidedAssociation? = null,
    val deliveryNote: String? = null,
    val availableCameras: List<UsableCamera> = emptyList(),
    val selectedCameraId: String? = null,
    val effectiveCameraId: String? = null,
    val cameraNote: String? = null,
    val settings: CameraSettings = CameraSettings(),
    val activeProfile: String = DEFAULT_PROFILE,
    val appliedLine: String? = null,
    val settingsNote: String? = null,
    /** Why the validated profile does not apply, when one exists for this camera. */
    val calibrationBlocker: String? = null,
    /** Recorded optics of this camera's validated profile, if any. */
    val calibratedOptics: CalibratedOptics? = null,
)

/**
 * Guided-capture screen logic. Owns the session/request/registration
 * reducers, the dwell accumulator, the shutter gate and the capture
 * pipeline; every collaborator is constructor-injected so this class
 * unit-tests on the JVM with fakes (no camera, no OpenCV, no Room).
 *
 * Local mode (this milestone): the operator arms explicitly, picks a
 * local target preset, and captures persist through [FrameSampler].
 * Core requests, consent grants and receipts arrive in Phase E behind
 * the same reducer states; nothing here assumes a server.
 */
class GuidanceViewModel(
    private val repo: EvidenceRepository,
    private val sampler: FrameSampler,
    private val cameraFactory: (LifecycleOwner, PreviewView?) -> GuidedCamera,
    private val prefs: GuidedPrefs,
    private val cameraPrefs: CameraPrefs? = null,
    private val settingsStore: CameraSettingsStore? = null,
    private val nanoClock: () -> Long,
    private val utcClock: () -> Instant,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val pairingStore: PairingStore? = null,
    private val remoteFactory: GuidedRemoteFactory? = null,
    private val associations: GuidedAssociationRepository? = null,
    private val appVersion: String = "0.1.0",
    private val uuid: () -> String = { UUID.randomUUID().toString() },
    private val measuredProfiles: MeasuredProfileSource? = null,
    private val voice: org.anastylos.capture.voice.VoiceController? = null,
    private val timing: TimingAccumulator = TimingAccumulator(),
    /** Ignores a second Next-request tap this soon after a successful arm. Tests leave this at 0. */
    private val armDebounceMs: Long = 0L,
    // Same monotonic domain as nanoClock (elapsedRealtime in production);
    // deriving it keeps one injectable clock, so JVM tests need no Android.
    private val armClock: () -> Long = { nanoClock() / 1_000_000L },
    private val armLog: (String) -> Unit = { Log.i(ARM_LOG, it) },
) : ViewModel() {
    private val _state = MutableStateFlow(GuidanceUiState())
    val state: StateFlow<GuidanceUiState> = _state.asStateFlow()

    private var camera: GuidedCamera? = null
    private var cameraOwner: LifecycleOwner? = null
    private var cameraPreview: PreviewView? = null
    private var settingsJob: Job? = null
    private var lastAppliedSeen: AppliedSettings? = null
    private var estimatesJob: Job? = null
    /** Hackathon MVP dwell (~500 ms). The frozen 1 s / 12-sample profile stays on [DwellAccumulator]. */
    private val dwell = DwellAccumulator(
        holdMs = HackathonMvp.DWELL_MS,
        minSamples = HackathonMvp.DWELL_SAMPLES,
    )
    private val speeds = SpeedTracker()
    private val shutterMutex = Mutex()
    private var armingToken: String? = null
    private var firedForArming = false
    private var lastArmRealtimeMs = 0L
    private var armFrames = 0
    private var armFresh = 0
    private var lastDwellSamples = -1
    private var lastHoldReason: String? = null
    private var captureSequence = 0L
    private var sessionId: String? = null
    private var lastCorners: Map<String, List<Double>> = emptyMap()
    private var lastCornersNs: Long? = null
    private var lastEstimate: PoseEstimator.Estimate? = null
    private var lastMotion: Pair<Double, Double>? = null
    private var lastUiEstimateNs: Long? = null
    private var lastStillCallbackNs: Long? = null
    /** The pose current when the still arrived; frames keep coming before the sidecar is built. */
    private var stillEstimate: PoseEstimator.Estimate? = null
    private var remote: GuidedRemote? = null
    private var remoteCollectJob: Job? = null
    private var bindEpoch: String? = null
    private var pendingAttempt: GuidedAttemptInput? = null
    private var reservedAttemptId: String? = null
    private var reservedAttemptNumber: Int? = null
    private var lastMirroredRemoteSession: SessionState? = null
    private var lastMirroredRemoteStatus: String? = null
    private var adoptingRequestId: String? = null
    private val adoptedRequestIds = mutableSetOf<String>()
    private val bootAnchorId: String by lazy { uuid() }

    init {
        viewModelScope.launch {
            prefs.sharpnessReference.collect { reference ->
                _state.update { it.copy(sharpnessReference = reference) }
            }
        }
        viewModelScope.launch {
            try {
                cameraPrefs?.selectedCameraId?.collect { id ->
                    _state.update { it.copy(selectedCameraId = id) }
                }
            } catch (_: Exception) {
            }
        }
    }

    fun setPermissionGranted(granted: Boolean) {
        val was = _state.value.permissionGranted
        _state.update { it.copy(permissionGranted = granted) }
        if (was && !granted) onSessionEvent(SessionEvent.PermissionLost)
    }

    fun setPhoneRotation(rotation: PhoneRotation) {
        _state.update { it.copy(phoneRotation = rotation) }
    }

    fun setForeground(foreground: Boolean) {
        _state.update { it.copy(foreground = foreground) }
        if (!foreground) onSessionEvent(SessionEvent.Backgrounded)
    }

    init {
        viewModelScope.launch {
            try {
                settingsStore?.activeProfile?.collect { name ->
                    _state.update { it.copy(activeProfile = name) }
                }
            } catch (_: Exception) {
            }
        }
    }

    fun attachCamera(owner: LifecycleOwner, preview: PreviewView?) {
        if (camera != null) {
            camera?.attachPreview(preview)
            return
        }
        val source = cameraFactory(owner, preview)
        camera = source
        cameraOwner = owner
        cameraPreview = preview
        source.requestedCameraId = _state.value.selectedCameraId
        source.requestedStillSize = _state.value.settings.stillSize
        viewModelScope.launch {
            val bound = try {
                val size = _state.value.settings.analysisSize
                source.bindAsync(size.first, size.second)
            } catch (_: Exception) {
                false
            }
            if (!bound) {
                _state.update { it.copy(cameraBound = false, cameraError = "camera unavailable") }
                return@launch
            }
            source.setBoard(boardSpecFor(_state.value.boardId))
            val intrinsics = try {
                source.currentIntrinsics()
            } catch (_: Exception) {
                null
            }
            val geometry = try {
                source.streamGeometry()
            } catch (_: Exception) {
                null
            }
            _state.update {
                it.copy(
                    cameraBound = true, cameraError = null,
                    intrinsicsLabel = intrinsics?.profileId,
                    uncalibrated = intrinsics == null ||
                        intrinsics.profileId.contains("uncalibrated"),
                    overlayGeometry = geometry,
                    availableCameras = source.availableCameras(),
                    effectiveCameraId = source.effectiveCameraId,
                    cameraNote = source.cameraNote,
                )
            }
            _state.update {
                it.withCalibrationStatus(intrinsics?.profileId)
            }
            observeSettings()
            estimatesJob?.cancel()
            estimatesJob = viewModelScope.launch {
                source.estimates.collect { estimate ->
                    pollApplied(source)
                    onEstimate(estimate)
                }
            }
        }
    }

    fun detachCamera() {
        estimatesJob?.cancel()
        estimatesJob = null
        try {
            camera?.unbind()
        } catch (_: Exception) {
        }
        camera = null
        _state.update { it.copy(cameraBound = false) }
        onSessionEvent(SessionEvent.Backgrounded)
    }

    fun selectBoard(boardId: String) {
        if (boardId == _state.value.boardId) return
        camera?.setBoard(boardSpecFor(boardId))
        dwell.reset()
        speeds.reset()
        lastCorners = emptyMap()
        lastCornersNs = null
        _state.update { it.copy(boardId = boardId, dwell = DwellState.Idle, cues = emptyList()) }
    }

    /**
     * Operator camera switch: persists the ID, then rebinds on a
     * new sensor. The rebind invalidates everything downstream —
     * fresh intrinsics/profile, no pose or motion reuse — and the
     * session disarms via detach (rearm required), matching the
     * rotation-crop safety rule.
     */
    fun selectCamera(id: String) {
        if (id == _state.value.selectedCameraId) return
        _state.update { it.copy(selectedCameraId = id) }
        viewModelScope.launch {
            try {
                cameraPrefs?.setSelectedCameraId(id)
            } catch (_: Exception) {
                _state.update { it.copy(cameraNote = "could not save the camera selection") }
            }
        }
        if (!_state.value.cameraBound) return
        rebindCamera()
    }

    private fun rebindCamera() {
        dwell.reset()
        speeds.reset()
        lastCorners = emptyMap()
        lastCornersNs = null
        lastEstimate = null
        stillEstimate = null
        lastMotion = null
        lastAppliedSeen = null
        _state.update { it.copy(appliedLine = null) }
        val owner = cameraOwner
        val preview = cameraPreview
        detachCamera()
        if (owner != null) attachCamera(owner, preview)
    }

    fun capsFor(id: String?): CameraCapabilities {
        return _state.value.availableCameras.firstOrNull { it.id == id }?.capabilities
            ?: CameraCapabilities()
    }

    private fun observeSettings() {
        settingsJob?.cancel()
        val id = _state.value.effectiveCameraId ?: return
        val profile = _state.value.activeProfile
        val store = settingsStore ?: return
        settingsJob = viewModelScope.launch {
            try {
                store.settings(id, profile).collect { stored ->
                    val caps = capsFor(id)
                    val clean = sanitizeSettings(stored, caps)
                    val sizeChanged = clean.analysisSize != _state.value.settings.analysisSize ||
                        clean.stillSize != _state.value.settings.stillSize
                    _state.update { it.copy(settings = clean) }
                    camera?.requestedStillSize = clean.stillSize
                    // GUIDE-01 Phase C: the first bind runs on default
                    // settings (stored settings arrive async after it);
                    // a stored size mismatch must rebind like a manual
                    // size change, never linger on the wrong stream.
                    if (sizeChanged && _state.value.cameraBound) rebindCamera()
                    else applyToCamera(clean, caps)
                }
            } catch (_: Exception) {
            }
        }
    }

    private fun applyToCamera(settings: CameraSettings, caps: CameraCapabilities) {
        val source = camera ?: return
        if (!_state.value.cameraBound) return
        val ok = try {
            source.applySettings(settings, caps)
        } catch (_: Exception) {
            false
        }
        val label = try {
            source.currentIntrinsics()?.profileId
        } catch (_: Exception) {
            _state.value.intrinsicsLabel
        }
        _state.update {
            it.copy(
                settingsNote = if (ok) null else "settings not applied",
                intrinsicsLabel = label,
                uncalibrated = label == null || label.contains("uncalibrated"),
            ).withCalibrationStatus(label)
        }
        viewModelScope.launch {
            val stillOk = try {
                source.refreshStillOptions()
            } catch (_: Exception) {
                false
            }
            if (!stillOk) {
                _state.update {
                    if (it.settingsNote == null) {
                        it.copy(settingsNote = "still capture options not refreshed")
                    } else {
                        it
                    }
                }
            }
        }
    }

    private fun pollApplied(source: GuidedCamera) {
        val applied = try {
            source.lastApplied
        } catch (_: Exception) {
            null
        }
        if (applied != null && applied != lastAppliedSeen) {
            lastAppliedSeen = applied
            _state.update { it.copy(appliedLine = formatApplied(applied)) }
        }
    }

    fun updateSettings(transform: (CameraSettings) -> CameraSettings) {
        val id = _state.value.effectiveCameraId
        val caps = capsFor(id)
        val next = sanitizeSettings(transform(_state.value.settings), caps)
        val sizeChanged = next.analysisSize != _state.value.settings.analysisSize ||
            next.stillSize != _state.value.settings.stillSize
        _state.update { it.copy(settings = next) }
        if (id != null) {
            val profile = _state.value.activeProfile
            viewModelScope.launch {
                try {
                    settingsStore?.update(id, profile, next)
                } catch (_: Exception) {
                    _state.update { it.copy(settingsNote = "could not save settings") }
                }
            }
        }
        if (sizeChanged && _state.value.cameraBound) rebindCamera()
        else applyToCamera(next, caps)
    }

    fun selectProfile(name: String) {
        if (name == _state.value.activeProfile) return
        _state.update { it.copy(activeProfile = name) }
        viewModelScope.launch {
            try {
                settingsStore?.setActiveProfile(name)
            } catch (_: Exception) {
            }
        }
        observeSettings()
    }

    /**
     * One-tap reuse of the validated profile: sets exactly the optics
     * it was recorded with (manual focus, zoom 1x) and a compatible
     * analysis grid, keeping the current one when it already is.
     * Validity is still decided by the profile binding afterwards.
     */
    fun applyCalibratedOptics() {
        val optics = _state.value.calibratedOptics ?: return
        updateSettings { current ->
            val grid = listOf(current.analysisSize, CameraSettings.DEFAULT_ANALYSIS_SIZE)
                .firstOrNull { (w, h) ->
                    CalibrationProfile.gridScale(optics.referenceWidth, optics.referenceHeight, w, h) != null
                } ?: (optics.referenceWidth to optics.referenceHeight)
            current.copy(
                focusMode = FocusMode.MANUAL,
                focusDiopters = optics.focusDiopters,
                linearZoom = 0f,
                analysisSize = grid,
            )
        }
    }

    private fun GuidanceUiState.withCalibrationStatus(label: String?): GuidanceUiState {
        val profiles = measuredProfiles ?: return this
        val calibrated = label != null && !label.contains("uncalibrated")
        val optics = profiles.calibratedOptics(effectiveCameraId)
        val (w, h) = overlayGeometry?.let { it.analysisWidth to it.analysisHeight } ?: settings.analysisSize
        val blocker = if (calibrated) {
            null
        } else {
            profiles.mismatch(
                effectiveCameraId, w, h, settings.linearZoom, settings.focusMode, settings.focusDiopters,
            )
        }
        return copy(calibrationBlocker = blocker, calibratedOptics = optics)
    }

    fun selectTarget(target: TargetViewpoint?) {
        if (target != null && target.boardId != _state.value.boardId) {
            selectBoard(target.boardId)
        }
        _state.update { it.copy(target = target) }
    }

    fun arm() {
        val snap = _state.value
        if (snap.remoteAttached) {
            logArm("arm remote session=${snap.session} request=${snap.request} id=${snap.requestId}")
            remoteArmFlow()
            return
        }
        // Fresh session arming from DISARMED.
        if (snap.session == SessionState.DISARMED) {
            val transition = onSessionEvent(
                SessionEvent.Arm(
                    grantLive = true,
                    permissionGranted = snap.permissionGranted,
                    foreground = snap.foreground,
                ),
            )
            if (!transition.accepted) {
                logArm(
                    "arm rejected session=${snap.session} fg=${snap.foreground} " +
                        "perm=${snap.permissionGranted} reason=${transition.reason}",
                )
                return
            }
            if (startRequest("arm")) {
                viewModelScope.launch {
                    ensureSession()
                }
            }
            return
        }
        // Next request inside a live session: allowed only once the
        // previous request reached a terminal state, with a new token.
        // ACTIVE+QUEUED is the stuck state after an arm transition that
        // never started its request. It is not a finished capture.
        if (snap.session == SessionState.ACTIVE &&
            (snap.request in TERMINAL_REQUESTS || snap.request == RequestState.QUEUED)
        ) {
            startRequest(if (snap.request == RequestState.QUEUED) "arm-queued" else "rearm")
            return
        }
        logArm(
            "arm ignored session=${snap.session} request=${snap.request} id=${snap.requestId} " +
                "consumed=$firedForArming token=${armingToken?.take(8)} fg=${snap.foreground}",
        )
    }

    private fun remoteArmFlow() {
        val session = remote?.session ?: return
        if (_state.value.session == SessionState.DISARMED) {
            _state.update { it.copy(remoteError = null) }
            viewModelScope.launch {
                val expiry = try {
                    utcClock().plusSeconds(1800).toIsoUtc()
                } catch (_: Exception) {
                    _state.update { it.copy(remoteError = "clock unavailable") }
                    return@launch
                }
                when (
                    val outcome = session.arm(
                        cameraId = cameraIdForConsent(),
                        maxImages = 20,
                        expiresAt = expiry,
                        allowAutoUpload = true,
                    )
                ) {
                    is RemoteConsentOutcome.Applied -> {
                        if (outcome.session != SessionState.ACTIVE) {
                            _state.update {
                                it.copy(remoteError = "consent applied but session is ${outcome.session}")
                            }
                            return@launch
                        }
                        val transition = onSessionEvent(
                            SessionEvent.Arm(
                                grantLive = true,
                                permissionGranted = _state.value.permissionGranted,
                                foreground = _state.value.foreground,
                            ),
                        )
                        if (!transition.accepted) return@launch
                        viewModelScope.launch { ensureSession() }
                        postRemoteEvent("armed")
                        tryAdopt(session.state.value)
                    }
                    is RemoteConsentOutcome.Conflict -> _state.update {
                        it.copy(
                            remoteError = "revision conflict (Core at ${outcome.currentRevision}); retry arm",
                        )
                    }
                    is RemoteConsentOutcome.NeedsEnrollment -> _state.update {
                        it.copy(remoteError = "device not enrolled: ${outcome.message}")
                    }
                    is RemoteConsentOutcome.Failed -> _state.update {
                        it.copy(remoteError = outcome.message)
                    }
                }
            }
            return
        }
        if (_state.value.session == SessionState.ACTIVE && _state.value.request in TERMINAL_REQUESTS) {
            tryAdopt(session.state.value)
        }
    }

    /**
     * Full reset to a fresh session lifecycle (new reducer
     * instances). Used after CANCELLED / EXPIRED / COMPLETE, which
     * the session reducer intentionally leaves terminal.
     */
    fun newSession() {
        dwell.reset()
        speeds.reset()
        armingToken = null
        firedForArming = false
        sessionId = null
        lastCorners = emptyMap()
        lastCornersNs = null
        _state.update {
            it.copy(
                session = SessionState.DISARMED, sessionReason = null,
                request = RequestState.QUEUED, lateMark = null,
                requestId = null, dwell = DwellState.Idle,
                cues = emptyList(), holdReason = null,
                captureResult = null, captureError = null,
            )
        }
    }

    private fun startRequest(source: String): Boolean {
        val now = armClock()
        if (armDebounceMs > 0L && lastArmRealtimeMs > 0L && now - lastArmRealtimeMs < armDebounceMs) {
            logArm(
                "arm debounced source=$source waited=${now - lastArmRealtimeMs}ms " +
                    "request=${_state.value.request} id=${_state.value.requestId} consumed=$firedForArming",
            )
            return false
        }
        lastArmRealtimeMs = now
        // A new arming token starts a new request lifecycle (a fresh
        // reducer): the token binds exactly one request, so a repeat
        // capture after CAPTURED_LOCAL never inherits the previous
        // request's state.
        armingToken = UUID.randomUUID().toString()
        firedForArming = false
        armFrames = 0
        armFresh = 0
        lastDwellSamples = 0
        // Each arming collects its own 500 ms dwell. A Ready window
        // from the previous shot must not satisfy the next one.
        dwell.reset()
        val requestId = "local-${armingToken!!.take(8)}"
        _state.update {
            it.copy(
                request = RequestState.QUEUED, lateMark = null,
                requestId = requestId, requestRevision = 1, captureResult = null,
            )
        }
        onRequestEvent(RequestEvent.Accept)
        logArm(
            "arm start source=$source id=$requestId session=${_state.value.session} " +
                "request=${_state.value.request} consumed=false fg=${_state.value.foreground}",
        )
        return true
    }

    fun disarm() {
        onSessionEvent(SessionEvent.OperatorDisarm)
        armingToken = null
        firedForArming = false
        pendingAttempt = null
        reservedAttemptId = null
        reservedAttemptNumber = null
        if (_state.value.remoteAttached) {
            postRemoteEvent("disarmed")
            val session = remote?.session
            if (session != null) {
                viewModelScope.launch {
                    val expiry = try {
                        utcClock().plusSeconds(1800).toIsoUtc()
                    } catch (_: Exception) {
                        return@launch
                    }
                    when (
                        val outcome = session.disarm(
                            cameraId = cameraIdForConsent(),
                            maxImages = 20,
                            expiresAt = expiry,
                        )
                    ) {
                        is RemoteConsentOutcome.Applied -> Unit
                        is RemoteConsentOutcome.Conflict -> _state.update {
                            it.copy(remoteError = "disarm conflict (Core at ${outcome.currentRevision})")
                        }
                        is RemoteConsentOutcome.NeedsEnrollment -> _state.update {
                            it.copy(remoteError = "device not enrolled: ${outcome.message}")
                        }
                        is RemoteConsentOutcome.Failed -> _state.update {
                            it.copy(remoteError = outcome.message)
                        }
                    }
                }
            }
        }
    }

    fun pause() {
        onSessionEvent(SessionEvent.OperatorPause)
        postRemoteEvent("paused")
    }

    fun resume() {
        onSessionEvent(
            SessionEvent.Resume(
                grantLive = true,
                registrationFresh = _state.value.registration == RegistrationState.ACQUIRED,
                foreground = _state.value.foreground,
            ),
        )
        postRemoteEvent("resumed")
    }

    fun cancelRequest() {
        onRequestEvent(RequestEvent.Cancel)
        postRemoteEvent("cancelled")
    }

    fun skipTarget() {
        onRequestEvent(RequestEvent.Skip)
    }

    fun cantReach() {
        onRequestEvent(RequestEvent.CantReach)
        postRemoteEvent("unreachable")
    }

    fun setSharpnessReference() {
        val measured = _state.value.measuredSharpness
        if (measured == null || !measured.isFinite() || measured <= 0.0) return
        viewModelScope.launch {
            try {
                prefs.setSharpnessReference(measured)
            } catch (_: Exception) {
            }
        }
    }

    fun clearSharpnessReference() {
        viewModelScope.launch {
            try {
                prefs.clearSharpnessReference()
            } catch (_: Exception) {
            }
        }
    }

    /** Reliable manual fallback: explicit operator still, always unqualified spatially. */
    fun captureManual() {
        val source = camera ?: return
        if (_state.value.shutterBusy) return
        _state.update { it.copy(shutterBusy = true, captureError = null) }
        viewModelScope.launch {
            try {
                val still = source.takeGuidedStill()
                lastStillCallbackNs = nanoClock()
                stillEstimate = lastEstimate
                val stored = persistStill(still, automatic = false)
                _state.update { it.copy(shutterBusy = false, captureResult = stored) }
            } catch (e: Exception) {
                _state.update { it.copy(shutterBusy = false, captureError = e.message ?: "capture failed") }
            }
        }
    }

    fun dismissResult() {
        _state.update { it.copy(captureResult = null, captureError = null) }
    }

    // ---------- remote Core control ----------

    fun attachRemote(sessionId: String) {
        if (_state.value.session != SessionState.DISARMED) {
            _state.update { it.copy(remoteError = "disarm before attaching a remote session") }
            return
        }
        if (!isUuid(sessionId)) {
            _state.update { it.copy(remoteError = "session id is not a uuid") }
            return
        }
        val factory = remoteFactory
        val store = pairingStore
        if (factory == null || store == null) {
            _state.update { it.copy(remoteError = "remote control unavailable in this build") }
            return
        }
        val pairing = store.load()
        if (pairing == null) {
            _state.update { it.copy(remoteError = "pair with a workstation first") }
            return
        }
        val transport = try {
            HttpsTransport(pairing.baseUrl, tlsForPairing(pairing))
        } catch (e: Exception) {
            _state.update { it.copy(remoteError = "transport unavailable: ${e.message}") }
            return
        }
        detachRemote()
        val bundle = factory.create(transport)
        remote = bundle
        bindEpoch = uuid()
        adoptedRequestIds.clear()
        pendingAttempt = null
        reservedAttemptId = null
        reservedAttemptNumber = null
        lastMirroredRemoteSession = null
        lastMirroredRemoteStatus = null
        _state.update {
            it.copy(
                remoteAttached = true, remoteSessionId = sessionId, remoteError = null,
                remoteRequest = null, attemptReady = false, reservingAttempt = false,
                delivery = null, deliveryNote = null,
            )
        }
        voice?.link = org.anastylos.capture.sync.GuidedVoiceLink(
            org.anastylos.capture.sync.GuidedControlClient(transport), sessionId,
            token = { (bundle.session.resolveToken() as? GuidedTokenOutcome.Ready)?.token },
            utcNow = { utcClock().toIsoUtc() },
        )
        bundle.session.attach(sessionId)
        bundle.session.startPolling()
        viewModelScope.launch { publishCapabilities(bundle.session) }
        remoteCollectJob?.cancel()
        remoteCollectJob = viewModelScope.launch {
            bundle.session.state.collect { rs ->
                _state.update {
                    it.copy(
                        remoteSession = rs.session,
                        remoteError = rs.lastError ?: it.remoteError,
                    )
                }
                mirrorRemoteSession(rs)
                mirrorRemoteRequests(rs)
            }
        }
    }

    /**
     * Publishes what this phone can fulfil. Only a measured profile's
     * uuid is offered as a calibration id; factory intrinsics offer none.
     */
    private suspend fun publishCapabilities(session: GuidedRemoteSession) {
        val source = session.resolveToken() as? GuidedTokenOutcome.Ready ?: return
        val caps = org.anastylos.capture.sync.GuidedCapabilities(
            sourceId = source.sourceId,
            updatedAt = utcClock().toIsoUtc(),
            stale = false,
            cameraIds = _state.value.availableCameras.map { it.id }.take(8),
            resolutionsPx = listOfNotNull(
                _state.value.overlayGeometry?.let { listOf(it.analysisWidth, it.analysisHeight) },
            ),
            calibrationIds = listOfNotNull(measuredCalibrationId(_state.value.intrinsicsLabel)),
            targetKinds = listOf("pose"),
            qualityProfileIds = listOf(GuidanceHypothesis1.PROFILE_ID),
            appVersion = appVersion,
            protocolVersion = 1,
        )
        when (val outcome = session.publishCapabilities(caps)) {
            is RemoteEventOutcome.Sent -> Unit
            is RemoteEventOutcome.Failed -> _state.update { it.copy(remoteError = outcome.message) }
        }
    }

    fun detachRemote() {
        voice?.link = org.anastylos.capture.voice.DetachedVoiceLink()
        remoteCollectJob?.cancel()
        remoteCollectJob = null
        try {
            remote?.session?.detach()
        } catch (_: Exception) {
        }
        remote = null
        pendingAttempt = null
        reservedAttemptId = null
        reservedAttemptNumber = null
        _state.update {
            it.copy(
                remoteAttached = false, remoteSessionId = null, remoteSession = null,
                remoteError = null, remoteRequest = null, attemptReady = false,
                reservingAttempt = false,
            )
        }
    }

    fun clearRemoteError() {
        _state.update { it.copy(remoteError = null) }
    }

    /**
     * Operator declares which physical frame a Core board uuid is.
     * Required before a remote request is adopted — the phone never
     * guesses board identity.
     */
    fun selectRemoteBoard(frameId: String) {
        val boardUuid = _state.value.remoteRequest?.boardId ?: pendingCandidateBoardUuid()
        if (boardUuid == null) {
            _state.update { it.copy(remoteError = "no remote request to map") }
            return
        }
        if (BoardGeometry.forBoardId(frameId) == null) {
            _state.update { it.copy(remoteError = "unknown frame $frameId") }
            return
        }
        viewModelScope.launch {
            try {
                prefs.setBoardFrame(boardUuid, frameId)
            } catch (e: Exception) {
                _state.update { it.copy(remoteError = "mapping not saved: ${e.message}") }
                return@launch
            }
            selectBoard(frameId)
            _state.update { it.copy(remoteError = null) }
            remote?.session?.let { tryAdopt(it.state.value) }
        }
    }

    fun retryAttempt() {
        if (!_state.value.remoteAttached || _state.value.remoteRequest == null) return
        if (_state.value.attemptReady || _state.value.reservingAttempt) return
        if (pendingAttempt == null) return
        reserveAttempt()
    }

    private fun pendingCandidateBoardUuid(): String? {
        val rs = remote?.session?.state?.value ?: return null
        val active = rs.activeRequestId?.let { rs.requests[it] }
        if (active != null && active.status == "QUEUED") return active.boardId
        return rs.requests.values.firstOrNull { it.status == "QUEUED" }?.boardId
    }

    private fun mirrorRemoteSession(rs: RemoteSessionState) {
        val mapped = rs.session ?: return
        if (mapped == lastMirroredRemoteSession) return
        lastMirroredRemoteSession = mapped
        val local = _state.value.session
        when (mapped) {
            SessionState.ACTIVE -> if (local == SessionState.PAUSED) {
                onSessionEvent(
                    SessionEvent.Resume(
                        grantLive = true,
                        registrationFresh = _state.value.registration == RegistrationState.ACQUIRED,
                        foreground = _state.value.foreground,
                    ),
                )
            }
            // Local arming stays operator-driven: remote ACTIVE never
            // arms a DISARMED phone by itself.
            SessionState.PAUSED -> onSessionEvent(SessionEvent.OperatorPause)
            SessionState.CANCELLED -> onSessionEvent(SessionEvent.OperatorDisarm)
            SessionState.EXPIRED -> onSessionEvent(SessionEvent.GrantExpired)
            // Consent changed under us: back to DISARMED, rearm required.
            SessionState.DISARMED -> onSessionEvent(SessionEvent.ProcessRestarted)
            SessionState.ACQUISITION_COMPLETE -> onSessionEvent(SessionEvent.AcquisitionComplete)
            SessionState.COMPLETE -> onSessionEvent(SessionEvent.AllQualified)
            SessionState.COMPLETE_WITH_SKIPS -> onSessionEvent(SessionEvent.CompleteWithSkips)
        }
    }

    private fun mirrorRemoteRequests(rs: RemoteSessionState) {
        val adopted = _state.value.remoteRequest
        if (adopted == null) {
            if (_state.value.session == SessionState.ACTIVE) tryAdopt(rs)
            return
        }
        val current = rs.requests[adopted.requestId] ?: return
        if (current != adopted) _state.update { it.copy(remoteRequest = current) }
        if (current.status != lastMirroredRemoteStatus) {
            lastMirroredRemoteStatus = current.status
            when (current.status) {
                "CANCELLED" -> onRequestEvent(RequestEvent.Cancel)
                "EXPIRED" -> onRequestEvent(RequestEvent.Expire)
                "SUPERSEDED" -> onRequestEvent(RequestEvent.Supersede)
                else -> Unit
            }
        }
        if (_state.value.request in TERMINAL_REQUESTS) tryAdopt(rs, exclude = adopted.requestId)
    }

    private fun tryAdopt(rs: RemoteSessionState, exclude: String? = null) {
        if (_state.value.remoteRequest != null && exclude == null) return
        val candidate = selectCandidate(rs, exclude) ?: return
        if (candidate.requestId == adoptingRequestId) return
        if (_state.value.session != SessionState.ACTIVE) return
        adoptingRequestId = candidate.requestId
        viewModelScope.launch {
            try {
                adoptRemoteRequest(candidate)
            } finally {
                if (adoptingRequestId == candidate.requestId) adoptingRequestId = null
            }
        }
    }

    private fun selectCandidate(rs: RemoteSessionState, exclude: String?): GuidedViewRequest? {
        val active = rs.activeRequestId?.let { rs.requests[it] }
        if (active != null && active.status == "QUEUED" && active.requestId != exclude &&
            active.requestId !in adoptedRequestIds
        ) {
            return active
        }
        return rs.requests.values
            .filter { it.status == "QUEUED" && it.requestId != exclude && it.requestId !in adoptedRequestIds }
            .minByOrNull { it.revision }
    }

    private fun adoptRemoteRequest(candidate: GuidedViewRequest) {
        viewModelScope.launch {
            val frame = try {
                prefs.boardFrameFor(candidate.boardId).first()
            } catch (_: Exception) {
                null
            }
            if (frame.isNullOrBlank()) {
                _state.update {
                    it.copy(
                        remoteError = "request targets board ${candidate.boardId.take(8)}…; " +
                            "map it to a physical frame",
                    )
                }
                return@launch
            }
            if (BoardGeometry.forBoardId(frame) == null) {
                _state.update { it.copy(remoteError = "mapped frame $frame is unknown") }
                return@launch
            }
            val view = remoteTargetView(
                requestId = candidate.requestId,
                targetLabel = candidate.targetLabel,
                boardFrameId = frame,
                tbcUnits = candidate.target.tbc.units,
                tbcMatrix = candidate.target.tbc.matrix,
                translationToleranceMm = candidate.target.translationToleranceMm,
                rotationToleranceDeg = candidate.target.rotationToleranceDeg,
            )
            if (view == null) {
                _state.update { it.copy(remoteError = "request target failed validation; not adopted") }
                return@launch
            }
            selectBoard(frame)
            armingToken = uuid()
            firedForArming = false
            pendingAttempt = null
            reservedAttemptId = null
            reservedAttemptNumber = null
            adoptedRequestIds += candidate.requestId
            lastMirroredRemoteStatus = candidate.status
            _state.update {
                it.copy(
                    request = RequestState.QUEUED, lateMark = null,
                    requestId = candidate.requestId, requestRevision = candidate.revision,
                    remoteRequest = candidate,
                    target = TargetViewpoint(
                        id = view.id,
                        label = view.label,
                        boardId = view.boardFrameId,
                        pose = view.pose,
                        posTolMm = view.posTolMm,
                        angTolDeg = view.angTolDeg,
                    ),
                    captureResult = null, attemptReady = false, reservingAttempt = false,
                    delivery = null, deliveryNote = null, remoteError = null,
                )
            }
            onRequestEvent(RequestEvent.Accept)
            postRemoteEvent("accepted")
        }
    }

    private fun postRemoteEvent(kind: String, reason: String? = null, attemptId: String? = null) {
        val session = remote?.session ?: return
        if (!_state.value.remoteAttached) return
        viewModelScope.launch {
            val event = session.buildEvent(
                kind = kind,
                requestId = _state.value.remoteRequest?.requestId,
                requestRevision = _state.value.remoteRequest?.revision,
                attemptId = attemptId,
                reason = reason,
            ) ?: return@launch
            when (val outcome = session.postEvent(event)) {
                is RemoteEventOutcome.Sent -> Unit
                is RemoteEventOutcome.Failed ->
                    _state.update { it.copy(remoteError = outcome.message) }
            }
        }
    }

    private fun maybeReserveAttempt() {
        if (!_state.value.remoteAttached || _state.value.remoteRequest == null) return
        if (_state.value.request != RequestState.HOLD_STILL) return
        if (_state.value.attemptReady || _state.value.reservingAttempt) return
        if (pendingAttempt != null) return
        reserveAttempt()
    }

    private fun reserveAttempt() {
        val session = remote?.session ?: return
        val request = _state.value.remoteRequest ?: return
        val consentRevision = maxOf(1, session.state.value.consentRevision)
        val input = pendingAttempt ?: GuidedAttemptInput(
            operationId = uuid(),
            sessionId = request.sessionId,
            requestId = request.requestId,
            requestRevision = request.revision,
            attemptId = uuid(),
            consentRevision = consentRevision,
            boardRegistrationEpochId = request.boardRegistrationEpochId,
            objectPoseEpochId = request.objectPoseEpochId,
        ).also { pendingAttempt = it }
        _state.update { it.copy(reservingAttempt = true, remoteError = null) }
        viewModelScope.launch {
            when (val outcome = session.reserveAttempt(input)) {
                is RemoteAttemptOutcome.Reserved -> {
                    if (outcome.status == "reserved") {
                        reservedAttemptId = outcome.attemptId
                        reservedAttemptNumber = outcome.attemptNumber
                        _state.update { it.copy(reservingAttempt = false, attemptReady = true) }
                    } else {
                        _state.update {
                            it.copy(reservingAttempt = false, remoteError = "attempt ${outcome.status}")
                        }
                    }
                }
                is RemoteAttemptOutcome.Conflict -> _state.update {
                    it.copy(reservingAttempt = false, remoteError = "attempt conflict (${outcome.code})")
                }
                is RemoteAttemptOutcome.Expired -> _state.update {
                    it.copy(reservingAttempt = false, remoteError = "attempt expired: ${outcome.message}")
                }
                is RemoteAttemptOutcome.Failed -> _state.update {
                    it.copy(reservingAttempt = false, remoteError = outcome.message)
                }
            }
        }
    }

    private fun cameraIdForConsent(): String =
        when (_state.value.overlayGeometry?.lensFacing) {
            CameraSelector.LENS_FACING_FRONT -> "camera:1"
            else -> "camera:0"
        }

    /** `measured:<uuid>[@WxH-scaled]` → `<uuid>`; factory intrinsics → null. */
    private fun measuredCalibrationId(intrinsicsLabel: String?): String? {
        val id = intrinsicsLabel?.removePrefix("measured:")?.substringBefore('@') ?: return null
        return id.takeIf { intrinsicsLabel.startsWith("measured:") && isUuid(it) }
    }

    private fun isUuid(value: String): Boolean =
        try {
            UUID.fromString(value)
            true
        } catch (_: IllegalArgumentException) {
            false
        }

    private fun onSessionEvent(event: SessionEvent): org.anastylos.capture.guidance.SessionTransition {
        val transition = reduceSession(_state.value.session, event)
        _state.update { it.copy(session = transition.state, sessionReason = transition.reason) }
        if (transition.state == SessionState.DISARMED) {
            dwell.reset()
            armingToken = null
            firedForArming = false
        }
        return transition
    }

    private fun onRequestEvent(event: RequestEvent) {
        val transition = reduceRequest(_state.value.request, event, _state.value.lateMark)
        _state.update {
            it.copy(request = transition.state, lateMark = transition.lateMark)
        }
    }

    private fun boardSpecFor(boardId: String): PoseEstimator.BoardSpec {
        val board = BoardGeometry.forBoardId(boardId)
        if (board == null) {
            // ChArUco (or unknown): detection-only, no metric geometry.
            return PoseEstimator.BoardSpec(
                boardId = boardId, markerSizeMm = 0.0, markerIds = emptyList(),
                dictionary = "DICT_6X6_250",
            )
        }
        return PoseEstimator.BoardSpec(
            boardId = board.boardId,
            markerSizeMm = board.markers.values.firstOrNull()?.sizeMm ?: 0.0,
            markerIds = board.markers.keys.sorted(),
            dictionary = board.dictionary,
            markerCornersMm = board.markers.mapValues { (_, marker) -> marker.corners },
        )
    }

    private fun onEstimate(estimate: PoseEstimator.Estimate) {
        // Rotation can change after bind (display rotation); refresh
        // the overlay geometry when the camera reports a new one.
        val geometry = try {
            camera?.streamGeometry()
        } catch (_: Exception) {
            null
        }
        if (geometry != null && geometry != _state.value.overlayGeometry) {
            _state.update { it.copy(overlayGeometry = geometry) }
        }
        val nowNs = nanoClock()
        val ageMs = (nowNs - estimate.monotonicNs) / 1_000_000L
        recordUiTiming(nowNs, ageMs, estimate)
        val (phone, wireReason) = mapWireRegistration(estimate.status)
        val poseEvent = when (estimate.status) {
            WireRegistration.VALID -> WirePoseEvent.PosesValid
            WireRegistration.AMBIGUOUS, WireRegistration.DEGRADED -> WirePoseEvent.PoseAmbiguous
            WireRegistration.LOST, WireRegistration.UNREGISTERED -> WirePoseEvent.PoseLost
            WireRegistration.BAD_CALIBRATION -> WirePoseEvent.CalibrationInvalid
        }
        val registration = reduceRegistration(_state.value.registration, poseEvent).state
        val pose = estimate.tBC?.let { tBC ->
            val center = translationOfRowMajor(tBC)
            val (yaw, pitch, roll) = eulerYXZDeg(rotationOfRowMajor(tBC))
            CameraPoseB(center, yaw, pitch, roll)
        }
        val target = _state.value.target
        val cues = if (pose != null && target != null && registration == RegistrationState.ACQUIRED) {
            computeCues(
                pose, target.pose, _state.value.phoneRotation,
                target.posTolMm ?: Vec3(
                    GuidanceHypothesis1.POS_TOL_X_MM,
                    GuidanceHypothesis1.POS_TOL_Y_MM,
                    GuidanceHypothesis1.POS_TOL_Z_MM,
                ),
                target.angTolDeg ?: GuidanceHypothesis1.ANG_TOL_DEG,
            )
        } else {
            emptyList()
        }
        val hasTarget = target != null
        val orientationOk = hasTarget && cues.none { it.axis == CueAxis.YAW || it.axis == CueAxis.PITCH || it.axis == CueAxis.ROLL }
        val distanceOk = hasTarget && cues.none { it.axis == CueAxis.LATERAL || it.axis == CueAxis.VERTICAL || it.axis == CueAxis.DEPTH }
        val motion = motionStats(estimate)
        val quality = evaluateImageQuality(
            ImageQualityInput(
                sharpness = estimate.frameQuality?.sharpness,
                sharpReference = _state.value.sharpnessReference,
                darkFraction = estimate.frameQuality?.darkFraction,
                saturatedFraction = estimate.frameQuality?.saturatedFraction,
                motionMedianPx = motion?.first,
                motionP90Px = motion?.second,
            ),
        )
        // Hackathon MVP: ArUco corner jitter is a warning. Exposure and
        // sharpness still reject. guidance-hypothesis-1 itself is unchanged.
        val motionWarning = quality is ImageQualityVerdict.Rejected &&
            quality.reasons.all { it == "motion" }
        val qualityForGate = quality == ImageQualityVerdict.Accepted || motionWarning
        val calibrated = !_state.value.uncalibrated
        val poseFresh = ageMs in 0..GuidanceHypothesis1.POSE_AGE_MS
        val aligned = orientationOk && distanceOk && qualityForGate && calibrated
        val gatesPass = _state.value.session == SessionState.ACTIVE &&
            registration == RegistrationState.ACQUIRED && aligned && poseFresh
        // A stale but still-acquired pose is not evidence either way.
        val ignoreStale = registration == RegistrationState.ACQUIRED && !poseFresh
        val windowSpeeds = if (ignoreStale) {
            null
        } else if (pose != null && estimate.tBC != null) {
            speeds.observe(
                estimate.monotonicNs / 1_000_000L,
                pose.centerMm,
                rotationOfRowMajor(estimate.tBC),
            )
        } else {
            speeds.reset()
            null
        }
        val beforeDwell = dwell.state
        val dwellState = if (ignoreStale) {
            dwell.bridge(estimate.monotonicNs / 1_000_000L)
            dwell.state
        } else {
            dwell.observe(
                StabilitySample(
                    tMs = estimate.monotonicNs / 1_000_000L,
                    speedMmS = windowSpeeds?.speedMmS ?: Double.MAX_VALUE,
                    angularSpeedDegS = windowSpeeds?.angularSpeedDegS ?: Double.MAX_VALUE,
                    poseAgeMs = ageMs,
                    gatesPass = gatesPass && windowSpeeds != null,
                ),
            )
        }
        if (!ignoreStale) {
            navigate(registration, cues, aligned && poseFresh)
        }
        noteDwell(
            before = beforeDwell,
            after = dwellState,
            ageMs = ageMs,
            poseFresh = poseFresh,
            ignoreStale = ignoreStale,
            calibrated = calibrated,
            aligned = aligned,
            registration = registration,
            quality = quality,
            speeds = windowSpeeds,
            cues = cues.size,
        )
        lastEstimate = estimate
        lastMotion = motion
        _state.update {
            it.copy(
                registration = registration, wireReason = wireReason,
                poseAgeMs = ageMs, rmsPx = estimate.rmsPx,
                cornersPx = estimate.cornersPx, markersSeen = estimate.markerIdsSeen,
                dwell = dwellState,
                cues = cues, orientationOk = orientationOk, distanceOk = distanceOk,
                qualityVerdict = quality,
                motionWarning = motionWarning,
                measuredSharpness = estimate.frameQuality?.sharpness,
            )
        }
        maybeReserveAttempt()
        maybeFireShutter()
    }

    private fun navigate(registration: RegistrationState, cues: List<MovementCue>, aligned: Boolean) {
        val request = _state.value.request
        if (registration != RegistrationState.ACQUIRED) {
            if (request == RequestState.NAVIGATING || request == RequestState.ALIGNING ||
                request == RequestState.HOLD_STILL
            ) {
                onRequestEvent(RequestEvent.GateFailed)
            }
            return
        }
        when (request) {
            RequestState.ACCEPTED -> onRequestEvent(RequestEvent.EnterNavigating)
            RequestState.NAVIGATING -> if (cues.size <= 2) onRequestEvent(RequestEvent.EnterAligning)
            RequestState.ALIGNING -> {
                if (aligned) onRequestEvent(RequestEvent.GatesPass)
                else if (cues.size > 4) onRequestEvent(RequestEvent.EnterNavigating)
            }
            RequestState.HOLD_STILL -> if (!aligned) onRequestEvent(RequestEvent.GateFailed)
            else -> Unit
        }
    }

    private fun noteDwell(
        before: DwellState,
        after: DwellState,
        ageMs: Long,
        poseFresh: Boolean,
        ignoreStale: Boolean,
        calibrated: Boolean,
        aligned: Boolean,
        registration: RegistrationState,
        quality: ImageQualityVerdict,
        speeds: SpeedTracker.Speeds?,
        cues: Int,
    ) {
        val request = _state.value.request
        if (request in TERMINAL_REQUESTS || _state.value.session != SessionState.ACTIVE) return
        armFrames++
        if (poseFresh) armFresh++
        val samples = dwellSamples(after)
        val dropped = dwellSamples(before) > 0 && samples < dwellSamples(before)
        val reason = dwellBlockReason(
            ignoreStale, calibrated, aligned, registration, quality, poseFresh, ageMs, speeds, cues,
        )
        if (dropped || samples != lastDwellSamples || armFrames % 20 == 0) {
            lastDwellSamples = samples
            logArm(
                "dwell id=${_state.value.requestId} session=${_state.value.session} request=$request " +
                    "consumed=$firedForArming track=$registration cal=$calibrated align=$aligned " +
                    "quality=$quality age=${ageMs}ms fresh=$armFresh/$armFrames " +
                    "dwell=${dwellText(after)} reason=$reason cues=$cues " +
                    "target=${_state.value.target?.id ?: "none"} board=${_state.value.boardId}",
            )
        }
    }

    private fun dwellBlockReason(
        ignoreStale: Boolean,
        calibrated: Boolean,
        aligned: Boolean,
        registration: RegistrationState,
        quality: ImageQualityVerdict,
        poseFresh: Boolean,
        ageMs: Long,
        speeds: SpeedTracker.Speeds?,
        cues: Int,
    ): String = when {
        _state.value.session != SessionState.ACTIVE -> "not_armed"
        _state.value.target == null -> "no_target"
        registration != RegistrationState.ACQUIRED -> "tracking_$registration"
        !calibrated -> "uncalibrated"
        !aligned && cues > 0 -> "off_target"
        !aligned -> "quality_$quality"
        ignoreStale || !poseFresh -> "stale_age_${ageMs}"
        speeds == null -> "no_speed"
        speeds.speedMmS >= GuidanceHypothesis1.MAX_SPEED_MM_S -> "speed_${speeds.speedMmS}"
        speeds.angularSpeedDegS >= GuidanceHypothesis1.MAX_ANGULAR_SPEED_DEG_S -> "angular_${speeds.angularSpeedDegS}"
        else -> "counting"
    }

    private fun logArm(message: String) = armLog(message)

    private fun dwellSamples(state: DwellState): Int = when (state) {
        DwellState.Idle -> 0
        is DwellState.Accumulating -> state.samples
        DwellState.Ready -> HackathonMvp.DWELL_SAMPLES
    }

    private fun dwellText(state: DwellState): String = when (state) {
        DwellState.Idle -> "idle"
        is DwellState.Accumulating -> "${state.samples}/${HackathonMvp.DWELL_SAMPLES} ${state.elapsedMs}ms"
        DwellState.Ready -> "ready"
    }

    /** GUIDE-01 diagnostics: throttled UI-side age/gap summary, else null. */
    private fun recordUiTiming(nowNs: Long, ageMs: Long, estimate: PoseEstimator.Estimate) {
        val gapMs = lastUiEstimateNs?.let { (nowNs - it) / 1_000_000.0 } ?: 0.0
        lastUiEstimateNs = nowNs
        val summary = timing.recordUi(ageMs, gapMs) ?: return
        val t = estimate.tBC?.let { translationOfRowMajor(it) }
        val pose = if (t == null) "pose=none" else
            "rms=${"%.2f".format(java.util.Locale.US, estimate.rmsPx ?: Double.NaN)} " +
                "t=${"%.1f,%.1f,%.1f".format(java.util.Locale.US, t.x, t.y, t.z)} status=${estimate.status} " +
                "profile=${_state.value.intrinsicsLabel}"
        Log.i(DIAG_LOG_TAG, "guided $summary $pose")
    }

    private fun motionStats(estimate: PoseEstimator.Estimate): Pair<Double, Double>? {
        val current = mutableMapOf<String, List<Double>>()
        for (marker in estimate.cornersPx) {
            for ((index, corner) in marker.corners.withIndex()) {
                current["${marker.id}:$index"] = corner
            }
        }
        val previousNs = lastCornersNs
        val previous = lastCorners
        lastCorners = current
        lastCornersNs = estimate.monotonicNs
        if (previousNs == null || previous.isEmpty() || current.isEmpty()) return null
        val dtMs = (estimate.monotonicNs - previousNs) / 1_000_000.0
        if (dtMs <= 0.0 || dtMs > 1000.0) return null
        val displacements = mutableListOf<Double>()
        for ((key, corner) in current) {
            val old = previous[key] ?: continue
            val dx = corner[0] - old[0]
            val dy = corner[1] - old[1]
            displacements += kotlin.math.sqrt(dx * dx + dy * dy) * (100.0 / dtMs)
        }
        if (displacements.isEmpty()) return null
        val sorted = displacements.sorted()
        val median = sorted[sorted.size / 2]
        val p90 = sorted[((sorted.size * 0.9).toInt()).coerceAtMost(sorted.size - 1)]
        return median to p90
    }

    private fun maybeFireShutter() {
        val snapshot = _state.value
        if (snapshot.request != RequestState.HOLD_STILL || snapshot.dwell != DwellState.Ready) {
            if (snapshot.holdReason != null) _state.update { it.copy(holdReason = null) }
            return
        }
        // Remote requests additionally need a reserved attempt; the
        // dwell keeps accumulating while the reservation lands.
        if (snapshot.remoteAttached && snapshot.remoteRequest != null && !snapshot.attemptReady) {
            return
        }
        val decision = ShutterGate.decide(
            ShutterGate.Input(
                session = snapshot.session, request = snapshot.request,
                lateMark = snapshot.lateMark, registration = snapshot.registration,
                poseAgeMs = snapshot.poseAgeMs, dwell = snapshot.dwell,
                orientationOk = snapshot.orientationOk, distanceOk = snapshot.distanceOk,
                qualityOk = snapshot.qualityVerdict == ImageQualityVerdict.Accepted ||
                    snapshot.motionWarning,
                boardId = snapshot.boardId, targetBoardId = snapshot.target?.boardId,
                profileId = snapshot.intrinsicsLabel,
                requiredProfileId = null,
                grantLive = snapshot.session == SessionState.ACTIVE,
                foreground = snapshot.foreground,
                alreadyFiredForArming = firedForArming,
            ),
        )
        when (decision) {
            is ShutterGate.Decision.Fire -> {
                _state.update { it.copy(holdReason = null) }
                logArm(
                    "shutter requested id=${snapshot.requestId} age=${snapshot.poseAgeMs} " +
                        "dwell=${dwellText(snapshot.dwell)} consumed=$firedForArming",
                )
                fireAutomatic()
            }
            is ShutterGate.Decision.Hold -> {
                _state.update { it.copy(holdReason = decision.reason) }
                if (decision.reason.name != lastHoldReason) {
                    lastHoldReason = decision.reason.name
                    logArm(
                        "shutter hold id=${snapshot.requestId} reason=${decision.reason.name} " +
                            "request=${snapshot.request} session=${snapshot.session} " +
                            "fg=${snapshot.foreground} age=${snapshot.poseAgeMs} consumed=$firedForArming",
                    )
                }
            }
        }
    }

    private fun fireAutomatic() {
        if (_state.value.shutterBusy) return
        _state.update { it.copy(shutterBusy = true, captureError = null) }
        viewModelScope.launch {
            shutterMutex.withLock {
                if (firedForArming) {
                    _state.update { it.copy(shutterBusy = false) }
                    return@withLock
                }
                val transition = reduceRequest(_state.value.request, RequestEvent.DwellReady, _state.value.lateMark)
                _state.update { it.copy(request = transition.state, lateMark = transition.lateMark) }
                if (!transition.shutterCommitted) {
                    _state.update { it.copy(shutterBusy = false) }
                    return@withLock
                }
                firedForArming = true
                try {
                    val source = camera ?: throw IllegalStateException("camera detached")
                    val still = source.takeGuidedStill()
                    lastStillCallbackNs = nanoClock()
                    stillEstimate = lastEstimate
                    val stored = persistStill(still, automatic = true)
                    onRequestEvent(RequestEvent.JpegPersisted)
                    onRequestEvent(RequestEvent.VerifyPass)
                    _state.update { it.copy(shutterBusy = false, captureResult = stored) }
                    logArm(
                        "shutter completed id=${_state.value.requestId} request=${_state.value.request} " +
                            "sha=${stored.sha256.take(16)} image=${stored.imageId} consumed=$firedForArming",
                    )
                } catch (e: Exception) {
                    onRequestEvent(RequestEvent.JpegPersisted)
                    onRequestEvent(RequestEvent.VerifyFail)
                    _state.update { it.copy(shutterBusy = false, captureError = e.message ?: "capture failed") }
                    logArm(
                        "shutter failed id=${_state.value.requestId} request=${_state.value.request} " +
                            "error=${e.message} consumed=$firedForArming",
                    )
                }
            }
        }
    }

    private suspend fun persistStill(
        still: org.anastylos.capture.camera.GuidedCameraSource.GuidedStill,
        automatic: Boolean,
    ): CaptureResult = withContext(ioDispatcher) {
        val id = ensureSession()
        captureSequence += 1
        val stored = sampler.adoptFrame(
            id,
            CapturedStill(bytes = still.bytes, capturedAt = still.capturedAt),
            captureSequence,
        )
        val sha = sha256Hex(still.bytes)
        if (_state.value.remoteAttached) {
            recordRemoteCapture(stored, still, sha)
        }
        CaptureResult(
            observationId = stored.observationId,
            imageId = stored.imageId,
            sha256 = sha,
            automatic = automatic,
            associationNote = still.associationNote,
        )
    }

    private suspend fun recordRemoteCapture(
        stored: SamplerEvent.FrameStored,
        still: org.anastylos.capture.camera.GuidedCameraSource.GuidedStill,
        sha: String,
    ) {
        val assocRepo = associations ?: return
        val sessionUuid = _state.value.remoteSessionId ?: return
        val request = _state.value.remoteRequest
        val assoc = GuidedAssociation(
            imageId = stored.imageId,
            observationId = stored.observationId,
            packageId = null,
            guidedSessionId = sessionUuid,
            requestId = request?.requestId,
            requestRevision = request?.revision,
            attemptId = reservedAttemptId,
            attemptNumber = reservedAttemptNumber,
            sidecarRevision = 0,
            deliveryState = DeliveryState.PENDING_PACKAGE,
            receiptStatus = null,
            updatedAt = utcClock(),
        )
        try {
            assocRepo.recordGuidedAssociation(assoc)
        } catch (e: Exception) {
            _state.update { it.copy(deliveryNote = "association not recorded: ${e.message}") }
            return
        }
        _state.update { it.copy(delivery = assoc, deliveryNote = null) }
        if (reservedAttemptId != null) postRemoteEvent("captured_local", attemptId = reservedAttemptId)
        val delivery = remote?.delivery ?: return
        viewModelScope.launch {
            runDelivery(delivery, stored.imageId, stored.observationId, still, sha)
        }
    }

    private suspend fun runDelivery(
        delivery: org.anastylos.capture.sync.GuidedDelivery,
        imageId: String,
        observationId: String,
        still: org.anastylos.capture.camera.GuidedCameraSource.GuidedStill,
        imageSha: String,
    ) {
        val sessionUuid = _state.value.remoteSessionId ?: return
        val legacy = pairingStore?.load()?.token
        if (legacy.isNullOrBlank()) {
            _state.update { it.copy(deliveryNote = "not paired; delivery deferred (evidence is local)") }
            return
        }
        val guided = when (val resolved = remote?.session?.resolveToken()) {
            is GuidedTokenOutcome.Ready -> resolved.token
            is GuidedTokenOutcome.NeedsEnrollment ->
                return noteDelivery("device not enrolled: ${resolved.message}")
            is GuidedTokenOutcome.ExchangeSpent ->
                return noteDelivery("enrollment spent: ${resolved.message}")
            is GuidedTokenOutcome.Unauthorized ->
                return noteDelivery("guided token rejected")
            is GuidedTokenOutcome.Failed, null ->
                return noteDelivery(
                    (resolved as? GuidedTokenOutcome.Failed)?.message ?: "guided token unavailable",
                )
        }
        val sidecarInput = assembleSidecarInput(imageId, observationId, still, imageSha)
        val outcome = try {
            delivery.deliver(DeliveryInput(imageId, legacy, guided, sessionUuid, sidecarInput))
        } catch (e: Exception) {
            return noteDelivery("delivery failed: ${e.message}")
        }
        val refreshed = try {
            associations?.getGuidedAssociation(imageId)
        } catch (_: Exception) {
            null
        }
        when (outcome) {
            is DeliveryOutcome.Delivered -> _state.update {
                it.copy(
                    delivery = refreshed ?: it.delivery,
                    deliveryNote = describeDelivery(outcome),
                )
            }
            is DeliveryOutcome.PackageRejected -> _state.update {
                it.copy(delivery = refreshed ?: it.delivery, deliveryNote = "package rejected: ${outcome.reason}")
            }
            is DeliveryOutcome.UploadFailed -> _state.update {
                it.copy(delivery = refreshed ?: it.delivery, deliveryNote = "upload failed: ${outcome.reason}")
            }
            is DeliveryOutcome.BindingFailed -> _state.update {
                it.copy(delivery = refreshed ?: it.delivery, deliveryNote = "binding failed: ${outcome.reason}")
            }
            is DeliveryOutcome.SidecarFailed -> _state.update {
                it.copy(delivery = refreshed ?: it.delivery, deliveryNote = "sidecar failed: ${outcome.reason}")
            }
            is DeliveryOutcome.AuthLost -> _state.update {
                it.copy(deliveryNote = "auth lost: ${outcome.reason}")
            }
            is DeliveryOutcome.Blocked -> _state.update {
                it.copy(deliveryNote = "delivery blocked: ${outcome.reason}")
            }
        }
    }

    private fun noteDelivery(note: String) {
        _state.update { it.copy(deliveryNote = note) }
    }

    private fun describeDelivery(outcome: DeliveryOutcome.Delivered): String {
        val parts = mutableListOf("delivery: ${outcome.deliveryState}")
        outcome.receiptStatus?.let { parts += "receipt ${it.wire}" }
        if (outcome.sidecarUncertain) parts += "sidecar uncertain (revision conflict; awaiting receipt)"
        outcome.sidecarSkippedReason?.let { parts += "sidecar skipped: $it" }
        return parts.joinToString("; ")
    }

    /**
     * Assembles the sidecar input from real capture measurements, or
     * null when the sidecar must be skipped (no estimate, unknown
     * board, unreadable still). Every field traces to a measured
     * value; nominal derivations are marked by the builder.
     */
    private suspend fun assembleSidecarInput(
        imageId: String,
        observationId: String,
        still: org.anastylos.capture.camera.GuidedCameraSource.GuidedStill,
        imageSha: String,
    ): SpatialSidecar.Input? {
        val estimate = stillEstimate ?: return null
        val snapshot = _state.value
        val sessionUuid = snapshot.remoteSessionId ?: return null
        val request = snapshot.remoteRequest ?: return null
        val geometry = snapshot.overlayGeometry ?: return null
        val epoch = bindEpoch ?: return null
        val board = BoardGeometry.forBoardId(snapshot.boardId) ?: return null
        val observation = try {
            repo.getObservation(observationId)
        } catch (_: Exception) {
            null
        } ?: return null
        val bounds = try {
            BitmapFactory.Options().apply { inJustDecodeBounds = true }.also {
                BitmapFactory.decodeByteArray(still.bytes, 0, still.bytes.size, it)
            }
        } catch (_: Exception) {
            return null
        }
        if (bounds.outWidth < 1 || bounds.outHeight < 1) return null
        val exifOrientation = try {
            val raw = ExifInterface(java.io.ByteArrayInputStream(still.bytes))
                .getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
            if (raw in 1..8) raw else ExifInterface.ORIENTATION_NORMAL
        } catch (_: Exception) {
            ExifInterface.ORIENTATION_NORMAL
        }
        val callbackNs = lastStillCallbackNs ?: return null
        val sampleAgeMs = (callbackNs - estimate.monotonicNs) / 1_000_000.0
        val quality = estimate.frameQuality
        val motion = lastMotion
        val intrinsics = try {
            camera?.currentIntrinsics()
        } catch (_: Exception) {
            null
        }
        val nominalDescriptor = intrinsics?.let {
            "factory-fx${it.fx}-fy${it.fy}-cx${it.cx}-cy${it.cy}-w${it.width}-h${it.height}-dist${it.distortion.joinToString(",")}"
        } ?: "unresolved-factory-analysis-${geometry.analysisWidth}x${geometry.analysisHeight}"
        val layoutDescriptor = canonicalBoardLayout(board)
        val goalBytes = try {
            Json.encodeToString(GuidedTarget.serializer(), request.target).toByteArray(Charsets.UTF_8)
        } catch (_: Exception) {
            return null
        }
        return SpatialSidecar.Input(
            evidenceId = uuid(),
            sessionId = sessionUuid,
            sourceId = request.sourceId,
            requestId = request.requestId,
            requestRevision = request.revision,
            attemptId = reservedAttemptId ?: return null,
            createdAt = utcClock().toIsoUtc(),
            image = SpatialSidecar.Image(
                packageId = framePackageId(observation),
                observationId = observationId,
                imageId = imageId,
                assetId = imageId,
                sha256 = imageSha,
                byteLength = still.bytes.size.toLong(),
                mime = "image/jpeg",
                widthPx = bounds.outWidth,
                heightPx = bounds.outHeight,
                exifOrientation = exifOrientation,
            ),
            camera = SpatialSidecar.Camera(
                cameraId = cameraIdForConsent(),
                lensFacing = when (geometry.lensFacing) {
                    CameraSelector.LENS_FACING_FRONT -> "front"
                    LENS_FACING_EXTERNAL -> "external"
                    else -> "back"
                },
                sensorMode = "default",
            ),
            calibration = SpatialSidecar.Calibration(
                nominalDescriptor = nominalDescriptor,
                layoutSha256 = layoutDescriptor.toByteArray(Charsets.UTF_8).sha256Hex(),
                boardFrameId = board.boardId,
                metricValidity = "nominal_only",
            ),
            analysisWidthPx = geometry.analysisWidth,
            analysisHeightPx = geometry.analysisHeight,
            analysisRotationDeg = geometry.analysisRotationDeg,
            transformEpoch = epoch,
            captureTime = SpatialSidecar.CaptureTime(
                callbackUtc = still.capturedAt.toIsoUtc(),
                callbackMonotonicNs = callbackNs.toString(),
                bootId = bootAnchorId,
            ),
            pose = SpatialSidecar.Pose(
                tBC = estimate.tBC,
                estimatorStatus = estimate.status,
                rmsPx = estimate.rmsPx,
                detectedMarkerIds = estimate.markerIdsSeen,
                residualReferenceWidthPx = geometry.analysisWidth,
                sampleAgeMs = sampleAgeMs,
                imageSha256 = imageSha,
            ),
            calibrated = false,
            target = SpatialSidecar.Target(
                goalBytes = goalBytes,
                roiVerticesMm = boardRoiCorners(board),
                objectPoseEpochRef = request.objectPoseEpochId,
            ),
            quality = SpatialSidecar.Quality(
                rmsPx = estimate.rmsPx,
                rmsGatePx = GuidanceHypothesis1.LIVE_RMS_PX,
                motionMedianPx = motion?.first,
                motionP90Px = motion?.second,
                motionMedianGatePx = GuidanceHypothesis1.MAX_MOTION_MEDIAN_PX,
                motionP90GatePx = GuidanceHypothesis1.MAX_MOTION_P90_PX,
                sharpness = quality?.sharpness,
                sharpnessReference = snapshot.sharpnessReference,
                sharpnessRatioGate = GuidanceHypothesis1.MIN_SHARPNESS_RATIO,
                darkFraction = quality?.darkFraction,
                saturatedFraction = quality?.saturatedFraction,
                badExposureGate = GuidanceHypothesis1.MAX_BAD_EXPOSURE_FRAC,
                visibility = if (estimate.markerIdsSeen.isNotEmpty()) "visible" else "unknown",
                inFrame = estimate.cornersPx.isNotEmpty(),
                thresholdConfigJson = THRESHOLD_CONFIG_JSON,
            ),
            appVersion = appVersion,
            buildSha = appVersion,
            algorithmConfigJson = ALGORITHM_CONFIG_JSON
                .replace("{dict}", board.dictionary)
                .replace("{w}", geometry.analysisWidth.toString())
                .replace("{h}", geometry.analysisHeight.toString())
                .replace("{roi}", quality?.exposureRoi ?: "unmeasured"),
        )
    }

    private fun canonicalBoardLayout(board: BoardGeometry.Board): String =
        buildString {
            append("anastylos-board-layout:1:${board.boardId}:${board.dictionary}:")
            board.markers.toSortedMap().forEach { (id, marker) ->
                append("$id:${marker.sizeMm}:")
                marker.corners.forEach { corner -> append("${corner[0]},${corner[1]};") }
                append("|")
            }
        }.toString()

    private fun boardRoiCorners(board: BoardGeometry.Board): List<List<Double>> {
        val xs = board.markers.values.flatMap { marker -> marker.corners.map { it[0] } }
        val ys = board.markers.values.flatMap { marker -> marker.corners.map { it[1] } }
        if (xs.isEmpty() || ys.isEmpty()) return listOf(listOf(0.0, 0.0, 0.0))
        val minX = xs.min()
        val maxX = xs.max()
        val minY = ys.min()
        val maxY = ys.max()
        return listOf(
            listOf(minX, minY, 0.0),
            listOf(maxX, minY, 0.0),
            listOf(maxX, maxY, 0.0),
            listOf(minX, maxY, 0.0),
        )
    }

    private suspend fun ensureSession(): String {
        sessionId?.let { return it }
        // One dedicated session per guidance run: explicit and auditable.
        val id = withContext(ioDispatcher) { repo.createSession("Guided capture").sessionId }
        sessionId = id
        return id
    }

    override fun onCleared() {
        detachCamera()
        remoteCollectJob?.cancel()
        try {
            remote?.session?.close()
        } catch (_: Exception) {
        }
        remote = null
    }
}

private const val ARM_LOG = "AnastylosArm"

private val TERMINAL_REQUESTS = setOf(
    RequestState.CAPTURED_LOCAL,
    RequestState.CANCELLED,
    RequestState.EXPIRED,
    RequestState.SUPERSEDED,
)

/** Canonical bytes hashed into sidecar `threshold_config_sha256`. */
private const val THRESHOLD_CONFIG_JSON =
    """{"profile":"guidance-hypothesis-1","rms_px":1.2,"motion_median_px":0.7,"motion_p90_px":1.5,"sharpness_ratio":0.7,"bad_exposure_frac":0.05,"min_markers":3}"""

/** Canonical solver config; `{dict}`, `{w}`, `{h}`, `{roi}` are substituted per capture. */
private const val ALGORITHM_CONFIG_JSON =
    """{"solver":"aruco-solvepnp-iterative","opencv":"4.14.0","dictionary":"{dict}","analysis":"{w}x{h}","exposure_roi":"{roi}"}"""

/**
 * Mirrors `CameraSelector.LENS_FACING_EXTERNAL` without referencing
 * the experimental constant (lint cannot see the opt-in through the
 * inlined Java constant). The 0/1/2 facing values are stable CameraX API.
 */
private const val LENS_FACING_EXTERNAL = 2

private fun sha256Hex(bytes: ByteArray): String {
    val digest = java.security.MessageDigest.getInstance("SHA-256")
    return digest.digest(bytes).joinToString("") { "%02x".format(it) }
}
