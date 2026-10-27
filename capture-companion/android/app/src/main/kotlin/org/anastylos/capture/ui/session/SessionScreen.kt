package org.anastylos.capture.ui.session

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.view.PreviewView
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import org.anastylos.capture.camera.CameraStillSource

/**
 * Slice A session screen: functional, honest, and deliberately plain.
 * Every control performs its labeled action; nothing is a placeholder.
 * The product design system arrives in a later milestone.
 */
@Composable
fun SessionScreen(viewModel: SessionViewModel, onConnect: () -> Unit) {
    val state by viewModel.state.collectAsState()
    LaunchedEffect(Unit) { viewModel.refreshPairing() }
    val context = LocalContext.current
    var cameraGranted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> cameraGranted = granted }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("ANASTYLOS CAPTURE", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Sampled stills feed the active session. Nothing uploads without pairing.",
            style = MaterialTheme.typography.bodyMedium,
        )

        if (!cameraGranted) {
            Text("Camera access is needed to sample frames. Photos stay on this phone until you upload them.")
            Button(onClick = { permissionLauncher.launch(Manifest.permission.CAMERA) }) {
                Text("Grant camera access")
            }
        } else {
            CameraPreview(
                onCapture = { viewModel.startSampling(it) },
                sampling = state.sampling,
                onStop = { viewModel.stopSampling() },
            )
        }

        SessionSection(
            state = state,
            onCreate = { viewModel.createSession(it) },
            onSelect = { viewModel.selectSession(it) },
            onInterval = { viewModel.setInterval(it) },
        )

        SamplingStatus(state)

        PairingSection(
            state = state,
            onPair = { host, port, code, fingerprint ->
                viewModel.pair(host, port, code, fingerprint)
            },
            onUnpair = { viewModel.unpair() },
            onScanQr = onConnect,
        )

        UploadSection(
            state = state,
            onUpload = { viewModel.uploadPending() },
        )

        val error = state.error ?: state.pairingError
        if (error != null) {
            Text(error, color = MaterialTheme.colorScheme.error)
            OutlinedButton(onClick = { viewModel.dismissError() }) {
                Text("Dismiss")
            }
        }
        Spacer(modifier = Modifier.height(24.dp))
    }
}

@Composable
private fun CameraPreview(
    onCapture: (suspend () -> org.anastylos.capture.camera.CapturedStill) -> Unit,
    sampling: Boolean,
    onStop: () -> Unit,
) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current
    val previewView = remember { PreviewView(context) }
    val source = remember { CameraStillSource(context, lifecycle, previewView) }
    var bound by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        bound = try {
            source.bind()
        } catch (_: Exception) {
            false
        }
    }
    DisposableEffect(Unit) {
        onDispose { source.unbind() }
    }

    AndroidView(
        factory = { previewView },
        modifier = Modifier
            .fillMaxWidth()
            .height(240.dp),
    )
    if (!bound) {
        Text("No camera available on this device.")
        return
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(
            onClick = { onCapture { source.takeStill() } },
            enabled = !sampling,
        ) {
            Text("Start sampling")
        }
        OutlinedButton(onClick = onStop, enabled = sampling) {
            Text("Stop")
        }
    }
}

@Composable
private fun SessionSection(
    state: SessionUiState,
    onCreate: (String) -> Unit,
    onSelect: (String) -> Unit,
    onInterval: (Int) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    Text("Session", style = MaterialTheme.typography.titleMedium)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = name,
            onValueChange = { name = it },
            label = { Text("Session name") },
            singleLine = true,
            modifier = Modifier.weight(1f),
        )
        Button(
            onClick = {
                onCreate(name.ifBlank { "Untitled session" })
                name = ""
            },
        ) {
            Text("Create")
        }
    }
    state.sessions.forEach { session ->
        val active = session.sessionId == state.activeSessionId
        Text(
            text = (if (active) "● " else "○ ") + session.name,
            modifier = Modifier
                .fillMaxWidth()
                .clickable { onSelect(session.sessionId) }
                .padding(vertical = 8.dp),
        )
    }
    Text("Sample every", style = MaterialTheme.typography.titleSmall)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf(2, 5, 10, 30).forEach { seconds ->
            FilterChip(
                selected = state.intervalSeconds == seconds,
                onClick = { onInterval(seconds) },
                label = { Text("${seconds}s") },
            )
        }
    }
}

@Composable
private fun SamplingStatus(state: SessionUiState) {
    Text("Capture", style = MaterialTheme.typography.titleMedium)
    Text(
        when {
            state.activeSessionId == null -> "Create a session to begin."
            state.sampling -> "Sampling every ${state.intervalSeconds}s. " +
                "${state.framesStored} stored, ${state.framesMissed} missed."
            else -> "${state.framesStored} stored, ${state.framesMissed} missed. " +
                "Last frame: ${state.lastFrameAt ?: "none yet"}."
        },
    )
    Text("On this phone: ${state.pendingCount} waiting, ${state.syncedCount} synced.")
}

@Composable
private fun PairingSection(
    state: SessionUiState,
    onPair: (host: String, port: String, code: String, fingerprint: String) -> Unit,
    onUnpair: () -> Unit,
    onScanQr: () -> Unit,
) {
    Text("Workstation", style = MaterialTheme.typography.titleMedium)
    val pairing = state.pairing
    if (pairing != null) {
        Text("Paired with ${pairing.workstationId} at ${pairing.baseUrl}.")
        OutlinedButton(onClick = onUnpair) {
            Text("Unpair")
        }
        return
    }
    Button(onClick = onScanQr, modifier = Modifier.fillMaxWidth()) {
        Text("Scan the workstation QR")
    }
    Text("Or pair manually:")
    var host by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("8443") }
    var code by remember { mutableStateOf("") }
    var fingerprint by remember { mutableStateOf("") }
    OutlinedTextField(
        value = host,
        onValueChange = { host = it },
        label = { Text("Workstation host or IP") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = port,
        onValueChange = { port = it },
        label = { Text("Port") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = code,
        onValueChange = { code = it },
        label = { Text("Pairing code from the receiver window") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = fingerprint,
        onValueChange = { fingerprint = it },
        label = { Text("Certificate fingerprint from the receiver window") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Button(
        onClick = { onPair(host, port, code, fingerprint) },
        enabled = !state.pairingBusy,
    ) {
        Text(if (state.pairingBusy) "Pairing…" else "Pair")
    }
}

@Composable
private fun UploadSection(
    state: SessionUiState,
    onUpload: () -> Unit,
) {
    Text("Sync", style = MaterialTheme.typography.titleMedium)
    val canUpload = state.pairing != null && state.activeSessionId != null &&
        state.pendingCount > 0 && !state.uploading
    Button(onClick = onUpload, enabled = canUpload) {
        Text(if (state.uploading) "Uploading…" else "Upload waiting frames")
    }
    state.lastUpload?.let { Text(it) }
    if (state.pairing == null) {
        Text("Pair a workstation to enable uploads.")
    }
}
