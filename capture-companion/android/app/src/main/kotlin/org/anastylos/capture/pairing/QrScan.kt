package org.anastylos.capture.pairing

/**
 * What one ML Kit barcode callback means for the pairing flow.
 *
 * The analyzer hands us the raw text of the first QR barcode per
 * frame (or null when the frame held none); [interpretScannedText]
 * turns that into a decision. Cancellation is a UI event (back /
 * Cancel button), not a scanner outcome, so it lives in the
 * ViewModel rather than here.
 */
sealed interface QrScanResult {
    /** Scannable text; still untrusted until the payload parser accepts it. */
    data class Valid(val rawText: String) : QrScanResult

    /** A barcode decoded but carried nothing usable. */
    data class Unreadable(val reason: String) : QrScanResult
}

/**
 * Interpret one analyzer callback.
 *
 * @param rawValue the QR barcode's raw value, or null when the frame
 *   held no QR barcode. Null frames are routine (the user is still
 *   aiming) and stay silent; only a *decoded but empty* barcode is
 *   [QrScanResult.Unreadable].
 * @return null when the frame simply held no QR code — keep scanning.
 */
fun interpretScannedText(rawValue: String?): QrScanResult? {
    if (rawValue == null) return null
    val text = rawValue.trim()
    if (text.isEmpty()) {
        return QrScanResult.Unreadable("that code held no readable text — aim at the workstation QR")
    }
    return QrScanResult.Valid(text)
}

/** Mirrors `QR_DEVICE_LABEL_MAX` in `anastylos_capture_receiver.py`. */
const val DEVICE_LABEL_MAX = 64

/**
 * Build the operator-facing device label for the redeem request from
 * `Build.MANUFACTURER` / `Build.MODEL`.
 *
 * The receiver's `clean_device_label` rejects labels with control
 * characters or overlong text, so this builder never emits those:
 * controls are stripped, whitespace is collapsed, overlong labels
 * are truncated. Never blank — falls back to a generic name.
 */
fun buildDeviceLabel(manufacturer: String?, model: String?): String {
    val combined = listOfNotNull(manufacturer, model).joinToString(" ")
    val printable = combined.filter { it >= ' ' && it != '\u007F' }
    val collapsed = printable.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
    val trimmed = collapsed.take(DEVICE_LABEL_MAX).trim()
    return trimmed.ifEmpty { "Android phone" }
}
