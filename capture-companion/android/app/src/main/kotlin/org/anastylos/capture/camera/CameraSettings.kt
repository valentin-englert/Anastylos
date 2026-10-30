package org.anastylos.capture.camera

import android.hardware.camera2.CameraMetadata
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import kotlin.math.abs
import kotlin.math.ln
import kotlin.math.pow

/**
 * Manual-control capabilities of one camera, read from Camera2
 * characteristics. Absent ranges (null) mean "unknown": the UI
 * hides the control and [sanitizeSettings] forces the auto path.
 * Sizes are plain pairs (no android stub types in unit tests).
 */
data class CameraCapabilities(
    val aeModes: Set<Int> = emptySet(),
    val isoRange: ClosedRange<Int>? = null,
    val exposureNsRange: ClosedRange<Long>? = null,
    val maxFrameDurationNs: Long? = null,
    val evStepsRange: ClosedRange<Int>? = null,
    val evStepSize: Float? = null,
    val afModes: Set<Int> = emptySet(),
    /** Diopters; null or <= 0 means fixed focus (no focus control). */
    val minFocusDistance: Float? = null,
    val awbModes: Set<Int> = emptySet(),
    val maxDigitalZoom: Float? = null,
    val yuvSizes: List<Pair<Int, Int>> = emptyList(),
    val jpegSizes: List<Pair<Int, Int>> = emptyList(),
    val flashAvailable: Boolean = false,
    /** True when REQUEST_AVAILABLE_CAPABILITIES contains MANUAL_SENSOR. */
    val manualSensor: Boolean = false,
    /** CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES as (lower, upper). */
    val aeFpsRanges: List<Pair<Int, Int>> = emptyList(),
) {
    val supportsManualExposure: Boolean
        // MANUAL_SENSOR is the true gate: AE_OFF alone only locks
        // converged AE on LIMITED HALs (SM-A536B EXIF proof).
        get() = manualSensor && CameraMetadata.CONTROL_AE_MODE_OFF in aeModes &&
            isoRange != null && exposureNsRange != null
    val supportsEv: Boolean
        get() = evStepsRange != null && evStepSize != null &&
            aeModes.any { it != CameraMetadata.CONTROL_AE_MODE_OFF }
    val supportsManualFocus: Boolean
        get() = CameraMetadata.CONTROL_AF_MODE_OFF in afModes &&
            minFocusDistance != null && minFocusDistance > 0f
    val supportsAutoFocusModes: Boolean
        get() = afModes.any { it != CameraMetadata.CONTROL_AF_MODE_OFF }
    val supportsZoom: Boolean
        get() = maxDigitalZoom != null && maxDigitalZoom > 1f
}

enum class ExposureMode { AUTO, MANUAL }

enum class FocusMode { AUTO, MANUAL }

enum class AwbMode(val label: String, val camera2: Int) {
    AUTO("Auto", CameraMetadata.CONTROL_AWB_MODE_AUTO),
    INCANDESCENT("Incandescent", CameraMetadata.CONTROL_AWB_MODE_INCANDESCENT),
    FLUORESCENT("Fluorescent", CameraMetadata.CONTROL_AWB_MODE_FLUORESCENT),
    DAYLIGHT("Daylight", CameraMetadata.CONTROL_AWB_MODE_DAYLIGHT),
    CLOUDY("Cloudy", CameraMetadata.CONTROL_AWB_MODE_CLOUDY_DAYLIGHT),
}

/** Named setting slots. Slots carry no capture semantics; each
 * (camera, profile) pair persists its own [CameraSettings]. */
val SETTING_PROFILES = listOf("Default", "Low light", "Bright")

const val DEFAULT_PROFILE = "Default"

/** Operator-controllable camera state. All values are requests;
 * [sanitizeSettings] clamps them to [CameraCapabilities]. */
data class CameraSettings(
    val exposureMode: ExposureMode = ExposureMode.AUTO,
    val iso: Int = 400,
    val exposureNs: Long = 20_000_000L,
    /** EV compensation in device steps (scale via [CameraCapabilities.evStepSize]). */
    val evSteps: Int = 0,
    val focusMode: FocusMode = FocusMode.AUTO,
    /** Diopters, 0 = infinity. */
    val focusDiopters: Float = 0f,
    val awbMode: AwbMode = AwbMode.AUTO,
    /** CameraX linear zoom 0..1. */
    val linearZoom: Float = 0f,
    val analysisSize: Pair<Int, Int> = DEFAULT_ANALYSIS_SIZE,
    /** Null = maximum JPEG (current behavior). */
    val stillSize: Pair<Int, Int>? = null,
    val torch: Boolean = false,
) {
    companion object {
        val DEFAULT_ANALYSIS_SIZE = 1280 to 720
        val FALLBACK_ANALYSIS_SIZES = listOf(1280 to 720, 960 to 720, 640 to 480)
    }
}

/**
 * Clamps every request into the supported envelope; forces the
 * auto path wherever the capability is absent. Unknown ranges
 * (null) always resolve to auto/defaults — never assumed usable.
 */
/** Aspect bucket for CameraX selectors. The CameraX default
 * aspect strategy is 4:3-fallback: without an explicit aspect,
 * 16:9 requests resolve to 4:3 sizes (SM-A536B proof:
 * 1920x1080 still resolved to 4624x3468, 1280x720 analysis to
 * 960x720). Every selector built from an explicit size must
 * carry [aspectStrategyFor].
 */
enum class AspectBucket { R16_9, R4_3 }

fun aspectBucketFor(width: Int, height: Int): AspectBucket {
    val r = width.toFloat() / height.toFloat()
    return if (abs(r - 16f / 9f) <= abs(r - 4f / 3f)) AspectBucket.R16_9 else AspectBucket.R4_3
}

fun aspectStrategyFor(width: Int, height: Int): AspectRatioStrategy =
    if (aspectBucketFor(width, height) == AspectBucket.R16_9) {
        AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY
    } else {
        AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY
    }


fun sanitizeSettings(requested: CameraSettings, caps: CameraCapabilities): CameraSettings {
    var s = requested
    if (!caps.supportsManualExposure && s.exposureMode == ExposureMode.MANUAL) {
        s = s.copy(exposureMode = ExposureMode.AUTO)
    }
    val isoRange = caps.isoRange
    val expRange = caps.exposureNsRange
    if (s.exposureMode == ExposureMode.MANUAL && isoRange != null && expRange != null) {
        val maxNs = minOf(expRange.endInclusive, caps.maxFrameDurationNs ?: expRange.endInclusive)
        s = s.copy(
            iso = s.iso.coerceIn(isoRange.start, isoRange.endInclusive),
            exposureNs = s.exposureNs.coerceIn(expRange.start, maxNs),
        )
    }
    val evRange = caps.evStepsRange
    s = if (evRange != null) {
        s.copy(evSteps = s.evSteps.coerceIn(evRange.start, evRange.endInclusive))
    } else {
        s.copy(evSteps = 0)
    }
    if (!caps.supportsManualFocus && s.focusMode == FocusMode.MANUAL) {
        s = s.copy(focusMode = FocusMode.AUTO)
    }
    val minFocus = caps.minFocusDistance
    if (s.focusMode == FocusMode.MANUAL && minFocus != null && minFocus > 0f) {
        s = s.copy(focusDiopters = s.focusDiopters.coerceIn(0f, minFocus))
    } else if (s.focusMode == FocusMode.AUTO) {
        s = s.copy(focusDiopters = 0f)
    }
    if (s.awbMode.camera2 !in caps.awbModes) s = s.copy(awbMode = AwbMode.AUTO)
    if (AwbMode.AUTO.camera2 !in caps.awbModes && caps.awbModes.isEmpty()) {
        s = s.copy(awbMode = AwbMode.AUTO)
    }
    s = s.copy(linearZoom = s.linearZoom.coerceIn(0f, 1f))
    if (!caps.supportsZoom) s = s.copy(linearZoom = 0f)
    val analysis = if (s.analysisSize in caps.yuvSizes) {
        s.analysisSize
    } else {
        CameraSettings.FALLBACK_ANALYSIS_SIZES.firstOrNull { it in caps.yuvSizes }
            ?: caps.yuvSizes.firstOrNull()
            ?: CameraSettings.DEFAULT_ANALYSIS_SIZE
    }
    s = s.copy(analysisSize = analysis)
    if (s.stillSize != null && s.stillSize !in caps.jpegSizes) s = s.copy(stillSize = null)
    if (!caps.flashAvailable) s = s.copy(torch = false)
    return s
}

/** Compact versioned encoding for DataStore (`v1;…`). Strict: any
 * malformed value decodes to null (caller falls back to defaults). */
fun serializeSettings(s: CameraSettings): String = listOf(
    "v1",
    s.exposureMode.name,
    s.iso.toString(),
    s.exposureNs.toString(),
    s.evSteps.toString(),
    s.focusMode.name,
    s.focusDiopters.toString(),
    s.awbMode.name,
    s.linearZoom.toString(),
    "${s.analysisSize.first}x${s.analysisSize.second}",
    s.stillSize?.let { "${it.first}x${it.second}" } ?: "MAX",
    if (s.torch) "1" else "0",
).joinToString(";")

fun parseSettings(raw: String?): CameraSettings? {
    if (raw == null) return null
    val p = raw.split(";")
    if (p.size != 12 || p[0] != "v1") return null
    try {
        val analysis = p[9].split("x").let { it[0].toInt() to it[1].toInt() }
        val still = if (p[10] == "MAX") null else p[10].split("x").let { it[0].toInt() to it[1].toInt() }
        if (analysis.first <= 0 || analysis.second <= 0) return null
        if (still != null && (still.first <= 0 || still.second <= 0)) return null
        return CameraSettings(
            exposureMode = ExposureMode.valueOf(p[1]),
            iso = p[2].toInt(),
            exposureNs = p[3].toLong(),
            evSteps = p[4].toInt(),
            focusMode = FocusMode.valueOf(p[5]),
            focusDiopters = p[6].toFloat(),
            awbMode = AwbMode.valueOf(p[7]),
            linearZoom = p[8].toFloat(),
            analysisSize = analysis,
            stillSize = still,
            torch = p[11] == "1",
        )
    } catch (_: Exception) {
        return null
    }
}

/** Human-readable exposure time: 1/50 s, 20 ms, … */
fun formatExposure(ns: Long): String {
    if (ns <= 0) return "—"
    val seconds = ns / 1_000_000_000.0
    return if (seconds >= 1.0) "%.1f s".format(java.util.Locale.US, seconds)
    else if (seconds >= 0.1) "%d ms".format((ns / 1_000_000).toInt())
    else "1/%d".format((1.0 / seconds).toInt())
}

/** Human-readable focus distance from diopters: ∞, 50 cm, … */
fun formatFocus(diopters: Float): String {
    if (!diopters.isFinite() || diopters < 0f) return "—"
    if (diopters == 0f) return "∞"
    val cm = 100f / diopters
    return if (cm >= 100f) "%.1f m".format(java.util.Locale.US, cm / 100f)
    else "%d cm".format(cm.toInt())
}

/** Log slider position 0..1 → exposure ns across [min, max]. */
fun exposureSliderToNs(t: Float, minNs: Long, maxNs: Long): Long {
    val clamped = t.coerceIn(0f, 1f)
    if (minNs <= 0 || maxNs <= minNs) return minNs.coerceAtLeast(1L)
    val ratio = maxNs.toDouble() / minNs.toDouble()
    return (minNs * ratio.pow(clamped.toDouble())).toLong().coerceIn(minNs, maxNs)
}

/** Exposure ns → log slider position 0..1. */
fun exposureNsToSlider(ns: Long, minNs: Long, maxNs: Long): Float {
    if (minNs <= 0 || maxNs <= minNs) return 0f
    val ratio = maxNs.toDouble() / minNs.toDouble()
    return (ln(ns.coerceIn(minNs, maxNs).toDouble() / minNs) / ln(ratio)).toFloat()
}
