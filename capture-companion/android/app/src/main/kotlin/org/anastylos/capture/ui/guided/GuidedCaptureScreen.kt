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
import org.anastylos.capture.camera.SETTING_PROFILES
import org.anastylos.capture.R
import org.anastylos.capture.ui.camera.CameraMenu
import org.anastylos.capture.ui.camera.BarIconButton
import org.anastylos.capture.ui.camera.CameraScaffold
import org.anastylos.capture.ui.camera.availableWheels
import org.anastylos.capture.ui.camera.onImage
import org.anastylos.capture.ui.camera.MenuSection
import org.anastylos.capture.ui.camera.Pill
import android.content.res.Configuration
import android.os.SystemClock
import androidx.compose.runtime.produceState
import kotlinx.coroutines.delay
import org.anastylos.capture.video.RecordingController
import org.anastylos.capture.video.RecordingState
import org.anastylos.capture.video.SheetWatch
import org.anastylos.capture.video.UploadProgress
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.height
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp
import org.anastylos.capture.ui.camera.SettingsReadout
import org.anastylos.capture.ui.camera.SettingsWheelPanel
import org.anastylos.capture.ui.camera.WheelKind
import org.anastylos.capture.ui.onboarding.HelpDialog
import org.anastylos.capture.ui.onboarding.HelpTopic
import org.anastylos.capture.ui.theme.Glyph
import org.anastylos.capture.ui.theme.Palette
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource

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
    onScanSession: () -> Unit,
    scannedSessionId: String?,
    onScannedConsumed: () -> Unit,
    onReplayGuide: () -> Unit,
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
    var scanProblem by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(scannedSessionId) {
        val id = scannedSessionId ?: return@LaunchedEffect
        if (id == INVALID_SESSION_QR) scanProblem = "That QR code does not hold a session id." else viewModel.attachRemote(id)
        onScannedConsumed()
    }
    val recorder = viewModel.recording
    val recordingState by (recorder?.recording ?: remember { kotlinx.coroutines.flow.MutableStateFlow<RecordingState>(RecordingState.Idle) }).collectAsState()
    val uploadProgress by (recorder?.upload ?: remember { kotlinx.coroutines.flow.MutableStateFlow<UploadProgress?>(null) }).collectAsState()
    DisposableEffect(recorder) {
        container.voice.onRecordingCommand = { start -> if (start) recorder?.start() else recorder?.stop() }
        onDispose { container.voice.onRecordingCommand = null }
    }
    val recordingNow = recordingState is RecordingState.Recording
    var sheetWarning by remember { mutableStateOf(false) }
    LaunchedEffect(recordingNow) {
        val watch = SheetWatch()
        sheetWarning = false
        while (recordingNow) {
            sheetWarning = watch.onSample(SystemClock.elapsedRealtime(), viewModel.state.value.registration == RegistrationState.ACQUIRED)
            delay(1_000)
        }
    }
    var menuOpen by rememberSaveable { mutableStateOf(false) }
    var wheel by remember { mutableStateOf<WheelKind?>(null) }
    var help by remember { mutableStateOf<HelpTopic?>(null) }
    var diagnostics by rememberSaveable { mutableStateOf(false) }
    val caps = viewModel.capsFor(cold.effectiveCameraId)
    val landscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE

    Box(Modifier.fillMaxSize().background(Palette.Ground)) {
        CameraScaffold(
            onBack = onBack,
            status = { compact -> StatusLabel(cold.remoteAttached, if (compact) null else voiceLabelOf(voiceState), compact) },
            barExtras = {
                if (recorder != null) {
                    BarIconButton(
                        if (recordingNow) Glyph.Stop else Glyph.Record,
                        if (recordingNow) "Stop recording" else "Record the disassembly",
                        tint = Palette.Brick,
                    ) { if (recordingNow) recorder.stop() else recorder.start() }
                }
                if (!cold.remoteAttached) BarIconButton(Glyph.Qr, "Scan the session QR code", onClick = onScanSession)
            },
            torchOn = cold.settings.torch,
            torchAvailable = caps.flashAvailable,
            onTorch = { viewModel.updateSettings { it.copy(torch = !it.torch) } },
            onMenu = { menuOpen = true },
            actions = { compact -> ActionButtons(cold, viewModel, container.voice, voiceState, compact, onScanSession) },
            overlayTop = { isLandscape ->
                RecordingLines(recordingState, uploadProgress, sheetWarning, recorder)
                if (cold.remoteAttached) BannerText(statusBannerOf(state), isLandscape)
                if (cold.remoteAttached && cold.remoteRequest == null && !isLandscape) CueList(state)
                state.cameraError?.let { ErrorLine("Camera: $it") }
                state.captureError?.let { ErrorLine("Capture: $it") }
                scanProblem?.let { ErrorLine(it) }
                state.remoteError?.let { error ->
                    ErrorLine(error)
                    if (!isLandscape) OutlinedButton(onClick = { viewModel.clearRemoteError() }) { Text("Dismiss") }
                }
            },
            overlayBottom = { isLandscape ->
                if (!isLandscape) SettingsReadout(cold.settings, caps, wheel) { kind -> wheel = if (wheel == kind) null else kind }
                wheel?.let { kind ->
                    SettingsWheelPanel(kind, cold.settings, caps, viewModel::updateSettings, onInfo = { help = it })
                }
            },
            preview = {
                AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
                DetectionOverlay(state, Modifier.fillMaxSize())
            },
        )
        if (menuOpen) {
            CameraMenu(
                cameras = cold.availableCameras,
                effectiveCameraId = cold.effectiveCameraId,
                cameraNote = cold.cameraNote,
                onSelectCamera = { viewModel.selectCamera(it) },
                profiles = SETTING_PROFILES,
                activeProfile = cold.activeProfile,
                onProfile = { viewModel.selectProfile(it) },
                settings = cold.settings,
                caps = caps,
                onUpdate = viewModel::updateSettings,
                appliedLine = cold.appliedLine,
                settingsNote = cold.settingsNote,
                analysisSelectable = true,
                onInfo = { help = it },
                onClose = { menuOpen = false },
            ) {
                if (landscape) {
                    MenuSection("Exposure and focus") {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
                            availableWheels(cold.settings, caps).forEach { kind ->
                                Pill(kind.label, wheel == kind) { wheel = kind; menuOpen = false }
                            }
                        }
                    }
                }
                MenuSection("Guide") {
                    Pill("How it works", selected = false, onClick = { menuOpen = false; onReplayGuide() })
                }
                MenuSection("Diagnostics") {
                    Pill(if (diagnostics) "Hide" else "Show", selected = diagnostics, onClick = { diagnostics = !diagnostics })
                    if (diagnostics) Diagnostics(state, cold, viewModel, container.voice, voiceState)
                }
            }
        }
        help?.let { HelpDialog(it) { help = null } }
    }
}

@Composable
private fun Diagnostics(
    state: GuidanceUiState,
    cold: GuidanceUiState,
    viewModel: GuidanceViewModel,
    voice: VoiceController,
    voiceState: VoiceController.State,
) {
    if (state.uncalibrated) {
        Text(
            "UNCALIBRATED: factory intrinsics (${state.intrinsicsLabel ?: "none"}). " +
                "Poses are coarse; metric evidence is disabled until calibration.",
            color = MaterialTheme.colorScheme.error,
        )
        cold.calibrationBlocker?.let { reason ->
            Text("Calibrated profile not applied: $reason")
            Button(onClick = { viewModel.applyCalibratedOptics() }) { Text("Use calibrated optics") }
        }
    }
    StateGrid(state)
    state.sessionReason?.let { Text("Session note: $it") }
    RemoteDetails(cold)
    ResultDetails(cold, viewModel)
    DetailControls(cold, viewModel)
    VoiceDetails(voice, voiceState)
    TargetPicker(cold, viewModel)
    QualityRow(state, viewModel)
}

/** Recording time, upload progress and the out-of-view warning; plain text, never blocking. */
@Composable
private fun RecordingLines(
    recording: RecordingState,
    upload: UploadProgress?,
    sheetWarning: Boolean,
    recorder: RecordingController?,
) {
    val elapsedSeconds by produceState(0L, recording) {
        val started = (recording as? RecordingState.Recording)?.startedAtMs ?: return@produceState
        while (true) {
            value = (System.currentTimeMillis() - started) / 1000
            delay(1_000)
        }
    }
    when (recording) {
        is RecordingState.Recording ->
            Text("● Recording %d:%02d".format(elapsedSeconds / 60, elapsedSeconds % 60), color = Palette.Brick, style = MaterialTheme.typography.titleMedium.onImage())
        is RecordingState.Failed -> {
            ErrorLine(recording.message)
            Text("Dismiss", color = Palette.Paper, style = MaterialTheme.typography.bodyMedium.onImage(), modifier = Modifier.clickable { recorder?.dismissFailure() })
        }
        RecordingState.Idle -> Unit
    }
    if (sheetWarning) Text("The sheet is out of view. Recording continues.", color = Palette.Ochre, style = MaterialTheme.typography.titleMedium.onImage())
    when (upload) {
        is UploadProgress.Sending -> Text("Sending video ${upload.sent} of ${upload.total}", color = Palette.Paper, style = MaterialTheme.typography.bodyMedium.onImage())
        is UploadProgress.Waiting -> Text("Video saved. ${upload.reason} Retrying.", color = Palette.Paper, style = MaterialTheme.typography.bodyMedium.onImage())
        UploadProgress.Done -> Text("Video sent to the workstation.", color = Palette.Sage, style = MaterialTheme.typography.bodyMedium.onImage())
        is UploadProgress.Failed -> {
            ErrorLine("Video not sent: ${upload.message}")
            Text("Dismiss", color = Palette.Paper, style = MaterialTheme.typography.bodyMedium.onImage(), modifier = Modifier.clickable { recorder?.dismissFailure() })
        }
        null -> Unit
    }
}

@Composable
private fun ErrorLine(text: String) {
    Text(text, color = Palette.Brick, style = MaterialTheme.typography.titleMedium.onImage())
}

/** Session attached or not (and the voice state in portrait); the first thing to read. */
@Composable
private fun StatusLabel(attached: Boolean, voiceLabel: String?, compact: Boolean) {
    val dot = if (attached) Palette.Sage else Palette.Brick
    val text = if (attached) (if (compact) "Attached" else "Session attached") else "No session"
    if (compact) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.size(9.dp).background(dot, CircleShape))
            Text(text, color = Palette.Paper, fontSize = 11.sp)
        }
    } else {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(9.dp).background(dot, CircleShape))
            Text(text, color = Palette.Paper, style = MaterialTheme.typography.titleSmall)
            voiceLabel?.let { Text(it, color = Palette.Muse, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold) }
        }
    }
}

/** The current task as plain text over the image; landscape keeps only the headline. */
@Composable
private fun BannerText(banner: StatusBanner, landscape: Boolean) {
    val color = when (banner.tone) {
        BannerTone.NEUTRAL, BannerTone.WAITING -> Palette.Paper
        BannerTone.ACTION -> Palette.Ochre
        BannerTone.SUCCESS -> Palette.Sage
        BannerTone.PROBLEM -> Palette.Brick
    }
    Text(
        banner.title,
        color = color,
        style = (if (landscape) MaterialTheme.typography.titleLarge else MaterialTheme.typography.headlineSmall).onImage(),
    )
    if (!landscape) banner.detail?.let { Text(it, color = Palette.Paper, style = MaterialTheme.typography.titleMedium.onImage()) }
}

/**
 * Opt-in voice: just the Muse mark. A blue ring shows the microphone
 * is open; transcripts never appear here.
 */
@Composable
private fun MuseButton(voice: VoiceController, voiceState: VoiceController.State, size: Dp) {
    val context = LocalContext.current
    val micLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (granted) voice.startListening() }
    Image(
        painterResource(R.drawable.muse_logo),
        contentDescription = if (voiceState.listening) "Turn the microphone off" else "Talk to Muse",
        modifier = Modifier
            .size(size)
            .clip(RoundedCornerShape(size / 4))
            .border(if (voiceState.listening) 3.dp else 0.dp, Palette.Muse, RoundedCornerShape(size / 4))
            .clickable {
                when {
                    voiceState.listening -> voice.stopListening()
                    ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                        PackageManager.PERMISSION_GRANTED -> voice.startListening()
                    else -> micLauncher.launch(Manifest.permission.RECORD_AUDIO)
                }
            },
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
    Column(
        modifier = Modifier.background(Palette.Scrim, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        state.cues.forEach { cue -> Text(cueText(cue), style = MaterialTheme.typography.titleMedium, color = Palette.Paper) }
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
private fun ActionButton(text: String, short: String, primary: Boolean, compact: Boolean, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    val sized = modifier.height(if (compact) 44.dp else 56.dp)
    val label: @Composable () -> Unit = {
        if (compact) Text(short, fontSize = 13.sp, maxLines = 1) else Text(text, style = MaterialTheme.typography.titleMedium)
    }
    val padding = if (compact) PaddingValues(horizontal = 4.dp) else ButtonDefaults.ContentPadding
    if (primary) {
        Button(onClick = onClick, enabled = enabled, modifier = sized, contentPadding = padding) { label() }
    } else {
        OutlinedButton(
            onClick = onClick,
            enabled = enabled,
            modifier = sized,
            contentPadding = padding,
            colors = ButtonDefaults.outlinedButtonColors(contentColor = Palette.Paper),
        ) { label() }
    }
}

/** The few actions the operator needs during the flow; the rest lives in the menu. */
@Composable
private fun ActionButtons(
    state: GuidanceUiState,
    viewModel: GuidanceViewModel,
    voice: VoiceController,
    voiceState: VoiceController.State,
    compact: Boolean,
    onScanSession: () -> Unit,
) {
    val photoActions = state.remoteAttached && state.remoteRequest != null
    val buttons: @Composable (Modifier) -> Unit = { fill ->
        if (!state.remoteAttached) {
            ActionButton("Scan session QR", "Scan", primary = true, compact = compact, modifier = fill, onClick = onScanSession)
        } else {
            when (state.session) {
                SessionState.DISARMED -> ActionButton("Start", "Start", true, compact, fill) { viewModel.arm() }
                SessionState.ACTIVE -> {
                    ActionButton("Next request", "Next", true, compact, fill) { viewModel.arm() }
                    ActionButton("Pause", "Pause", false, compact, if (compact) fill else Modifier) { viewModel.pause() }
                }
                SessionState.PAUSED -> ActionButton("Resume", "Resume", true, compact, fill) { viewModel.resume() }
                else -> ActionButton("New session", "New", true, compact, fill) { viewModel.newSession() }
            }
        }
    }
    val photoButtons: @Composable (Modifier) -> Unit = { mod ->
        ActionButton("Take photo now", "Photo", false, compact, mod, enabled = !state.shutterBusy) { viewModel.captureManual() }
        if (!state.attemptReady && !state.reservingAttempt) {
            ActionButton("Retry connection", "Retry", false, compact, mod) { viewModel.retryAttempt() }
        }
    }
    if (compact) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            MuseButton(voice, voiceState, 44.dp)
            if (photoActions) photoButtons(Modifier.fillMaxWidth())
            buttons(Modifier.fillMaxWidth())
        }
    } else {
        if (photoActions) Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) { photoButtons(Modifier.weight(1f)) }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            buttons(Modifier.weight(1f))
            MuseButton(voice, voiceState, 52.dp)
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

/** Value passed back from the QR scanner when the code carries no session id. */
const val INVALID_SESSION_QR = "invalid"
