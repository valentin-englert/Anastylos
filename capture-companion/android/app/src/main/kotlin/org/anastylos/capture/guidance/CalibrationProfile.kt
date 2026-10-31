package org.anastylos.capture.guidance

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import org.anastylos.capture.camera.FocusMode

/**
 * Measured calibration profile (`anastylos-camera-calibration@1`,
 * asset `cal01-profile.json`): the only intrinsics source that may
 * feed a metric pose. Parsing and compatibility are pure and
 * fail-closed: any unknown schema, unvalidated profile, geometry or
 * optics mismatch yields null, and the caller falls back to the
 * factory detection-only path (which itself never validates — see
 * [OpenCvPoseEstimator]).
 *
 * The profile is bound to its recording geometry AND optics: camera
 * ID, pixel grid, zoom 1x, and manual focus in diopters. A change
 * to any of them invalidates the profile instead of silently reusing
 * it (CAL-01). The one admitted pixel-grid change is a uniform
 * downscale of the same aspect ratio (GUIDE-01): Camera2 crops every
 * output stream from the same crop region centred to its aspect
 * ratio and then scales it, so a 1280x720 stream images exactly the
 * field of the calibrated 1920x1080 grid. The intrinsics are scaled
 * exactly and the profile ID names the derivation.
 */
object CalibrationProfile {
    const val SCHEMA = "anastylos-camera-calibration@1"
    const val MODEL_BROWN_CONRADY_5 = "brown_conrady_5"
    const val VALIDITY_VALIDATED = "validated"

    /**
     * Diopters tolerance for the manual-focus compatibility check.
     * Covers tap quantization on the 0..10 diopter slider: the CAL-01
     * setup tap landed at 2.4668 D for a nominal 2.5 D (40 cm label),
     * a 0.033 gap with negligible optical effect (0.5 cm focus shift).
     * Anything coarser (auto focus, 2.0 D) still fails closed.
     */
    const val FOCUS_TOLERANCE_D = 0.05

    private val json = Json { ignoreUnknownKeys = true }

    @Serializable
    data class Document(
        @SerialName("schema") val schema: String = "",
        @SerialName("profile_id") val profileId: String = "",
        @SerialName("camera_id") val cameraId: String = "",
        @SerialName("camera_model") val cameraModel: String = "",
        @SerialName("metric_validity") val metricValidity: String = "",
        @SerialName("reference_image_size_px") val referenceSizePx: List<Int> = emptyList(),
        @SerialName("K") val k: List<Double> = emptyList(),
        @SerialName("distortion") val distortion: List<Double> = emptyList(),
        @SerialName("zoom") val zoom: Double = 1.0,
        @SerialName("focus_distance_diopters") val focusDiopters: Double? = null,
    )

    /** Null unless the document is a structurally valid measured profile. */
    fun parse(text: String): Document? {
        val doc = try {
            json.decodeFromString<Document>(text)
        } catch (_: Exception) {
            return null
        }
        if (doc.schema != SCHEMA) return null
        if (doc.cameraModel != MODEL_BROWN_CONRADY_5) return null
        if (doc.profileId.isBlank()) return null
        if (doc.referenceSizePx.size != 2) return null
        if (doc.referenceSizePx.any { it <= 0 }) return null
        if (doc.k.size != 9) return null
        if (doc.k.any { !it.isFinite() }) return null
        if (doc.k[0] <= 0.0 || doc.k[4] <= 0.0) return null
        if (doc.distortion.size != 5) return null
        if (doc.distortion.any { !it.isFinite() }) return null
        return doc
    }

    /**
     * True only for a validated profile whose recording geometry and
     * optics match the live stream: same camera, same analysis size or
     * an exact same-aspect downscale of it ([gridScale]), zoom 1x
     * ([linearZoom] 0), and the same manual focus in diopters.
     * Anything else — including a second calibrated camera — is a
     * different profile, never a silent reuse.
     */
    fun isCompatible(
        doc: Document,
        cameraId: String?,
        analysisWidth: Int,
        analysisHeight: Int,
        linearZoom: Float,
        focusMode: FocusMode,
        focusDiopters: Float,
    ): Boolean = mismatch(
        doc, cameraId, analysisWidth, analysisHeight, linearZoom, focusMode, focusDiopters,
    ) == null

    /**
     * The first binding the live configuration violates, as an
     * operator-readable reason, or null when [isCompatible]. Same
     * checks in the same order — the single source of the rule.
     */
    fun mismatch(
        doc: Document,
        cameraId: String?,
        analysisWidth: Int,
        analysisHeight: Int,
        linearZoom: Float,
        focusMode: FocusMode,
        focusDiopters: Float,
    ): String? {
        if (doc.metricValidity != VALIDITY_VALIDATED) return "profile not validated"
        if (cameraId == null || doc.cameraId != cameraId) {
            return "profile is for camera ${doc.cameraId}, live camera is ${cameraId ?: "none"}"
        }
        if (gridScale(doc, analysisWidth, analysisHeight) == null) {
            return "analysis ${analysisWidth}x$analysisHeight is not the calibrated " +
                "${doc.referenceSizePx[0]}x${doc.referenceSizePx[1]} grid or a same-aspect downscale"
        }
        if (doc.zoom != 1.0 || linearZoom != 0f) return "zoom must be 1x"
        val wantFocus = doc.focusDiopters ?: return "profile has no fixed focus"
        if (focusMode != FocusMode.MANUAL) {
            return "focus is $focusMode; profile needs manual ${"%.2f".format(java.util.Locale.US, wantFocus)} D"
        }
        if (kotlin.math.abs(focusDiopters - wantFocus) > FOCUS_TOLERANCE_D) {
            return "focus ${"%.2f".format(java.util.Locale.US, focusDiopters)} D; " +
                "profile needs ${"%.2f".format(java.util.Locale.US, wantFocus)} D"
        }
        return null
    }

    /**
     * Scale from the profile's pixel grid to [width]x[height]: 1.0 for
     * the recorded size, the exact factor for a same-aspect downscale,
     * null otherwise (other aspect = other crop; upscale = precision
     * the calibration never measured).
     */
    fun gridScale(doc: Document, width: Int, height: Int): Double? =
        gridScale(doc.referenceSizePx[0], doc.referenceSizePx[1], width, height)

    /** [gridScale] against a bare reference size. */
    fun gridScale(refW: Int, refH: Int, width: Int, height: Int): Double? {
        if (width <= 0 || height <= 0 || width > refW) return null
        if (width.toLong() * refH != height.toLong() * refW) return null
        return width.toDouble() / refW
    }

    /**
     * Intrinsics in solver pixels for a compatible [width]x[height]
     * grid (default: the recorded one); the profile ID never reads
     * uncalibrated. Pixel convention is top-left pixel centre (0, 0),
     * so principal points scale about the pixel-grid corner
     * (c' = (c + 0.5) s - 0.5). Distortion acts on normalized
     * coordinates and is grid-independent.
     */
    fun toIntrinsics(
        doc: Document,
        width: Int = doc.referenceSizePx[0],
        height: Int = doc.referenceSizePx[1],
    ): PoseEstimator.Intrinsics {
        val s = requireNotNull(gridScale(doc, width, height)) { "incompatible grid ${width}x$height" }
        val derived = width != doc.referenceSizePx[0]
        return PoseEstimator.Intrinsics(
            fx = doc.k[0] * s,
            fy = doc.k[4] * s,
            cx = (doc.k[2] + 0.5) * s - 0.5,
            cy = (doc.k[5] + 0.5) * s - 0.5,
            width = width,
            height = height,
            distortion = doc.distortion,
            profileId = "measured:" + doc.profileId + if (derived) "@${width}x$height-scaled" else "",
        )
    }
}
