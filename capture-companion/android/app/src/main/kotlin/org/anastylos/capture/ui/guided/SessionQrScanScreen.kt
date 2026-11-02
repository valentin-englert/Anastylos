package org.anastylos.capture.ui.guided

import androidx.compose.runtime.Composable
import org.anastylos.capture.ui.pair.ScannerBody

private val SESSION_ID = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

/** The first session id (UUID) inside a QR payload, whether bare, in a link or in JSON. */
fun sessionIdFromQr(text: String?): String? = text?.let { SESSION_ID.find(it)?.value?.lowercase() }

/**
 * Scans the workstation's session QR code. [onResult] gets the session id,
 * or [INVALID_SESSION_QR] when the code holds none; cancel returns nothing.
 */
@Composable
fun SessionQrScanScreen(onResult: (String) -> Unit, onCancel: () -> Unit) {
    ScannerBody(
        onQrText = { text -> onResult(sessionIdFromQr(text) ?: INVALID_SESSION_QR) },
        onCancel = onCancel,
    )
}
