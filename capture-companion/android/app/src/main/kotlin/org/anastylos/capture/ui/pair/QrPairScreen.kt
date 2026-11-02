package org.anastylos.capture.ui.pair

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview as CameraXPreview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import java.time.Instant
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.delay
import org.anastylos.capture.pairing.PendingRequest
import org.anastylos.capture.sync.QrPairPayload

/**
 * QR pairing route: state collector plus the camera scanner.
 *
 * The confirm step shows the workstation id, endpoint, and code
 * expiry — enough to cross-check the receiver window — but never
 * the pairing code, the SPKI pin, or the Bearer [REDACTED] Those are verified
 * automatically; showing them would only invite mistyping.
 */
@Composable
fun QrPairRoute(viewModel: PairViewModel, onDone: () -> Unit, onSessionCode: (String) -> Unit) {
    val state by viewModel.state.collectAsState()
    QrPairScreen(
        screen = state.screen,
        onConnectClicked = { viewModel.onConnectClicked() },
        onCancelScan = { viewModel.onCancelScan() },
        onQrText = { text ->
            // One scanner for both code kinds: a session code attaches, anything else goes through pairing.
            val kind = org.anastylos.capture.pairing.classifyScan(text)
            if (kind is org.anastylos.capture.pairing.ScanKind.Session) onSessionCode(kind.id) else viewModel.onScanText(text)
        },
        onRescan = { viewModel.onRescan() },
        onBackFromConfirm = { viewModel.onBackFromConfirm() },
        onConfirm = { viewModel.onConfirm() },
        onRetry = { viewModel.onRetry() },
        onCancelPairing = { viewModel.onCancelPairing() },
        onDone = onDone,
    )
}

@Composable
fun QrPairScreen(
    screen: PairScreen,
    onConnectClicked: () -> Unit,
    onCancelScan: () -> Unit,
    onQrText: (String?) -> Unit,
    onRescan: () -> Unit,
    onBackFromConfirm: () -> Unit,
    onConfirm: () -> Unit,
    onRetry: () -> Unit,
    onCancelPairing: () -> Unit,
    onDone: () -> Unit,
) {
    when (screen) {
        is PairScreen.Idle -> IdleBody(onConnectClicked, onDone)
        is PairScreen.Scanning -> ScannerBody(onQrText, onCancelScan)
        is PairScreen.Invalid -> MessageBody(
            title = "That code did not scan",
            message = screen.message,
            primaryLabel = "Scan again",
            onPrimary = onRescan,
            onDone = onDone,
        )
        is PairScreen.Confirm -> ConfirmBody(
            payload = screen.payload,
            onConfirm = onConfirm,
            onBack = onBackFromConfirm,
        )
        is PairScreen.Pairing -> PairingBody(screen, onCancelPairing)
        is PairScreen.Paired -> MessageBody(
            title = "Connected",
            message = "This phone is paired with workstation ${screen.workstationId}. " +
                "Approve it on the receiver if asked, then sync from the session screen.",
            primaryLabel = "Continue",
            onPrimary = onDone,
            onDone = onDone,
        )
        is PairScreen.Failed -> FailedBody(screen, onRetry, onRescan, onDone)
    }
}

@Composable
private fun IdleBody(onConnectClicked: () -> Unit, onDone: () -> Unit) {
    CenteredColumn {
        Text("Connect workstation", style = MaterialTheme.typography.headlineSmall)
        Text(
            "On the workstation, open the receiver and choose “Connect phone”. " +
                "Point this phone at the QR code it shows — no addresses or codes to type.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Button(
            onClick = onConnectClicked,
            modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
        ) {
            Text("Scan the workstation QR")
        }
        TextButton(onClick = onDone, modifier = Modifier.heightIn(min = 48.dp)) {
            Text("Not now")
        }
    }
}

@Composable
private fun MessageBody(
    title: String,
    message: String,
    primaryLabel: String,
    onPrimary: () -> Unit,
    onDone: () -> Unit,
) {
    CenteredColumn {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        Text(message, style = MaterialTheme.typography.bodyMedium)
        Button(
            onClick = onPrimary,
            modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
        ) {
            Text(primaryLabel)
        }
        TextButton(onClick = onDone, modifier = Modifier.heightIn(min = 48.dp)) {
            Text("Close")
        }
    }
}

@Composable
private fun FailedBody(
    screen: PairScreen.Failed,
    onRetry: () -> Unit,
    onRescan: () -> Unit,
    onDone: () -> Unit,
) {
    CenteredColumn {
        Text("Pairing failed", style = MaterialTheme.typography.headlineSmall)
        Text(screen.message, style = MaterialTheme.typography.bodyMedium)
        if (screen.retryAllowed) {
            Button(
                onClick = onRetry,
                modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
            ) {
                Text("Retry")
            }
        }
        OutlinedButton(
            onClick = onRescan,
            modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
        ) {
            Text("Scan a new code")
        }
        TextButton(onClick = onDone, modifier = Modifier.heightIn(min = 48.dp)) {
            Text("Close")
        }
    }
}

@Composable
private fun ConfirmBody(
    payload: QrPairPayload,
    onConfirm: () -> Unit,
    onBack: () -> Unit,
) {
    var now by remember { mutableStateOf(Instant.now()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1_000)
            now = Instant.now()
        }
    }
    CenteredColumn {
        Text("Workstation found", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Check this matches the receiver window, then connect. " +
                "The phone verifies the workstation's identity automatically.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            payload.workstationId,
            style = MaterialTheme.typography.bodyMedium,
            fontFamily = FontFamily.Monospace,
        )
        Text(
            "${payload.host}:${payload.port} · " +
                formatCodeExpiry(payload.expiresAt, now),
            style = MaterialTheme.typography.bodySmall,
        )
        Button(
            onClick = onConfirm,
            modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
        ) {
            Text("Connect to this workstation")
        }
        OutlinedButton(
            onClick = onBack,
            modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
        ) {
            Text("Back to scanner")
        }
    }
}

@Composable
private fun PairingBody(screen: PairScreen.Pairing, onCancel: () -> Unit) {
    CenteredColumn(horizontal = Alignment.CenterHorizontally) {
        CircularProgressIndicator(modifier = Modifier.size(48.dp))
        Text("Waiting for approval", style = MaterialTheme.typography.headlineSmall)
        Text(
            if (screen.request == null) {
                "Redeeming the code…"
            } else if (screen.verifying) {
                "Approved — confirming the connection…"
            } else {
                "Approve “${screen.payload.workstationId}” on the workstation to finish pairing."
            },
            style = MaterialTheme.typography.bodyMedium,
        )
        OutlinedButton(
            onClick = onCancel,
            modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
        ) {
            Text("Cancel")
        }
    }
}

@Composable
private fun CenteredColumn(
    horizontal: Alignment.Horizontal = Alignment.Start,
    content: @Composable () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
        horizontalAlignment = horizontal,
    ) {
        content()
    }
}

@Composable
internal fun ScannerBody(onQrText: (String?) -> Unit, onCancel: () -> Unit) {
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

    if (!cameraGranted) {
        CenteredColumn {
            Text("Camera access", style = MaterialTheme.typography.headlineSmall)
            Text(
                "The scanner needs the camera to read the workstation QR code. " +
                    "Nothing is recorded or uploaded while scanning.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Button(
                onClick = { permissionLauncher.launch(Manifest.permission.CAMERA) },
                modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
            ) {
                Text("Grant camera access")
            }
            TextButton(onClick = onCancel, modifier = Modifier.heightIn(min = 48.dp)) {
                Text("Cancel")
            }
        }
        return
    }
    CameraScanner(onQrText = onQrText, onCancel = onCancel)
}

/**
 * CameraX preview with an ML Kit QR analyzer (bundled model — no
 * first-run download, fully offline).
 *
 * Single-fire: the first decoded QR text wins; later frames are
 * dropped without being closed late, and the ViewModel ignores
 * callbacks once it leaves [PairScreen.Scanning]. Binding follows
 * the composition lifecycle — leaving the screen unbinds only its own use cases
 * and closes the scanner.
 *
 */
@Composable
private fun CameraScanner(onQrText: (String?) -> Unit, onCancel: () -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val previewView = remember { PreviewView(context) }
    var camera by remember { mutableStateOf<Camera?>(null) }
    var torchOn by remember { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    val fired = remember { AtomicBoolean(false) }
    val scanner = remember {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build(),
        )
    }

    DisposableEffect(previewView) {
        var provider: ProcessCameraProvider? = null
        var boundPreview: CameraXPreview? = null
        var boundAnalysis: ImageAnalysis? = null
        // getInstance() itself throws when CameraX has no backend on the
        // device — that must degrade to the error screen, never crash.
        val providerFuture = try {
            ProcessCameraProvider.getInstance(context)
        } catch (e: Exception) {
            cameraError = e.message ?: "camera unavailable"
            null
        }
        if (providerFuture == null) {
            return@DisposableEffect onDispose { scanner.close() }
        }
        val listener = Runnable {
            try {
                provider = providerFuture.get()
                val bound = provider ?: return@Runnable
                val preview = CameraXPreview.Builder().build()
                preview.setSurfaceProvider(previewView.surfaceProvider)
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                val mainExecutor = ContextCompat.getMainExecutor(context)
                analysis.setAnalyzer(
                    mainExecutor,
                    QrAnalyzer(scanner, fired, onQrText),
                )
                bound.unbindAll()
                boundPreview = preview
                boundAnalysis = analysis
                camera = bound.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    analysis,
                )
            } catch (e: Exception) {
                cameraError = e.message ?: "camera unavailable"
            }
        }
        providerFuture.addListener(listener, ContextCompat.getMainExecutor(context))
        onDispose {
            try {
                // Selective: a dispose-time unbindAll would detach the
                // next screen's camera (entering binds before leaving
                // disposes on forward navigation).
                val owned = listOfNotNull(boundPreview, boundAnalysis)
                if (owned.isNotEmpty()) {
                    provider?.unbind(*owned.toTypedArray())
                }
            } catch (_: Exception) {
                // Best effort; the process may already be torn down.
            }
            scanner.close()
        }
    }

    val error = cameraError
    if (error != null) {
        CenteredColumn {
            Text("Scanner unavailable", style = MaterialTheme.typography.headlineSmall)
            Text(
                "The camera could not start ($error). You can still pair by typing " +
                    "the code on the session screen.",
                style = MaterialTheme.typography.bodyMedium,
            )
            OutlinedButton(
                onClick = onCancel,
                modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
            ) {
                Text("Back")
            }
        }
        return
    }

    Box(modifier = Modifier.fillMaxSize()) {
        AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
        FramingOverlay()
        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            verticalArrangement = Arrangement.Bottom,
        ) {
            Text(
                "Aim at the workstation QR",
                color = Color.White,
                style = MaterialTheme.typography.bodyLarge,
            )
            Spacer(modifier = Modifier.height(12.dp))
            OutlinedButton(
                onClick = {
                    val cam = camera
                    if (cam != null && cam.cameraInfo.hasFlashUnit()) {
                        cam.cameraControl.enableTorch(!torchOn)
                        torchOn = !torchOn
                    }
                },
                modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth(),
            ) {
                Text(if (torchOn) "Turn flashlight off" else "Turn flashlight on")
            }
            Spacer(modifier = Modifier.height(8.dp))
            TextButton(onClick = onCancel, modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth()) {
                Text("Cancel", color = Color.White)
            }
        }
    }
}

/**
 * Single-fire QR analyzer: the first decoded QR text wins; every
 * frame is closed exactly once, on ML Kit completion.
 *
 * `ExperimentalGetImage` is the long-stable, documented CameraX→ML Kit
 * interop surface (raw MediaImage for the barcode analyzer); the
 * opt-in acknowledges the annotation, not instability.
 */
private class QrAnalyzer(
    private val scanner: BarcodeScanner,
    private val fired: AtomicBoolean,
    private val onQrText: (String?) -> Unit,
) : ImageAnalysis.Analyzer {
    @ExperimentalGetImage
    override fun analyze(proxy: ImageProxy) {
        if (fired.get()) {
            proxy.close()
            return
        }
        val media = proxy.image
        if (media == null) {
            proxy.close()
            return
        }
        val image = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
        scanner.process(image)
            .addOnSuccessListener { barcodes ->
                val text = barcodes
                    .firstOrNull { it.format == Barcode.FORMAT_QR_CODE }
                    ?.rawValue
                if (text != null && fired.compareAndSet(false, true)) {
                    onQrText(text)
                }
            }
            .addOnCompleteListener { proxy.close() }
    }
}

/** Dimmed scrim with a clear scan window and corner marks. */
@Composable
private fun FramingOverlay() {
    Canvas(modifier = Modifier.fillMaxSize()) {
        val side = size.minDimension * 0.68f
        val left = (size.width - side) / 2f
        val top = (size.height - side) / 2f
        val window = Rect(left, top, left + side, top + side)
        val clearing = Path().apply {
            addRoundRect(RoundRect(window, CornerRadius(24f, 24f)))
        }
        drawRect(Color.Black.copy(alpha = 0.55f))
        drawPath(clearing, Color.Transparent, blendMode = BlendMode.Clear)
        val mark = side * 0.12f
        val stroke = 8f
        val white = Color.White
        // Four corner brackets around the window.
        drawLine(white, Offset(left, top + mark), Offset(left, top), stroke)
        drawLine(white, Offset(left, top), Offset(left + mark, top), stroke)
        drawLine(white, Offset(window.right - mark, top), Offset(window.right, top), stroke)
        drawLine(white, Offset(window.right, top), Offset(window.right, top + mark), stroke)
        drawLine(white, Offset(left, window.bottom - mark), Offset(left, window.bottom), stroke)
        drawLine(white, Offset(left, window.bottom), Offset(left + mark, window.bottom), stroke)
        drawLine(
            white,
            Offset(window.right - mark, window.bottom),
            Offset(window.right, window.bottom),
            stroke,
        )
        drawLine(
            white,
            Offset(window.right, window.bottom - mark),
            Offset(window.right, window.bottom),
            stroke,
        )
    }
}

private fun previewPayload(): QrPairPayload =
    QrPairPayload(
        workstationId = "9f2b7a01c3d44e5f8a0b1c2d3e4f5061",
        baseUrl = "https://192.168.1.20:8443",
        host = "192.168.1.20",
        port = 8443,
        spkiSha256 = ByteArray(32) { it.toByte() },
        pairingCode = "preview only — never rendered",
        expiresAt = Instant.now().plusSeconds(272),
    )

@Preview(showBackground = true, name = "Pair idle")
@Composable
private fun IdlePreview() {
    QrPairScreen(
        screen = PairScreen.Idle,
        onConnectClicked = {},
        onCancelScan = {},
        onQrText = {},
        onRescan = {},
        onBackFromConfirm = {},
        onConfirm = {},
        onRetry = {},
        onCancelPairing = {},
        onDone = {},
    )
}

@Preview(showBackground = true, name = "Pair confirm")
@Composable
private fun ConfirmPreview() {
    QrPairScreen(
        screen = PairScreen.Confirm(previewPayload()),
        onConnectClicked = {},
        onCancelScan = {},
        onQrText = {},
        onRescan = {},
        onBackFromConfirm = {},
        onConfirm = {},
        onRetry = {},
        onCancelPairing = {},
        onDone = {},
    )
}

@Preview(showBackground = true, name = "Pair waiting")
@Composable
private fun PairingPreview() {
    QrPairScreen(
        screen = PairScreen.Pairing(
            previewPayload(),
            PendingRequest(
                "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                Instant.now().plusSeconds(300),
            ),
        ),
        onConnectClicked = {},
        onCancelScan = {},
        onQrText = {},
        onRescan = {},
        onBackFromConfirm = {},
        onConfirm = {},
        onRetry = {},
        onCancelPairing = {},
        onDone = {},
    )
}

@Preview(showBackground = true, name = "Pair failed")
@Composable
private fun FailedPreview() {
    QrPairScreen(
        screen = PairScreen.Failed(
            "Could not reach the workstation. Check both devices share the same Wi-Fi, then retry.",
            previewPayload(),
            null,
            true,
        ),
        onConnectClicked = {},
        onCancelScan = {},
        onQrText = {},
        onRescan = {},
        onBackFromConfirm = {},
        onConfirm = {},
        onRetry = {},
        onCancelPairing = {},
        onDone = {},
    )
}
