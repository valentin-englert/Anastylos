package org.anastylos.capture.camera

import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraMetadata
import android.graphics.ImageFormat
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.lifecycle.ProcessCameraProvider

/**
 * One CameraX-exposed camera usable for Preview/Capture. Populated
 * from Camera2 characteristics; a camera whose characteristics
 * cannot be read is not listed (never assumed usable).
 */
data class UsableCamera(
    /** Camera2 camera ID as seen by CameraX (selects via camera filter). */
    val id: String,
    /** Accurate operator label, e.g. "Back 0 · 5.2 mm". */
    val label: String,
    /** Camera2 LENS_FACING value (matches CameraX lens-facing ints). */
    val lensFacing: Int,
    val focalMm: Float?,
    val sensorWidthMm: Float?,
    val sensorHeightMm: Float?,
    val hasFlash: Boolean,
    /** LIMITED / FULL / LEVEL_3 / EXTERNAL, null when unread. */
    val hardwareLevel: String?,
    /** Manual-control envelope; unknown (all-auto) when unread. */
    val capabilities: CameraCapabilities = CameraCapabilities(),
)

/** Stored-ID resolution against the live inventory (pure). */
data class CameraResolution(
    val camera: UsableCamera,
    /** True only when a stored ID was requested but unavailable. */
    val fellBack: Boolean,
    /** Human-readable substitution note, null when exact/default. */
    val note: String?,
)

/**
 * Resolves the effective camera: exact stored-ID match wins; else
 * the lowest-ID back camera (the device main lens by platform
 * convention); else the lowest-ID camera. Null when none is
 * usable. A requested-but-missing ID always yields a note — no
 * silent substitution.
 */
fun resolveCamera(storedId: String?, available: List<UsableCamera>): CameraResolution? {
    if (available.isEmpty()) return null
    val sorted = available.sortedBy { it.id }
    val exact = storedId?.let { id -> sorted.firstOrNull { it.id == id } }
    if (exact != null) return CameraResolution(exact, fellBack = false, note = null)
    val back = sorted.firstOrNull { it.lensFacing == CameraSelector.LENS_FACING_BACK }
    val effective = back ?: sorted.first()
    if (storedId == null) return CameraResolution(effective, fellBack = false, note = null)
    return CameraResolution(
        effective, fellBack = true,
        note = "Camera $storedId unavailable, using ${effective.id}",
    )
}

/**
 * Operator label from measured characteristics only: facing word +
 * camera ID + focal length when known. Roles (main/ultra-wide) are
 * deliberately not guessed.
 */
fun cameraLabel(id: String, lensFacing: Int, focalMm: Float?): String {
    val facing = when (lensFacing) {
        CameraSelector.LENS_FACING_BACK -> "Back"
        CameraSelector.LENS_FACING_FRONT -> "Front"
        // Literal 2: LENS_FACING_EXTERNAL is error-level opt-in; see Wave-2 lens note.
        2 -> "External"
        else -> "Camera"
    }
    val focal = if (focalMm != null && focalMm.isFinite() && focalMm > 0f) {
        " · %.1f mm".format(java.util.Locale.US, focalMm)
    } else {
        ""
    }
    return "$facing $id$focal"
}

/** Inventory seam: production reads CameraX/Camera2, tests fake it. */
interface CameraInventorySource {
    fun list(provider: ProcessCameraProvider): List<UsableCamera>
}

/**
 * Production inventory: every CameraX-exposed camera whose Camera2
 * characteristics read cleanly. Per-camera containment: one
 * unreadable camera never hides the rest.
 */
class CameraXInventorySource : CameraInventorySource {
    @ExperimentalCamera2Interop
    override fun list(provider: ProcessCameraProvider): List<UsableCamera> {
        val out = mutableListOf<UsableCamera>()
        val infos = try {
            provider.availableCameraInfos
        } catch (_: Exception) {
            return emptyList()
        }
        for (info in infos) {
            try {
                val camera2 = Camera2CameraInfo.from(info)
                val id = camera2.cameraId
                val facing = camera2.getCameraCharacteristic(CameraCharacteristics.LENS_FACING)
                    ?: continue
                val focal = camera2.getCameraCharacteristic(
                    CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS,
                )?.firstOrNull()
                val sensor = camera2.getCameraCharacteristic(
                    CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE,
                )
                val hasFlash = try {
                    info.hasFlashUnit()
                } catch (_: Exception) {
                    false
                }
                val caps = readCapabilities(camera2, hasFlash)
                out += UsableCamera(
                    id = id,
                    label = cameraLabel(id, facing, focal),
                    lensFacing = facing,
                    focalMm = focal?.takeIf { it.isFinite() && it > 0f },
                    sensorWidthMm = sensor?.width?.takeIf { it.isFinite() && it > 0f },
                    sensorHeightMm = sensor?.height?.takeIf { it.isFinite() && it > 0f },
                    hasFlash = hasFlash,
                    hardwareLevel = hardwareLevelNameOrNull(camera2),
                    capabilities = caps,
                )
            } catch (_: Exception) {
                continue
            }
        }
        return out.sortedBy { it.id }.distinctBy { it.id }
    }

    @ExperimentalCamera2Interop
    private fun hardwareLevelNameOrNull(camera2: Camera2CameraInfo): String? {
        return try {
            camera2.getCameraCharacteristic(
                CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL,
            )?.let { hardwareLevelName(it) }
        } catch (_: Exception) {
            null
        }
    }

    @ExperimentalCamera2Interop
    private fun readCapabilities(camera2: Camera2CameraInfo, hasFlash: Boolean): CameraCapabilities {
        return try {
            val map = try {
                camera2.getCameraCharacteristic(
                    CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP,
                )
            } catch (_: Exception) {
                null
            }
            fun sizes(format: Int): List<Pair<Int, Int>> {
                return try {
                    map?.getOutputSizes(format)?.map { it.width to it.height }
                        ?.sortedByDescending { it.first * it.second }
                        ?: emptyList()
                } catch (_: Exception) {
                    emptyList()
                }
            }
            val step = camera2.getCameraCharacteristic(
                CameraCharacteristics.CONTROL_AE_COMPENSATION_STEP,
            )
            CameraCapabilities(
                aeModes = camera2.getCameraCharacteristic(
                    CameraCharacteristics.CONTROL_AE_AVAILABLE_MODES,
                )?.toSet() ?: emptySet(),
                isoRange = camera2.getCameraCharacteristic(
                    CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE,
                )?.let { it.lower..it.upper },
                exposureNsRange = camera2.getCameraCharacteristic(
                    CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE,
                )?.let { it.lower..it.upper },
                maxFrameDurationNs = camera2.getCameraCharacteristic(
                    CameraCharacteristics.SENSOR_INFO_MAX_FRAME_DURATION,
                ),
                evStepsRange = camera2.getCameraCharacteristic(
                    CameraCharacteristics.CONTROL_AE_COMPENSATION_RANGE,
                )?.let { it.lower..it.upper },
                evStepSize = step?.let {
                    if (it.denominator != 0) it.numerator.toFloat() / it.denominator.toFloat() else null
                },
                afModes = camera2.getCameraCharacteristic(
                    CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES,
                )?.toSet() ?: emptySet(),
                minFocusDistance = camera2.getCameraCharacteristic(
                    CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE,
                ),
                awbModes = camera2.getCameraCharacteristic(
                    CameraCharacteristics.CONTROL_AWB_AVAILABLE_MODES,
                )?.toSet() ?: emptySet(),
                maxDigitalZoom = camera2.getCameraCharacteristic(
                    CameraCharacteristics.SCALER_AVAILABLE_MAX_DIGITAL_ZOOM,
                ),
                yuvSizes = sizes(ImageFormat.YUV_420_888),
                jpegSizes = sizes(ImageFormat.JPEG),
                flashAvailable = hasFlash,
                manualSensor = camera2.getCameraCharacteristic(
                    CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES,
                )?.contains(
                    CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR,
                ) == true,
            )
        } catch (_: Exception) {
            CameraCapabilities()
        }
    }

    private fun hardwareLevelName(level: Int): String = when (level) {
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LIMITED -> "LIMITED"
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_FULL -> "FULL"
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_3 -> "LEVEL_3"
        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_EXTERNAL -> "EXTERNAL"
        else -> "LEVEL_$level"
    }
}

/** Log tag for camera bind/selection diagnostics (both sources). */
internal const val CAMERA_LOG_TAG = "AnastylosCam"
