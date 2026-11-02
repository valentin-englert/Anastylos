package org.anastylos.capture.ui.guided

import androidx.compose.runtime.Composable
import org.anastylos.capture.pairing.ScanKind
import org.anastylos.capture.pairing.classifyScan
import org.anastylos.capture.ui.pair.ScannerBody

/** Shown when a scanned code is neither a pairing code nor a session code. */
const val UNKNOWN_CODE_MESSAGE = "That QR code is neither a pairing code nor a session code."

/**
 * The one scanner: a session code attaches the session, a pairing code starts pairing
 * ([onPairingCode] gets the scanned text), anything else reports [UNKNOWN_CODE_MESSAGE] via [onUnknown].
 */
@Composable
fun SessionQrScanScreen(
    onSession: (String) -> Unit,
    onPairingCode: (String) -> Unit,
    onUnknown: () -> Unit,
    onCancel: () -> Unit,
) {
    ScannerBody(
        onQrText = { text ->
            when (val kind = classifyScan(text)) {
                is ScanKind.Session -> onSession(kind.id)
                ScanKind.Pairing -> onPairingCode(text.orEmpty().trim())
                ScanKind.Unknown -> onUnknown()
            }
        },
        onCancel = onCancel,
    )
}
