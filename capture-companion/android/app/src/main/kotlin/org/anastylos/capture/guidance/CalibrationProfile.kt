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
 * ID, analysis size, zoom 1x, and manual focus in diopters. A change
 * to any of them invalidates the profile instead of silently reusing
 * it (CAL-01).
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
     * optics exactly match the live stream: same camera, same analysis
     * size, zoom 1x ([linearZoom] 0), and the same manual focus in
     * diopters. Anything else — including a second calibrated camera —
     * is a different profile, never a silent reuse.
     */
    fun isCompatible(
        doc: Document,
        cameraId: String?,
        analysisWidth: Int,
        analysisHeight: Int,
        linearZoom: Float,
        focusMode: FocusMode,
        focusDiopters: Float,
    ): Boolean {
        if (doc.metricValidity != VALIDITY_VALIDATED) return false
        if (cameraId == null || doc.cameraId != cameraId) return false
        if (doc.referenceSizePx[0] != analysisWidth) return false
        if (doc.referenceSizePx[1] != analysisHeight) return false
        if (doc.zoom != 1.0 || linearZoom != 0f) return false
        val wantFocus = doc.focusDiopters ?: return false
        if (focusMode != FocusMode.MANUAL) return false
        if (kotlin.math.abs(focusDiopters - wantFocus) > FOCUS_TOLERANCE_D) return false
        return true
    }

    /** Intrinsics in solver pixels; the profile ID never reads uncalibrated. */
    fun toIntrinsics(doc: Document): PoseEstimator.Intrinsics = PoseEstimator.Intrinsics(
        fx = doc.k[0],
        fy = doc.k[4],
        cx = doc.k[2],
        cy = doc.k[5],
        width = doc.referenceSizePx[0],
        height = doc.referenceSizePx[1],
        distortion = doc.distortion,
        profileId = "measured:" + doc.profileId,
    )
}
