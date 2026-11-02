package org.anastylos.capture.ui.session

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.core.content.ContextCompat
import org.anastylos.capture.camera.CameraStillSource
import org.anastylos.capture.camera.SETTING_PROFILES
import org.anastylos.capture.camera.StillCamera
import org.anastylos.capture.ui.camera.CameraMenu
import org.anastylos.capture.ui.camera.CameraScaffold
import org.anastylos.capture.ui.camera.availableWheels
import org.anastylos.capture.ui.camera.onImage
import org.anastylos.capture.ui.camera.MenuSection
import org.anastylos.capture.ui.camera.Pill
import android.content.res.Configuration
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.sp
import org.anastylos.capture.ui.camera.SettingsReadout
import org.anastylos.capture.ui.camera.SettingsWheelPanel
import org.anastylos.capture.ui.camera.WheelKind
import org.anastylos.capture.ui.onboarding.HelpDialog
import org.anastylos.capture.ui.onboarding.HelpTopic
import org.anastylos.capture.ui.theme.Glyph
import org.anastylos.capture.ui.theme.Palette

private val SAMPLE_INTERVALS = listOf(2, 5, 10, 30)

/**
 * Timed photos: full-screen camera that stores a still every few
 * seconds into the chosen session. The interval, past sessions and
 * camera options sit behind the menu; the shutter wheels sit on the image.
 */
@Composable
fun SamplingScreen(viewModel: SessionViewModel, onBack: () -> Unit, onReplayGuide: () -> Unit) {
    val state by viewModel.state.collectAsState()
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current
    var cameraGranted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { cameraGranted = it }
    LaunchedEffect(Unit) { if (!cameraGranted) permissionLauncher.launch(Manifest.permission.CAMERA) }

    val previewView = remember { PreviewView(context) }
    val source: StillCamera = remember { CameraStillSource(context, lifecycle, previewView) }
    var bound by remember { mutableStateOf(false) }
    val caps = viewModel.capsFor(state.effectiveCameraId)

    LaunchedEffect(cameraGranted, state.selectedCameraId, state.settings.stillSize) {
        if (!cameraGranted) return@LaunchedEffect
        source.requestedCameraId = state.selectedCameraId
        source.requestedStillSize = state.settings.stillSize
        bound = try { source.bindAsync() } catch (_: Exception) { false }
        viewModel.onCamerasListed(source.availableCameras(), source.effectiveCameraId, source.cameraNote)
    }
    LaunchedEffect(state.settings, bound, caps) {
        if (!bound) return@LaunchedEffect
        val applied = try { source.applySettings(state.settings, caps) } catch (_: Exception) { false }
        val stillOk = try { source.refreshStillOptions() } catch (_: Exception) { false }
        viewModel.onSettingsApplied(applied && stillOk)
    }
    DisposableEffect(Unit) { onDispose { source.unbind() } }

    var menuOpen by rememberSaveable { mutableStateOf(false) }
    var sessionsOpen by rememberSaveable { mutableStateOf(false) }
    var wheel by remember { mutableStateOf<WheelKind?>(null) }
    var help by remember { mutableStateOf<HelpTopic?>(null) }

    val landscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE
    androidx.compose.foundation.layout.Box(Modifier.fillMaxSize().background(Palette.Ground)) {
        CameraScaffold(
            onBack = onBack,
            status = { compact -> SamplingStatus(state, compact) },
            barExtras = {},
            torchOn = state.settings.torch,
            torchAvailable = caps.flashAvailable,
            onTorch = { viewModel.updateSettings { it.copy(torch = !it.torch) } },
            onMenu = { menuOpen = true },
            actions = { compact ->
                SamplingAction(state, cameraGranted, bound, compact, permissionLauncher::launch, { sessionsOpen = true }, viewModel) { source.takeStill() }
            },
            overlayTop = { isLandscape ->
                Text(
                    samplingTitle(state, cameraGranted, bound),
                    color = if (state.sampling) Palette.Ochre else Palette.Paper,
                    style = (if (isLandscape) MaterialTheme.typography.titleLarge else MaterialTheme.typography.headlineSmall).onImage(),
                )
                if (!isLandscape) samplingDetail(state, bound)?.let {
                    Text(it, color = Palette.Paper, style = MaterialTheme.typography.titleMedium.onImage())
                }
            },
            overlayBottom = { isLandscape ->
                if (bound) {
                    if (!isLandscape) SettingsReadout(state.settings, caps, wheel) { kind -> wheel = if (wheel == kind) null else kind }
                    wheel?.let { kind -> SettingsWheelPanel(kind, state.settings, caps, viewModel::updateSettings, onInfo = { help = it }) }
                }
            },
            preview = { if (cameraGranted) AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize()) },
        )
        if (menuOpen) {
            CameraMenu(
                cameras = state.availableCameras,
                effectiveCameraId = state.effectiveCameraId,
                cameraNote = state.cameraNote,
                onSelectCamera = { viewModel.selectCamera(it) },
                profiles = SETTING_PROFILES,
                activeProfile = state.activeProfile,
                onProfile = { viewModel.selectProfile(it) },
                settings = state.settings,
                caps = caps,
                onUpdate = viewModel::updateSettings,
                appliedLine = null,
                settingsNote = state.settingsNote,
                analysisSelectable = false,
                onInfo = { help = it },
                onClose = { menuOpen = false },
            ) {
                MenuSection("Sample every") {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SAMPLE_INTERVALS.forEach { seconds ->
                            Pill("${seconds}s", state.intervalSeconds == seconds) { viewModel.setInterval(seconds) }
                        }
                    }
                }
                MenuSection("Sessions") {
                    Pill("Open session list", selected = false) { menuOpen = false; sessionsOpen = true }
                }
                if (landscape) {
                    MenuSection("Exposure and focus") {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.horizontalScroll(rememberScrollState())) {
                            availableWheels(state.settings, caps).forEach { kind ->
                                Pill(kind.label, wheel == kind) { wheel = kind; menuOpen = false }
                            }
                        }
                    }
                }
                MenuSection("Guide") {
                    Pill("How it works", selected = false) { menuOpen = false; onReplayGuide() }
                }
            }
        }
        if (sessionsOpen) {
            SessionsDialog(state, onSelect = { viewModel.selectSession(it); sessionsOpen = false }, onCreate = { viewModel.createSession(it) }, onDismiss = { sessionsOpen = false })
        }
        help?.let { HelpDialog(it) { help = null } }
    }
}

@Composable
private fun SamplingStatus(state: SessionUiState, compact: Boolean) {
    val active = state.sampling
    val text = if (active) (if (compact) "Active" else "Taking photos") else "Ready"
    if (compact) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.size(9.dp).background(if (active) Palette.Ochre else Palette.Muted, CircleShape))
            Text(text, color = Palette.Paper, fontSize = 11.sp)
        }
    } else {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(9.dp).background(if (active) Palette.Ochre else Palette.Muted, CircleShape))
            Text(text, color = Palette.Paper, style = MaterialTheme.typography.titleSmall)
        }
    }
}

private fun samplingTitle(state: SessionUiState, granted: Boolean, bound: Boolean): String = when {
    !granted -> "Camera access needed"
    !bound -> "No camera available"
    state.activeSessionId == null -> "Choose a session"
    state.sampling -> "Every ${state.intervalSeconds}s"
    else -> "Ready"
}

private fun samplingDetail(state: SessionUiState, bound: Boolean): String? = when {
    !bound || state.activeSessionId == null -> null
    state.sampling -> "${state.framesStored} stored · ${state.framesMissed} missed"
    else -> "${state.framesStored} stored in this session"
}

@Composable
private fun SamplingAction(
    state: SessionUiState,
    granted: Boolean,
    bound: Boolean,
    compact: Boolean,
    requestCamera: (String) -> Unit,
    openSessions: () -> Unit,
    viewModel: SessionViewModel,
    takeStill: suspend () -> org.anastylos.capture.camera.CapturedStill,
) {
    val modifier = Modifier.fillMaxWidth().height(if (compact) 44.dp else 56.dp)
    val padding = if (compact) PaddingValues(horizontal = 4.dp) else ButtonDefaults.ContentPadding
    val label: @Composable (String, String) -> Unit = { long, short ->
        if (compact) Text(short, fontSize = 13.sp, maxLines = 1) else Text(long, style = MaterialTheme.typography.titleMedium)
    }
    when {
        !granted -> Button({ requestCamera(Manifest.permission.CAMERA) }, modifier, contentPadding = padding) { label("Allow camera", "Allow") }
        state.activeSessionId == null -> Button(openSessions, modifier, contentPadding = padding) { label("Sessions", "Sessions") }
        state.sampling -> Button(
            onClick = { viewModel.stopSampling() },
            modifier = modifier,
            contentPadding = padding,
            colors = ButtonDefaults.buttonColors(containerColor = Palette.Brick, contentColor = Palette.Ground),
        ) { label("Stop", "Stop") }
        else -> Button({ viewModel.startSampling(takeStill) }, modifier, enabled = bound, contentPadding = padding) { label("Start", "Start") }
    }
}

/** Past sessions in one scrolling list, with the field to start a new one. */
@Composable
private fun SessionsDialog(state: SessionUiState, onSelect: (String) -> Unit, onCreate: (String) -> Unit, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier.fillMaxWidth().height(480.dp).background(Palette.SurfaceRaised, RoundedCornerShape(20.dp)).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Sessions", style = MaterialTheme.typography.headlineSmall, color = Palette.Paper)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(name, { name = it }, label = { Text("New session") }, singleLine = true, modifier = Modifier.weight(1f))
                TextButton(onClick = { onCreate(name.ifBlank { "Untitled session" }); name = "" }) { Text("Create", color = Palette.Ochre) }
            }
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                items(state.sessions, key = { it.sessionId }) { session ->
                    val active = session.sessionId == state.activeSessionId
                    Text(
                        session.name,
                        color = if (active) Palette.Ochre else Palette.Paper,
                        fontWeight = if (active) FontWeight.Bold else FontWeight.Normal,
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier.fillMaxWidth().clickable { onSelect(session.sessionId) }.padding(vertical = 12.dp),
                    )
                }
            }
            TextButton(onClick = onDismiss, modifier = Modifier.align(Alignment.End)) { Text("Close", color = Palette.Muted) }
        }
    }
}
