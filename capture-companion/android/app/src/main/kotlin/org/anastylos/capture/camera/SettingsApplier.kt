package org.anastylos.capture.camera

import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureRequest
import android.util.Log
import androidx.camera.camera2.interop.Camera2CameraControl
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.CaptureRequestOptions
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import androidx.camera.core.ImageCapture

/**
 * Applies sanitized [CameraSettings] to a bound camera. Single
 * owner of all control requests: one interop bundle per apply
 * (deterministic auto/manual switching, EV included), plus the
 * non-overlapping CameraX controls (zoom, torch). Never sets the same
 * key through both paths. Returns false (no throw) on failure.
 */
interface SettingsApplier {
    fun apply(camera: Camera, settings: CameraSettings, caps: CameraCapabilities): Boolean
}

/**
 * Still-capture slice of [CameraSettings]: bind-time request keys
 * for ImageCapture plus the capture mode. Mirrors
 * [CameraXSettingsApplier.apply] key-for-key so the repeating
 * (preview/analysis) and single (still) requests never diverge:
 * runtime interop options demonstrably do NOT reach still
 * captures on the SM-A536B (EXIF proof, CAMERA-02), hence the
 * bind-time extender. MAXIMIZE_QUALITY runs a precapture
 * metering pass that would re-converge AE behind manual
 * settings, so manual exposure uses MINIMIZE_LATENCY.
 */
data class StillOptions(
    val captureMode: Int,
    val aeMode: Int?,
    val exposureNs: Long?,
    val iso: Int?,
    val afMode: Int?,
    val focusDiopters: Float?,
    val awbMode: Int?,
    /** AE compensation steps; 0 in manual mode or when EV unsupported. */
    val evSteps: Int,
)

fun stillOptionsFor(settings: CameraSettings, caps: CameraCapabilities): StillOptions {
    val manual = settings.exposureMode == ExposureMode.MANUAL && caps.supportsManualExposure
    val manualFocus = settings.focusMode == FocusMode.MANUAL && caps.supportsManualFocus
    return StillOptions(
        captureMode = if (manual) {
            ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY
        } else {
            ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY
        },
        aeMode = if (manual) {
            CameraMetadata.CONTROL_AE_MODE_OFF
        } else {
            CameraMetadata.CONTROL_AE_MODE_ON
        },
        exposureNs = if (manual) settings.exposureNs else null,
        iso = if (manual) settings.iso else null,
        afMode = when {
            manualFocus -> CameraMetadata.CONTROL_AF_MODE_OFF
            caps.supportsAutoFocusModes -> CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE
            else -> null
        },
        focusDiopters = if (manualFocus) settings.focusDiopters else null,
        awbMode = settings.awbMode.camera2.takeIf { it in caps.awbModes },
        evSteps = if (settings.exposureMode == ExposureMode.AUTO && caps.supportsEv) {
            settings.evSteps
        } else {
            0
        },
    )
}

/** Frame rate the tracking stream needs for fresh poses (GUIDE-01). */
const val TRACKING_FPS = 30

/**
 * AE target-FPS range for auto exposure on the repeating stream: the
 * range with the highest lower bound not above [TRACKING_FPS] (ties:
 * lowest upper bound), or null when none exists. Without it the
 * SM-A536B AE stretches exposure to ~13 fps in room light; the sensor
 * timestamp is exposure start, so every frame then arrives ~150 ms
 * old and no pose can meet the frozen 150 ms freshness gate. A fixed
 * lower bound makes AE trade gain (noise) for exposure time instead.
 */
fun trackingFpsRange(ranges: List<Pair<Int, Int>>): Pair<Int, Int>? =
    ranges.filter { (lower, upper) -> lower in 1..upper && upper <= TRACKING_FPS }
        .sortedWith(compareByDescending<Pair<Int, Int>> { it.first }.thenBy { it.second })
        .firstOrNull()

/** Bakes [StillOptions] into an ImageCapture builder. Returns the builder. */
@ExperimentalCamera2Interop
fun applyStillOptions(builder: ImageCapture.Builder, opts: StillOptions): ImageCapture.Builder {
    val ext = Camera2Interop.Extender(builder)
    opts.aeMode?.let { ext.setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE, it) }
    opts.exposureNs?.let { ext.setCaptureRequestOption(CaptureRequest.SENSOR_EXPOSURE_TIME, it) }
    opts.iso?.let { ext.setCaptureRequestOption(CaptureRequest.SENSOR_SENSITIVITY, it) }
    opts.afMode?.let { ext.setCaptureRequestOption(CaptureRequest.CONTROL_AF_MODE, it) }
    opts.focusDiopters?.let { ext.setCaptureRequestOption(CaptureRequest.LENS_FOCUS_DISTANCE, it) }
    opts.awbMode?.let { ext.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, it) }
    ext.setCaptureRequestOption(CaptureRequest.CONTROL_AE_EXPOSURE_COMPENSATION, opts.evSteps)
    return builder
}

class CameraXSettingsApplier : SettingsApplier {
    @ExperimentalCamera2Interop
    override fun apply(camera: Camera, settings: CameraSettings, caps: CameraCapabilities): Boolean {
        return try {
            val options = CaptureRequestOptions.Builder()
            if (settings.exposureMode == ExposureMode.MANUAL && caps.supportsManualExposure) {
                options.setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_OFF)
                options.setCaptureRequestOption(CaptureRequest.SENSOR_EXPOSURE_TIME, settings.exposureNs)
                options.setCaptureRequestOption(CaptureRequest.SENSOR_SENSITIVITY, settings.iso)
            } else {
                options.setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_ON)
                trackingFpsRange(caps.aeFpsRanges)?.let { (lower, upper) ->
                    options.setCaptureRequestOption(
                        CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, android.util.Range(lower, upper),
                    )
                }
            if (settings.exposureMode == ExposureMode.AUTO && caps.supportsEv) {
                options.setCaptureRequestOption(
                    CaptureRequest.CONTROL_AE_EXPOSURE_COMPENSATION, settings.evSteps,
                )
            } else {
                options.setCaptureRequestOption(CaptureRequest.CONTROL_AE_EXPOSURE_COMPENSATION, 0)
            }
            }
            if (settings.focusMode == FocusMode.MANUAL && caps.supportsManualFocus) {
                options.setCaptureRequestOption(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_OFF)
                options.setCaptureRequestOption(CaptureRequest.LENS_FOCUS_DISTANCE, settings.focusDiopters)
            } else if (caps.supportsAutoFocusModes) {
                options.setCaptureRequestOption(
                    CaptureRequest.CONTROL_AF_MODE,
                    CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE,
                )
            }
            if (settings.awbMode.camera2 in caps.awbModes) {
                options.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, settings.awbMode.camera2)
            }
            Camera2CameraControl.from(camera.cameraControl).setCaptureRequestOptions(options.build())
            val control = camera.cameraControl
            control.setLinearZoom(settings.linearZoom)
            val torch = settings.torch && caps.flashAvailable && hasFlashUnit(camera)
            control.enableTorch(torch)
            Log.i(
                CAMERA_LOG_TAG,
                "apply exp=${settings.exposureMode}@${settings.iso}/" +
                    "${formatExposure(settings.exposureNs)}/ev${settings.evSteps} " +
                    "focus=${settings.focusMode}@${formatFocus(settings.focusDiopters)} " +
                    "awb=${settings.awbMode} zoom=${settings.linearZoom} torch=$torch " +
                    "fps=${trackingFpsRange(caps.aeFpsRanges)?.takeIf { settings.exposureMode == ExposureMode.AUTO }}",
            )
            true
        } catch (e: Exception) {
            Log.w(CAMERA_LOG_TAG, "settings apply failed", e)
            false
        }
    }

    private fun hasFlashUnit(camera: Camera): Boolean {
        return try {
            camera.cameraInfo.hasFlashUnit()
        } catch (_: Exception) {
            false
        }
    }
}
