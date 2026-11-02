package org.anastylos.capture.ui.guided

import android.Manifest
import android.content.pm.PackageManager
import android.view.Surface
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.Alignment
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import org.anastylos.capture.voice.VoiceController
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.viewmodel.compose.viewModel
import org.anastylos.capture.di.AppContainer
import org.anastylos.capture.guidance.PhoneRotation
import org.anastylos.capture.guidance.ImageQualityVerdict
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.SessionState
import org.anastylos.capture.ui.camera.CameraPicker
import org.anastylos.capture.camera.SETTING_PROFILES
import org.anastylos.capture.ui.camera.CameraSettingsPanel

/**
 * Operational Guided Capture screen: live preview with detection
 * overlay, tracking/request/dwell states, direction cues, arming
 * controls and a reliable manual fallback. Functional product UI,
 * not a mockup — and deliberately not a visual redesign.
 */
@Composable
fun GuidedCaptureScreen(
    container: AppContainer,
    onBack: () -> Unit,
    viewModel: GuidanceViewModel = viewModel(factory = container.guidedViewModelFactory()),
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val state by viewModel.state.collectAsState()
    // Per-pose fields change at the analysis rate; sections that never
    // read them get an equal-while-unchanged copy so Compose skips them.
    // GUIDE-01: recomposing the whole screen per pose saturated the
    // main thread (~70 ms frames) and aged every pose before the gates.
    val coldKey = state.withoutPoseFields()
    val cold = remember(coldKey) { coldKey }
    // FIT_CENTER: the overlay maps analysis pixels onto the view
    // analytically (OverlayMapping) — no crop math to get wrong.
    val previewView = remember {
        PreviewView(context).apply { scaleType = PreviewView.ScaleType.FIT_CENTER }
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> viewModel.setPermissionGranted(granted) }
    LaunchedEffect(Unit) {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        viewModel.setPermissionGranted(granted)
        if (!granted) permissionLauncher.launch(Manifest.permission.CAMERA)
    }

    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> viewModel.setForeground(true)
                Lifecycle.Event.ON_PAUSE -> viewModel.setForeground(false)
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    LaunchedEffect(previewView) {
        val rotation = when (previewView.display?.rotation) {
            Surface.ROTATION_90 -> PhoneRotation.R90
            Surface.ROTATION_180 -> PhoneRotation.R180
            Surface.ROTATION_270 -> PhoneRotation.R270
            else -> PhoneRotation.R0
        }
        viewModel.setPhoneRotation(rotation)
        viewModel.attachCamera(lifecycleOwner, previewView)
    }
    DisposableEffect(Unit) {
        onDispose { viewModel.detachCamera() }
    }

    val voiceState by container.voice.state.collectAsState()
    // The label is workstation-authored request text, spoken verbatim
    // through the voice channel only when voice is on; never model text.
    val requestId = cold.remoteRequest?.requestId
    LaunchedEffect(requestId) {
        val label = cold.remoteRequest?.targetLabel
        if (requestId != null && !label.isNullOrBlank()) container.voice.speakVerified(label)
    }
    var details by rememberSaveable { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedButton(onClick = onBack) { Text("Back") }
            Spacer(Modifier.weight(1f))
            StatusPill(
                if (cold.remoteAttached) "Connected" else "Not connected",
                if (cold.remoteAttached) PillGreen else PillRed,
            )
            voiceLabelOf(voiceState)?.let { StatusPill(it, PillBlue) }
        }
        StatusBannerView(statusBannerOf(state))
        Box(modifier = Modifier.fillMaxWidth()) {
            AndroidView(
                factory = { previewView },
                modifier = Modifier.fillMaxWidth(),
            )
            DetectionOverlay(state, Modifier.matchParentSize())
        }
        if (cold.remoteRequest == null) CueList(state)
        MainControls(cold, viewModel)
        VoiceToggle(container.voice, voiceState)
        state.cameraError?.let { Text("Camera: $it", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.error) }
        state.captureError?.let { Text("Capture: $it", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.error) }
        state.remoteError?.let { error ->
            Text(error, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.error)
            OutlinedButton(onClick = { viewModel.clearRemoteError() }) { Text("Dismiss") }
        }
        OutlinedButton(onClick = { details = !details }) {
            Text(if (details) "Hide details" else "Details")
        }
        if (details) {
            if (state.uncalibrated) {
                Text(
                    "UNCALIBRATED: factory intrinsics (${state.intrinsicsLabel ?: "none"}). " +
                        "Poses are coarse; metric evidence is disabled until calibration.",
                    color = MaterialTheme.colorScheme.error,
                )
                cold.calibrationBlocker?.let { reason ->
                    Text("Calibrated profile not applied: $reason")
                    Button(onClick = { viewModel.applyCalibratedOptics() }) {
                        Text("Use calibrated optics")
                    }
                }
            }
            StateGrid(state)
            state.sessionReason?.let { Text("Session note: $it") }
            RemoteDetails(cold)
            ResultDetails(cold, viewModel)
            DetailControls(cold, viewModel)
            VoiceDetails(container.voice, voiceState)
            TargetPicker(cold, viewModel)
            CameraPicker(
                cameras = cold.availableCameras,
                effectiveId = cold.effectiveCameraId,
                note = cold.cameraNote,
                onSelect = { viewModel.selectCamera(it) },
            )
            CameraSettingsPanel(
                settings = cold.settings,
                caps = viewModel.capsFor(cold.effectiveCameraId),
                profiles = SETTING_PROFILES,
                activeProfile = cold.activeProfile,
                appliedLine = cold.appliedLine,
                settingsNote = cold.settingsNote,
                analysisSelectable = true,
                onUpdate = viewModel::updateSettings,
                onProfile = { viewModel.selectProfile(it) },
            )
            QualityRow(state, viewModel)
        }
    }
}

private val PillGreen = Color(0xFF1B5E20)
private val PillRed = Color(0xFFB71C1C)
private val PillBlue = Color(0xFF0D47A1)

@Composable
private fun StatusPill(text: String, color: Color) {
    Text(
        text,
        color = Color.White,
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.Bold,
        modifier = Modifier
            .background(color, RoundedCornerShape(50))
            .padding(horizontal = 14.dp, vertical = 6.dp),
    )
}

/** The one current task, in large high-contrast type. */
@Composable
private fun StatusBannerView(banner: StatusBanner) {
    val color = when (banner.tone) {
        BannerTone.NEUTRAL -> Color(0xFF263238)
        BannerTone.WAITING -> Color(0xFF0D47A1)
        BannerTone.ACTION -> Color(0xFFBF360C)
        BannerTone.SUCCESS -> Color(0xFF1B5E20)
        BannerTone.PROBLEM -> Color(0xFFB71C1C)
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(color, RoundedCornerShape(16.dp))
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            banner.title,
            color = Color.White,
            style = MaterialTheme.typography.headlineLarge,
            fontWeight = FontWeight.Bold,
        )
        banner.detail?.let {
            Text(it, color = Color.White, style = MaterialTheme.typography.titleLarge)
        }
    }
}

/**
 * Opt-in "Listen for Muse": a visible indicator while the microphone is
 * open. Transcripts are never shown on the main screen.
 */
@Composable
private fun VoiceToggle(voice: VoiceController, voiceState: VoiceController.State) {
    val context = LocalContext.current
    val micLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (granted) voice.startListening() }
    FilterChip(
        selected = voiceState.listening,
        onClick = {
            when {
                voiceState.listening -> voice.stopListening()
                ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                    PackageManager.PERMISSION_GRANTED -> voice.startListening()
                else -> micLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }
        },
        label = {
            Text(
                if (voiceState.listening) "Microphone on — say “Muse…”" else "Voice off — tap to talk to Muse",
                style = MaterialTheme.typography.titleMedium,
            )
        },
        modifier = Modifier.heightIn(min = 48.dp),
    )
}

/** Hold-to-talk fallback, last phrase heard and the voice link status. */
@Composable
private fun VoiceDetails(voice: VoiceController, voiceState: VoiceController.State) {
    if (voiceState.listening) {
        OutlinedButton(
            onClick = {},
            modifier = Modifier.pointerInput(Unit) {
                detectTapGestures(onPress = {
                    voice.pushToTalkDown()
                    tryAwaitRelease()
                    voice.pushToTalkUp()
                })
            },
        ) { Text(if (voiceState.pushToTalk) "Release to send" else "Hold to talk") }
    }
    voiceState.heard?.let { Text("You said: “$it”", style = MaterialTheme.typography.bodySmall) }
    if (voiceState.status.isNotEmpty()) Text(voiceState.status, style = MaterialTheme.typography.bodySmall)
}

/** [GuidanceUiState] with every per-pose field at its default. */
private fun GuidanceUiState.withoutPoseFields(): GuidanceUiState = copy(
    registration = RegistrationState.UNREGISTERED,
    wireReason = null,
    dwell = org.anastylos.capture.guidance.DwellState.Idle,
    cues = emptyList(),
    poseAgeMs = null,
    rmsPx = null,
    cornersPx = emptyList(),
    markersSeen = emptyList(),
    qualityVerdict = ImageQualityVerdict.Unknown,
    motionWarning = false,
    measuredSharpness = null,
    orientationOk = false,
    distanceOk = false,
    holdReason = null,
)

@Composable
private fun DetectionOverlay(
    state: GuidanceUiState,
    modifier: Modifier = Modifier,
) {
    val corners = state.cornersPx
    val geometry = state.overlayGeometry
    Canvas(modifier = modifier) {
        val geo = geometry ?: return@Canvas
        // No geometry or a stream mismatch: hide the quads rather
        // than draw them in the wrong place. Tracking text still shows.
        if (!OverlayMapping.aspectsMatch(geo)) return@Canvas
        for (marker in corners) {
            if (marker.corners.size != 4) continue
            val mapped = marker.corners.map { corner ->
                OverlayMapping.mapToView(
                    corner[0].toFloat(),
                    corner[1].toFloat(),
                    geo,
                    size.width,
                    size.height,
                )
            }
            for (i in 0..3) {
                drawLine(Color.Green, mapped[i], mapped[(i + 1) % 4], strokeWidth = 4f)
            }
            drawCircle(Color.Yellow, radius = 8f, center = mapped[0])
        }
    }
}

@Composable
private fun CueList(state: GuidanceUiState) {
    if (state.cues.isEmpty()) {
        if (state.registration == RegistrationState.ACQUIRED && state.target != null) {
            Text("On target.")
        }
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        state.cues.forEach { cue -> Text(cueText(cue), style = MaterialTheme.typography.bodyLarge) }
    }
}

@Composable
private fun StateGrid(state: GuidanceUiState) {
    Text(
        "Session ${state.session} · Request ${state.request}" +
            (state.lateMark?.let { " ($it)" } ?: "") +
            "\nTracking ${state.registration}" +
            (state.wireReason?.let { " ($it)" } ?: "") +
            " · Dwell ${dwellText(state)}" +
            "\nMarkers ${if (state.markersSeen.isEmpty()) "—" else state.markersSeen.joinToString()}" +
            "\nPose age ${state.poseAgeMs?.let { "${it}ms" } ?: "—"}" +
            " · RMS ${state.rmsPx?.let { "%.2f".format(it) } ?: "—"}px" +
            " · Quality ${state.qualityVerdict}" +
            (if (state.motionWarning) " · motion warning (not blocking)" else "") +
            (state.holdReason?.let { "\nHold: ${it.code}" } ?: ""),
        style = MaterialTheme.typography.bodyMedium,
    )
}

private fun dwellText(state: GuidanceUiState): String = when (val dwell = state.dwell) {
    is org.anastylos.capture.guidance.DwellState.Idle -> "idle"
    is org.anastylos.capture.guidance.DwellState.Accumulating ->
        "holding ${dwell.samples}/${org.anastylos.capture.guidance.HackathonMvp.DWELL_SAMPLES}"
    is org.anastylos.capture.guidance.DwellState.Ready -> "READY"
}

@Composable
private fun TargetPicker(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    Text("Target viewpoint", style = MaterialTheme.typography.titleSmall)
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.horizontalScroll(rememberScrollState()),
    ) {
        LOCAL_TARGETS.forEach { target ->
            FilterChip(
                selected = state.target?.id == target.id,
                onClick = { viewModel.selectTarget(target) },
                label = { Text(target.label) },
            )
        }
    }
    Text("Board: ${state.boardId}")
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf("surface_a3", "surface_a4", "fixture_front", "fixture_back").forEach { board ->
            FilterChip(
                selected = state.boardId == board,
                onClick = { viewModel.selectBoard(board) },
                label = { Text(board.removePrefix("surface_").removePrefix("fixture_")) },
            )
        }
    }
}

@Composable
private fun BigButton(text: String, primary: Boolean, enabled: Boolean = true, onClick: () -> Unit) {
    val modifier = Modifier.fillMaxWidth().height(64.dp)
    val label: @Composable () -> Unit = { Text(text, style = MaterialTheme.typography.titleLarge) }
    if (primary) Button(onClick = onClick, enabled = enabled, modifier = modifier) { label() }
    else OutlinedButton(onClick = onClick, enabled = enabled, modifier = modifier) { label() }
}

/** The few actions the operator needs during the flow; the rest lives in Details. */
@Composable
private fun MainControls(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    var sessionField by rememberSaveable { mutableStateOf(state.remoteSessionId ?: "") }
    if (!state.remoteAttached) {
        OutlinedTextField(
            value = sessionField,
            onValueChange = { sessionField = it.trim() },
            label = { Text("Session id from the workstation") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        BigButton("Attach", primary = true, enabled = sessionField.isNotBlank()) {
            viewModel.attachRemote(sessionField)
        }
        return
    }
    when (state.session) {
        SessionState.DISARMED -> BigButton("Start", primary = true) { viewModel.arm() }
        SessionState.ACTIVE -> {
            BigButton("Next request", primary = true) { viewModel.arm() }
            BigButton("Pause", primary = false) { viewModel.pause() }
        }
        SessionState.PAUSED -> BigButton("Resume", primary = true) { viewModel.resume() }
        else -> BigButton("New session", primary = true) { viewModel.newSession() }
    }
    if (state.remoteRequest != null) {
        BigButton("Take photo now", primary = false, enabled = !state.shutterBusy) { viewModel.captureManual() }
        if (!state.attemptReady && !state.reservingAttempt) {
            BigButton("Retry connection to workstation", primary = false) { viewModel.retryAttempt() }
        }
    }
}

/** Less common actions, kept out of the main flow. */
@Composable
private fun DetailControls(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    Text("Controls", style = MaterialTheme.typography.titleSmall)
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.horizontalScroll(rememberScrollState()),
    ) {
        OutlinedButton(onClick = { viewModel.cancelRequest() }) { Text("Cancel request") }
        OutlinedButton(onClick = { viewModel.cantReach() }) { Text("Can't reach") }
        if (state.session == SessionState.ACTIVE || state.session == SessionState.PAUSED) {
            OutlinedButton(onClick = { viewModel.disarm() }) { Text("Disarm") }
        }
        if (state.remoteAttached) {
            OutlinedButton(onClick = { viewModel.detachRemote() }) { Text("Detach") }
            OutlinedButton(
                onClick = { viewModel.selectRemoteBoard(state.boardId) },
                enabled = state.boardId.isNotBlank(),
            ) { Text("Map board to current") }
        }
    }
}

@Composable
private fun QualityRow(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    Text(
        "Sharpness ${state.measuredSharpness?.let { "%.1f".format(it) } ?: "—"}" +
            " / ref ${state.sharpnessReference?.let { "%.1f".format(it) } ?: "unset"}",
        style = MaterialTheme.typography.bodySmall,
    )
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = { viewModel.setSharpnessReference() }) { Text("Calibrate sharpness") }
        OutlinedButton(onClick = { viewModel.clearSharpnessReference() }) { Text("Clear") }
    }
}

/** Ids, revisions and delivery internals: details view only. */
@Composable
private fun RemoteDetails(state: GuidanceUiState) {
    if (!state.remoteAttached) return
    Text(
        "Session ${state.remoteSessionId?.take(8)}… · ${state.remoteSession ?: "…"}",
        style = MaterialTheme.typography.bodySmall,
    )
    state.remoteRequest?.let { request ->
        Text(
            "Request ${request.requestId.take(8)}… · ${request.status} r${request.revision} · " +
                "board ${request.boardId.take(8)}… → ${state.boardId} · calibration ${request.calibrationId.take(8)}…",
            style = MaterialTheme.typography.bodySmall,
        )
    }
    if (state.reservingAttempt) Text("Reserving attempt…", style = MaterialTheme.typography.bodySmall)
    if (state.attemptReady) Text("Attempt reserved.", style = MaterialTheme.typography.bodySmall)
    state.delivery?.let { link ->
        Text(
            "Delivery ${link.deliveryState}" + (link.receiptStatus?.let { " · receipt ${it.wire}" } ?: ""),
            style = MaterialTheme.typography.bodySmall,
        )
    }
    state.deliveryNote?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
}

@Composable
private fun ResultDetails(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    val result = state.captureResult ?: return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            (if (result.automatic) "Auto-captured" else "Manually captured") +
                ": ${result.sha256.take(16)}…",
            style = MaterialTheme.typography.titleSmall,
        )
        Text(result.associationNote, style = MaterialTheme.typography.bodySmall)
        OutlinedButton(onClick = { viewModel.dismissResult() }) { Text("Dismiss") }
    }
}
