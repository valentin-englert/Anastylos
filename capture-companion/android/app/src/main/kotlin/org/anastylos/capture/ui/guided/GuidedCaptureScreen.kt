package org.anastylos.capture.ui.guided

import android.Manifest
import android.content.pm.PackageManager
import android.view.Surface
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Canvas
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
import org.anastylos.capture.guidance.CueAxis
import org.anastylos.capture.guidance.MovementCue
import org.anastylos.capture.guidance.PhoneRotation
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.guidance.SessionState

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

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onBack) { Text("Back") }
            Text("Guided capture", style = MaterialTheme.typography.titleLarge)
        }
        if (state.uncalibrated) {
            Text(
                "UNCALIBRATED: factory intrinsics (${state.intrinsicsLabel ?: "none"}). " +
                    "Poses are coarse; metric evidence is disabled until calibration.",
                color = MaterialTheme.colorScheme.error,
            )
        }
        TrackingBanner(state)
        Box(modifier = Modifier.fillMaxWidth()) {
            AndroidView(
                factory = { previewView },
                modifier = Modifier.fillMaxWidth(),
            )
            DetectionOverlay(state, Modifier.matchParentSize())
        }
        CueList(state)
        StateGrid(state)
        TargetPicker(state, viewModel)
        ControlRow(state, viewModel)
        QualityRow(state, viewModel)
        RemoteSection(state, viewModel)
        ResultCard(state, viewModel)
        state.cameraError?.let { Text("Camera: $it", color = MaterialTheme.colorScheme.error) }
        state.captureError?.let { Text("Capture: $it", color = MaterialTheme.colorScheme.error) }
        state.sessionReason?.let { Text("Session note: $it") }
    }
}

@Composable
private fun TrackingBanner(state: GuidanceUiState) {
    val (text, color) = when {
        state.request == RequestState.HOLD_STILL && state.dwell is org.anastylos.capture.guidance.DwellState.Ready ->
            "HOLD STILL — capturing…" to Color(0xFF2E7D32)
        state.request == RequestState.HOLD_STILL ->
            "HOLD STILL — keep the phone steady" to Color(0xFF2E7D32)
        state.registration == RegistrationState.ACQUIRED -> "Tracking valid" to Color(0xFF2E7D32)
        state.registration == RegistrationState.DEGRADED -> "Tracking degraded (no metric pose)" to Color(0xFFEF6C00)
        state.registration == RegistrationState.LOST -> "Tracking lost — aim at the board" to Color(0xFFC62828)
        state.registration == RegistrationState.BAD_CALIBRATION ->
            "Bad calibration — check profile" to Color(0xFFC62828)
        else -> "Searching for the board…" to Color.Gray
    }
    Text(text, color = color, style = MaterialTheme.typography.titleMedium)
}

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

private fun cueText(cue: MovementCue): String {
    val magnitude = "%.0f".format(kotlin.math.abs(cue.magnitude))
    return when (cue.axis) {
        CueAxis.LATERAL -> if (cue.magnitude > 0) "→ Move right ${magnitude}mm" else "← Move left ${magnitude}mm"
        CueAxis.VERTICAL -> if (cue.magnitude > 0) "↓ Move down ${magnitude}mm" else "↑ Move up ${magnitude}mm"
        CueAxis.DEPTH -> if (cue.magnitude > 0) "⊕ Move back ${magnitude}mm" else "⊙ Move closer ${magnitude}mm"
        CueAxis.YAW -> if (cue.magnitude > 0) "↻ Turn right ${magnitude}°" else "↺ Turn left ${magnitude}°"
        CueAxis.PITCH -> if (cue.magnitude > 0) "↓ Tilt down ${magnitude}°" else "↑ Tilt up ${magnitude}°"
        CueAxis.ROLL -> if (cue.magnitude > 0) "↻ Roll right ${magnitude}°" else "↺ Roll left ${magnitude}°"
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
            "\nPose age ${state.poseAgeMs?.let { "${it}ms" } ?: "—"}" +
            " · RMS ${state.rmsPx?.let { "%.2f".format(it) } ?: "—"}px" +
            " · Quality ${state.qualityVerdict}" +
            (state.holdReason?.let { "\nHold: ${it.code}" } ?: ""),
        style = MaterialTheme.typography.bodyMedium,
    )
}

private fun dwellText(state: GuidanceUiState): String = when (val dwell = state.dwell) {
    is org.anastylos.capture.guidance.DwellState.Idle -> "idle"
    is org.anastylos.capture.guidance.DwellState.Accumulating -> "holding ${dwell.samples}/12"
    is org.anastylos.capture.guidance.DwellState.Ready -> "READY"
}

@Composable
private fun TargetPicker(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    Text("Target viewpoint", style = MaterialTheme.typography.titleSmall)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
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
private fun ControlRow(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        when (state.session) {
            SessionState.DISARMED -> Button(onClick = { viewModel.arm() }) { Text("Arm") }
            SessionState.ACTIVE -> {
                Button(onClick = { viewModel.arm() }) { Text("Next request") }
                OutlinedButton(onClick = { viewModel.pause() }) { Text("Pause") }
                OutlinedButton(onClick = { viewModel.disarm() }) { Text("Disarm") }
            }
            SessionState.PAUSED -> {
                Button(onClick = { viewModel.resume() }) { Text("Resume") }
                OutlinedButton(onClick = { viewModel.disarm() }) { Text("Disarm") }
            }
            else -> {
                Button(onClick = { viewModel.newSession() }) { Text("New session") }
            }
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = { viewModel.captureManual() }, enabled = !state.shutterBusy) {
            Text("Capture now (manual)")
        }
        OutlinedButton(onClick = { viewModel.cancelRequest() }) { Text("Cancel request") }
        OutlinedButton(onClick = { viewModel.cantReach() }) { Text("Can't reach") }
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

@Composable
private fun RemoteSection(state: GuidanceUiState, viewModel: GuidanceViewModel) {
    var sessionField by rememberSaveable { mutableStateOf(state.remoteSessionId ?: "") }
    Text("Core remote control", style = MaterialTheme.typography.titleSmall)
    if (!state.remoteAttached) {
        Text(
            "Attach a guided session from the workstation to receive requests, reserve attempts, and deliver evidence.",
            style = MaterialTheme.typography.bodySmall,
        )
        OutlinedTextField(
            value = sessionField,
            onValueChange = { sessionField = it.trim() },
            label = { Text("Session id (uuid)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { viewModel.attachRemote(sessionField) },
                enabled = sessionField.isNotBlank(),
            ) { Text("Attach") }
        }
    } else {
        Text(
            "Session ${state.remoteSessionId?.take(8)}… · ${state.remoteSession ?: "…"}",
            style = MaterialTheme.typography.bodyMedium,
        )
        val request = state.remoteRequest
        if (request == null) {
            Text("Waiting for a Core request…", style = MaterialTheme.typography.bodySmall)
        } else {
            Text(
                "Request ${request.targetLabel} · ${request.status} r${request.revision}",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                "Board ${request.boardId.take(8)}… → ${state.boardId} · " +
                    "calibration ${request.calibrationId.take(8)}…",
                style = MaterialTheme.typography.bodySmall,
            )
            if (state.boardId.isBlank()) {
                Text("Map this board before navigating.", color = MaterialTheme.colorScheme.error)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { viewModel.detachRemote() }) { Text("Detach") }
            OutlinedButton(
                onClick = { viewModel.selectRemoteBoard(state.boardId) },
                enabled = state.boardId.isNotBlank(),
            ) { Text("Map board to current") }
        }
        if (state.reservingAttempt) Text("Reserving attempt…")
        if (state.attemptReady) Text("Attempt reserved.")
        if (!state.attemptReady && !state.reservingAttempt && state.remoteRequest != null) {
            OutlinedButton(onClick = { viewModel.retryAttempt() }) { Text("Retry attempt") }
        }
        state.delivery?.let { link ->
            Text(
                "Delivery ${link.deliveryState}" +
                    (link.receiptStatus?.let { " · receipt ${it.wire}" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
            )
        }
        state.deliveryNote?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
    }
    state.remoteError?.let { error ->
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(error, color = MaterialTheme.colorScheme.error)
            OutlinedButton(onClick = { viewModel.clearRemoteError() }) { Text("Dismiss") }
        }
    }
}

@Composable
private fun ResultCard(state: GuidanceUiState, viewModel: GuidanceViewModel) {
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
