package org.anastylos.capture.camera

import android.hardware.camera2.CameraMetadata

/**
 * Actually applied capture parameters sampled from a Camera2
 * CaptureResult (guided analysis path). Null fields mean "not
 * reported by this result". Displayed next to the request so the
 * operator sees requested vs effective.
 */
data class AppliedSettings(
    val exposureNs: Long?,
    val iso: Int?,
    val focusDiopters: Float?,
    val aeMode: Int?,
    val afMode: Int?,
    val awbMode: Int?,
    val evSteps: Int?,
)

fun formatApplied(a: AppliedSettings): String {
    val parts = mutableListOf<String>()
    if (a.exposureNs != null && a.iso != null) {
        parts += "${formatExposure(a.exposureNs)} · ISO${a.iso}"
    } else if (a.exposureNs != null) {
        parts += formatExposure(a.exposureNs)
    } else if (a.iso != null) {
        parts += "ISO${a.iso}"
    }
    a.aeMode?.let { parts += "AE ${aeName(it)}" }
    a.afMode?.let { parts += "AF ${afName(it)}" }
    if (a.focusDiopters != null && a.afMode == CameraMetadata.CONTROL_AF_MODE_OFF) {
        parts += formatFocus(a.focusDiopters)
    }
    a.awbMode?.let { parts += "AWB ${awbName(it)}" }
    if (a.evSteps != null && a.evSteps != 0) parts += "EV$a.evSteps"
    return parts.joinToString(" · ").ifEmpty { "—" }
}

private fun aeName(mode: Int): String = when (mode) {
    CameraMetadata.CONTROL_AE_MODE_OFF -> "off"
    CameraMetadata.CONTROL_AE_MODE_ON -> "on"
    CameraMetadata.CONTROL_AE_MODE_ON_AUTO_FLASH -> "flash-auto"
    CameraMetadata.CONTROL_AE_MODE_ON_ALWAYS_FLASH -> "flash"
    else -> mode.toString()
}

private fun afName(mode: Int): String = when (mode) {
    CameraMetadata.CONTROL_AF_MODE_OFF -> "off"
    CameraMetadata.CONTROL_AF_MODE_AUTO -> "auto"
    CameraMetadata.CONTROL_AF_MODE_MACRO -> "macro"
    CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_VIDEO -> "cont-v"
    CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE -> "cont-p"
    CameraMetadata.CONTROL_AF_MODE_EDOF -> "edof"
    else -> mode.toString()
}

private fun awbName(mode: Int): String = when (mode) {
    CameraMetadata.CONTROL_AWB_MODE_OFF -> "off"
    CameraMetadata.CONTROL_AWB_MODE_AUTO -> "auto"
    CameraMetadata.CONTROL_AWB_MODE_INCANDESCENT -> "incan"
    CameraMetadata.CONTROL_AWB_MODE_FLUORESCENT -> "fluor"
    CameraMetadata.CONTROL_AWB_MODE_WARM_FLUORESCENT -> "warm"
    CameraMetadata.CONTROL_AWB_MODE_DAYLIGHT -> "day"
    CameraMetadata.CONTROL_AWB_MODE_CLOUDY_DAYLIGHT -> "cloud"
    CameraMetadata.CONTROL_AWB_MODE_TWILIGHT -> "twilight"
    CameraMetadata.CONTROL_AWB_MODE_SHADE -> "shade"
    else -> mode.toString()
}
