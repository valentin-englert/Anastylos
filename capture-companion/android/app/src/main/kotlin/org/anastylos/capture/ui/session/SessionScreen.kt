package org.anastylos.capture.ui.session

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import org.anastylos.capture.R
import org.anastylos.capture.ui.camera.ScrimIconButton
import org.anastylos.capture.ui.theme.Glyph
import org.anastylos.capture.ui.theme.Palette

/**
 * Home: whether the phone is connected, and the two ways to work.
 * Everything else (sessions, sampling interval, camera settings)
 * lives where it is used, behind the camera's menu.
 */
@Composable
fun SessionScreen(
    viewModel: SessionViewModel,
    onConnect: () -> Unit,
    onGuided: () -> Unit,
    onSampling: () -> Unit,
    onReplayGuide: () -> Unit,
) {
    val state by viewModel.state.collectAsState()
    LaunchedEffect(Unit) { viewModel.refreshPairing() }
    var manualPairing by remember { mutableStateOf(false) }
    val paired = state.pairing != null

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Palette.Ground)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(24.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Image(painterResource(R.drawable.ic_launcher_foreground), contentDescription = null, modifier = Modifier.size(44.dp))
            Spacer(Modifier.weight(1f))
            if (!paired) ScrimIconButton(Glyph.Qr, "Pair with the workstation", onClick = onConnect)
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.Center,
        ) {
            Text("ANASTYLOS", style = MaterialTheme.typography.headlineLarge, color = Palette.Paper)
            Spacer(Modifier.height(8.dp))
            Text("A workbench that remembers what you took apart.", style = MaterialTheme.typography.bodyLarge, color = Palette.Muted)
            Spacer(Modifier.height(28.dp))
            ConnectionStatus(paired, state.pairing?.workstationId)
            if (!paired) {
                Spacer(Modifier.height(12.dp))
                Text(
                    "Tap the QR icon and scan the code on the workstation.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Palette.Muted,
                )
                TextButton(onClick = { manualPairing = true }) { Text("Enter pairing details instead", color = Palette.Ochre) }
            } else {
                TextButton(onClick = { viewModel.unpair() }) { Text("Disconnect", color = Palette.Muted) }
            }
        }
        val error = state.error ?: state.pairingError
        if (error != null) {
            Text(error, color = Palette.Brick, style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = { viewModel.dismissError() }) { Text("Dismiss", color = Palette.Paper) }
        }
        if (paired && state.pendingCount > 0) {
            Row(Modifier.fillMaxWidth().padding(bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "${state.pendingCount} photos waiting",
                    style = MaterialTheme.typography.bodyLarge,
                    color = Palette.Paper,
                    modifier = Modifier.weight(1f),
                )
                OutlinedButton(
                    onClick = { viewModel.uploadPending() },
                    enabled = !state.uploading && state.activeSessionId != null,
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Palette.Paper),
                ) { Text(if (state.uploading) "Uploading…" else "Upload") }
            }
        }
        Button(onClick = onGuided, modifier = Modifier.fillMaxWidth().height(64.dp)) {
            Text("Guided capture", style = MaterialTheme.typography.titleLarge)
        }
        Spacer(Modifier.height(12.dp))
        OutlinedButton(
            onClick = onSampling,
            modifier = Modifier.fillMaxWidth().height(56.dp),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = Palette.Paper),
        ) { Text("Timed photos", style = MaterialTheme.typography.titleMedium) }
        TextButton(onClick = onReplayGuide, modifier = Modifier.align(Alignment.CenterHorizontally)) {
            Text("How it works", color = Palette.Muted)
        }
    }
    if (manualPairing) {
        ManualPairingDialog(
            busy = state.pairingBusy,
            onPair = { host, port, code, fingerprint -> viewModel.pair(host, port, code, fingerprint) },
            onDismiss = { manualPairing = false },
        )
    }
}

@Composable
private fun ConnectionStatus(paired: Boolean, workstationId: String?) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.size(12.dp).background(if (paired) Palette.Sage else Palette.Brick, CircleShape))
        Text(
            if (paired) "Connected to ${workstationId ?: "workstation"}" else "Not connected",
            style = MaterialTheme.typography.titleMedium,
            color = Palette.Paper,
        )
    }
}

@Composable
private fun ManualPairingDialog(
    busy: Boolean,
    onPair: (host: String, port: String, code: String, fingerprint: String) -> Unit,
    onDismiss: () -> Unit,
) {
    var host by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("8443") }
    var code by remember { mutableStateOf("") }
    var fingerprint by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Palette.SurfaceRaised,
        title = { Text("Pair manually", color = Palette.Paper) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(host, { host = it }, label = { Text("Workstation host or IP") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(port, { port = it }, label = { Text("Port") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(code, { code = it }, label = { Text("Pairing code") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(fingerprint, { fingerprint = it }, label = { Text("Certificate fingerprint") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton(onClick = { onPair(host, port, code, fingerprint); onDismiss() }, enabled = !busy) {
                Text(if (busy) "Pairing…" else "Pair", color = Palette.Ochre)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = Palette.Muted) } },
    )
}
