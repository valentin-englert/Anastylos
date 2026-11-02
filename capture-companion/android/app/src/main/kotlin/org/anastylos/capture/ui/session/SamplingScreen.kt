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
import org.anastylos.capture.ui.camera.CameraTopControls
import org.anastylos.capture.ui.camera.MenuSection
import org.anastylos.capture.ui.camera.Pill
import org.anastylos.capture.ui.camera.ScrimIconButton
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

    androidx.compose.foundation.layout.Box(Modifier.fillMaxSize().background(Palette.Ground)) {
        if (cameraGranted) AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
        Column(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                ScrimIconButton(Glyph.Back, "Back", onClick = onBack)
                Spacer(Modifier.weight(1f))
                CameraTopControls(
                    torchOn = state.settings.torch,
                    torchAvailable = caps.flashAvailable,
                    onTorch = { viewModel.updateSettings { it.copy(torch = !it.torch) } },
                    onMenu = { menuOpen = true },
                )
            }
            SamplingBanner(state, cameraGranted, bound)
            Spacer(Modifier.weight(1f))
            if (bound) {
                SettingsReadout(state.settings, caps, wheel) { kind -> wheel = if (wheel == kind) null else kind }
                wheel?.let { kind ->
                    SettingsWheelPanel(kind, state.settings, caps, viewModel::updateSettings, onInfo = { help = it }, onClose = { wheel = null })
                }
            }
            SamplingAction(state, cameraGranted, bound, permissionLauncher::launch, { sessionsOpen = true }, viewModel) { source.takeStill() }
        }
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
private fun SamplingBanner(state: SessionUiState, granted: Boolean, bound: Boolean) {
    val (title, detail) = when {
        !granted -> "Camera access needed" to "Allow the camera to take photos."
        !bound -> "No camera available" to null
        state.activeSessionId == null -> "Choose a session" to "Photos are stored in a session."
        state.sampling -> "Taking photos every ${state.intervalSeconds}s" to "${state.framesStored} stored · ${state.framesMissed} missed"
        else -> "Ready" to "${state.framesStored} stored in this session"
    }
    Column(
        Modifier.fillMaxWidth().background(Color(0xB3000000), RoundedCornerShape(16.dp)).padding(horizontal = 18.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(title, style = MaterialTheme.typography.headlineMedium, color = if (state.sampling) Palette.Ochre else Palette.Paper)
        detail?.let { Text(it, style = MaterialTheme.typography.titleMedium, color = Palette.Paper) }
    }
}

@Composable
private fun SamplingAction(
    state: SessionUiState,
    granted: Boolean,
    bound: Boolean,
    requestCamera: (String) -> Unit,
    openSessions: () -> Unit,
    viewModel: SessionViewModel,
    takeStill: suspend () -> org.anastylos.capture.camera.CapturedStill,
) {
    val modifier = Modifier.fillMaxWidth().height(64.dp)
    when {
        !granted -> Button({ requestCamera(Manifest.permission.CAMERA) }, modifier) { Text("Allow camera", style = MaterialTheme.typography.titleLarge) }
        state.activeSessionId == null -> Button(openSessions, modifier) { Text("Sessions", style = MaterialTheme.typography.titleLarge) }
        state.sampling -> Button(
            onClick = { viewModel.stopSampling() },
            modifier = modifier,
            colors = ButtonDefaults.buttonColors(containerColor = Palette.Brick, contentColor = Palette.Ground),
        ) { Text("Stop", style = MaterialTheme.typography.titleLarge) }
        else -> Button({ viewModel.startSampling(takeStill) }, modifier, enabled = bound) {
            Text("Start", style = MaterialTheme.typography.titleLarge)
        }
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
