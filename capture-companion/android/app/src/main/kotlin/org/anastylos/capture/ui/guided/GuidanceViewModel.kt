package org.anastylos.capture.ui.guided

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
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.anastylos.capture.camera.CapturedStill
import org.anastylos.capture.camera.FrameSampler
import org.anastylos.capture.camera.GuidedCamera
import org.anastylos.capture.data.EvidenceRepository
import org.anastylos.capture.data.GuidedPrefs
import org.anastylos.capture.guidance.BoardGeometry
import org.anastylos.capture.guidance.CameraPoseB
import org.anastylos.capture.guidance.CueAxis
import org.anastylos.capture.guidance.DwellAccumulator
import org.anastylos.capture.guidance.DwellState
import org.anastylos.capture.guidance.GuidanceHypothesis1
import org.anastylos.capture.guidance.ImageQualityInput
import org.anastylos.capture.guidance.ImageQualityVerdict
import org.anastylos.capture.guidance.LateMark
import org.anastylos.capture.guidance.MovementCue
import org.anastylos.capture.guidance.PhoneRotation
import org.anastylos.capture.guidance.PoseEstimator
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.RequestEvent
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionEvent
import org.anastylos.capture.guidance.SessionState
import org.anastylos.capture.guidance.ShutterGate
import org.anastylos.capture.guidance.SpeedTracker
import org.anastylos.capture.guidance.StabilitySample
import org.anastylos.capture.guidance.Vec3
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

/** One local target viewpoint preset (Core-provided targets arrive in Phase E). */
data class TargetViewpoint(
    val id: String,
    val label: String,
    val boardId: String,
    val pose: CameraPoseB,
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
    val overlayGeometry: GuidedCamera.StreamGeometry? = null,
    val qualityVerdict: ImageQualityVerdict = ImageQualityVerdict.Unknown,
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
    private val nanoClock: () -> Long,
    private val utcClock: () -> Instant,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {
    private val _state = MutableStateFlow(GuidanceUiState())
    val state: StateFlow<GuidanceUiState> = _state.asStateFlow()

    private var camera: GuidedCamera? = null
    private var estimatesJob: Job? = null
    private val dwell = DwellAccumulator()
    private val speeds = SpeedTracker()
    private val shutterMutex = Mutex()
    private var armingToken: String? = null
    private var firedForArming = false
    private var captureSequence = 0L
    private var sessionId: String? = null
    private var lastCorners: Map<String, List<Double>> = emptyMap()
    private var lastCornersNs: Long? = null

    init {
        viewModelScope.launch {
            prefs.sharpnessReference.collect { reference ->
                _state.update { it.copy(sharpnessReference = reference) }
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

    fun attachCamera(owner: LifecycleOwner, preview: PreviewView?) {
        if (camera != null) {
            camera?.attachPreview(preview)
            return
        }
        val source = cameraFactory(owner, preview)
        camera = source
        viewModelScope.launch {
            val bound = try {
                source.bindAsync()
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
                )
            }
            estimatesJob?.cancel()
            estimatesJob = viewModelScope.launch {
                source.estimates.collect { estimate -> onEstimate(estimate) }
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

    fun selectTarget(target: TargetViewpoint?) {
        if (target != null && target.boardId != _state.value.boardId) {
            selectBoard(target.boardId)
        }
        _state.update { it.copy(target = target) }
    }

    fun arm() {
        // Fresh session arming from DISARMED.
        if (_state.value.session == SessionState.DISARMED) {
            val transition = onSessionEvent(
                SessionEvent.Arm(
                    grantLive = true,
                    permissionGranted = _state.value.permissionGranted,
                    foreground = _state.value.foreground,
                ),
            )
            if (!transition.accepted) return
            startRequest()
            viewModelScope.launch {
                ensureSession()
            }
            return
        }
        // Next request inside a live session: allowed only once the
        // previous request reached a terminal state, with a new token.
        if (_state.value.session == SessionState.ACTIVE && _state.value.request in TERMINAL_REQUESTS) {
            startRequest()
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

    private fun startRequest() {
        // A new arming token starts a new request lifecycle (a fresh
        // reducer): the token binds exactly one request, so a repeat
        // capture after CAPTURED_LOCAL never inherits the previous
        // request's state.
        armingToken = UUID.randomUUID().toString()
        firedForArming = false
        val requestId = "local-${armingToken!!.take(8)}"
        _state.update {
            it.copy(
                request = RequestState.QUEUED, lateMark = null,
                requestId = requestId, requestRevision = 1, captureResult = null,
            )
        }
        onRequestEvent(RequestEvent.Accept)
    }

    fun disarm() {
        onSessionEvent(SessionEvent.OperatorDisarm)
        armingToken = null
        firedForArming = false
    }

    fun pause() {
        onSessionEvent(SessionEvent.OperatorPause)
    }

    fun resume() {
        onSessionEvent(
            SessionEvent.Resume(
                grantLive = true,
                registrationFresh = _state.value.registration == RegistrationState.ACQUIRED,
                foreground = _state.value.foreground,
            ),
        )
    }

    fun cancelRequest() {
        onRequestEvent(RequestEvent.Cancel)
    }

    fun skipTarget() {
        onRequestEvent(RequestEvent.Skip)
    }

    fun cantReach() {
        onRequestEvent(RequestEvent.CantReach)
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
                Vec3(
                    GuidanceHypothesis1.POS_TOL_X_MM,
                    GuidanceHypothesis1.POS_TOL_Y_MM,
                    GuidanceHypothesis1.POS_TOL_Z_MM,
                ),
                GuidanceHypothesis1.ANG_TOL_DEG,
            )
        } else {
            emptyList()
        }
        val orientationOk = cues.none { it.axis == CueAxis.YAW || it.axis == CueAxis.PITCH || it.axis == CueAxis.ROLL }
        val distanceOk = cues.none { it.axis == CueAxis.LATERAL || it.axis == CueAxis.VERTICAL || it.axis == CueAxis.DEPTH }
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
        val qualityOk = quality == ImageQualityVerdict.Accepted
        val gatesPass = _state.value.session == SessionState.ACTIVE &&
            registration == RegistrationState.ACQUIRED &&
            orientationOk && distanceOk && qualityOk
        val windowSpeeds = if (pose != null && estimate.tBC != null) {
            speeds.observe(
                estimate.monotonicNs / 1_000_000L,
                pose.centerMm,
                rotationOfRowMajor(estimate.tBC),
            )
        } else {
            speeds.reset()
            null
        }
        val dwellState = dwell.observe(
            StabilitySample(
                tMs = estimate.monotonicNs / 1_000_000L,
                speedMmS = windowSpeeds?.speedMmS ?: Double.MAX_VALUE,
                angularSpeedDegS = windowSpeeds?.angularSpeedDegS ?: Double.MAX_VALUE,
                poseAgeMs = ageMs,
                gatesPass = gatesPass && windowSpeeds != null,
            ),
        )
        navigate(registration, cues, orientationOk && distanceOk && qualityOk)
        _state.update {
            it.copy(
                registration = registration, wireReason = wireReason,
                poseAgeMs = ageMs, rmsPx = estimate.rmsPx,
                cornersPx = estimate.cornersPx, dwell = dwellState,
                cues = cues, orientationOk = orientationOk, distanceOk = distanceOk,
                qualityVerdict = quality,
                measuredSharpness = estimate.frameQuality?.sharpness,
            )
        }
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
        val decision = ShutterGate.decide(
            ShutterGate.Input(
                session = snapshot.session, request = snapshot.request,
                lateMark = snapshot.lateMark, registration = snapshot.registration,
                poseAgeMs = snapshot.poseAgeMs, dwell = snapshot.dwell,
                orientationOk = snapshot.orientationOk, distanceOk = snapshot.distanceOk,
                qualityOk = snapshot.qualityVerdict == ImageQualityVerdict.Accepted,
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
                fireAutomatic()
            }
            is ShutterGate.Decision.Hold -> {
                _state.update { it.copy(holdReason = decision.reason) }
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
                    val stored = persistStill(still, automatic = true)
                    onRequestEvent(RequestEvent.JpegPersisted)
                    onRequestEvent(RequestEvent.VerifyPass)
                    _state.update { it.copy(shutterBusy = false, captureResult = stored) }
                } catch (e: Exception) {
                    onRequestEvent(RequestEvent.JpegPersisted)
                    onRequestEvent(RequestEvent.VerifyFail)
                    _state.update { it.copy(shutterBusy = false, captureError = e.message ?: "capture failed") }
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
        CaptureResult(
            observationId = stored.observationId,
            imageId = stored.imageId,
            sha256 = sha,
            automatic = automatic,
            associationNote = still.associationNote,
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
    }
}

private val TERMINAL_REQUESTS = setOf(
    RequestState.CAPTURED_LOCAL,
    RequestState.CANCELLED,
    RequestState.EXPIRED,
    RequestState.SUPERSEDED,
)

private fun sha256Hex(bytes: ByteArray): String {
    val digest = java.security.MessageDigest.getInstance("SHA-256")
    return digest.digest(bytes).joinToString("") { "%02x".format(it) }
}
