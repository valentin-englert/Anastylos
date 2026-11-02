package org.anastylos.capture.camera

import android.content.Context
import org.anastylos.capture.guidance.CalibrationProfile
import org.anastylos.capture.guidance.PoseEstimator

/**
 * Measured-profile lookup for the bound analysis stream. Returns the
 * calibrated intrinsics only when the stored profile exactly matches
 * the live geometry and optics; any mismatch, missing asset, or parse
 * failure yields null so the caller stays on the fail-closed factory
 * path. Implementations must never throw.
 */
interface MeasuredProfileSource {
    fun forGeometry(
        cameraId: String?,
        analysisWidth: Int,
        analysisHeight: Int,
        linearZoom: Float,
        focusMode: FocusMode,
        focusDiopters: Float,
    ): PoseEstimator.Intrinsics?

    /**
     * Why no measured profile matches, for the operator: null when
     * one matches or when no validated profile exists for [cameraId]
     * at all (nothing to reuse, so nothing to explain).
     */
    fun mismatch(
        cameraId: String?,
        analysisWidth: Int,
        analysisHeight: Int,
        linearZoom: Float,
        focusMode: FocusMode,
        focusDiopters: Float,
    ): String? = null

    /** Recorded optics of the validated profile for [cameraId], if any. */
    fun calibratedOptics(cameraId: String?): CalibratedOptics? = null

    /** What a sidecar cites for the measured profile [profileId]; null when none is bundled. */
    fun reference(profileId: String): ProfileReference? = null
}

/**
 * A measured profile as evidence cites it: the sha256 of the exact bundled
 * bytes (the workstation stages the same bytes under that hash) and the
 * sensor mode and pixel frame the validator binds against.
 */
data class ProfileReference(
    val profileId: String,
    val sha256: String,
    val sensorMode: String,
    val pixelFrameId: String,
)

/** The fixed optics a measured profile was recorded with. */
data class CalibratedOptics(
    val focusDiopters: Float,
    val referenceWidth: Int,
    val referenceHeight: Int,
)

/**
 * Reads measured profiles from APK assets; null-safe. Only assets
 * bound to this phone's model are candidates (see [assetNames]), and
 * every candidate still passes [CalibrationProfile.isCompatible], so
 * a wrong-device or wrong-geometry asset can never validate a pose.
 */
class AssetMeasuredProfileSource(
    context: Context,
    private val deviceModel: String = android.os.Build.MODEL,
) : MeasuredProfileSource {
    private val appContext = context.applicationContext

    private val cache = mutableMapOf<String, CalibrationProfile.Document?>()
    private val shas = mutableMapOf<String, String>()

    /**
     * Bundled profiles are bound to the physical phone they were
     * measured on. The `anastylos-camera-calibration@1` schema has no
     * device field and every phone has a camera "0", so without this
     * binding another phone (GUIDE-01: a Redmi M2101K6G) would load
     * the SM-A536B intrinsics as measured. The device is named by the
     * asset file (`measured-profile-<Build.MODEL>.json`); the legacy
     * `cal01-profile.json` belongs to [CAL01_DEVICE_MODEL] only.
     * Unknown device = no profile.
     */
    private val assetNames: List<String> = buildList {
        add("measured-profile-$deviceModel.json")
        if (deviceModel == CAL01_DEVICE_MODEL) add("cal01-profile.json")
    }

    private fun documentFor(name: String): CalibrationProfile.Document? = synchronized(cache) {
        cache.getOrPut(name) {
            try {
                val bytes = appContext.assets.open(name).use { it.readBytes() }
                CalibrationProfile.parse(bytes.toString(Charsets.UTF_8))?.also {
                    shas[name] = java.security.MessageDigest.getInstance("SHA-256").digest(bytes)
                        .joinToString("") { b -> "%02x".format(b) }
                }
            } catch (_: Exception) {
                null
            }
        }
    }

    override fun forGeometry(
        cameraId: String?,
        analysisWidth: Int,
        analysisHeight: Int,
        linearZoom: Float,
        focusMode: FocusMode,
        focusDiopters: Float,
    ): PoseEstimator.Intrinsics? {
        return try {
            for (name in assetNames) {
                val doc = documentFor(name) ?: continue
                if (!CalibrationProfile.isCompatible(
                        doc, cameraId, analysisWidth, analysisHeight,
                        linearZoom, focusMode, focusDiopters,
                    )
                ) {
                    continue
                }
                return CalibrationProfile.toIntrinsics(doc, analysisWidth, analysisHeight)
            }
            null
        } catch (_: Exception) {
            null
        }
    }

    override fun mismatch(
        cameraId: String?,
        analysisWidth: Int,
        analysisHeight: Int,
        linearZoom: Float,
        focusMode: FocusMode,
        focusDiopters: Float,
    ): String? {
        if (forGeometry(cameraId, analysisWidth, analysisHeight, linearZoom, focusMode, focusDiopters) != null) {
            return null
        }
        return try {
            val doc = validatedFor(cameraId) ?: return null
            CalibrationProfile.mismatch(
                doc, cameraId, analysisWidth, analysisHeight, linearZoom, focusMode, focusDiopters,
            )
        } catch (_: Exception) {
            null
        }
    }

    override fun calibratedOptics(cameraId: String?): CalibratedOptics? = try {
        validatedFor(cameraId)?.let { doc ->
            doc.focusDiopters?.let { focus ->
                CalibratedOptics(focus.toFloat(), doc.referenceSizePx[0], doc.referenceSizePx[1])
            }
        }
    } catch (_: Exception) {
        null
    }

    override fun reference(profileId: String): ProfileReference? = try {
        assetNames.firstNotNullOfOrNull { name ->
            val doc = documentFor(name)?.takeIf {
                it.profileId == profileId && it.metricValidity == CalibrationProfile.VALIDITY_VALIDATED
            } ?: return@firstNotNullOfOrNull null
            val sha = synchronized(cache) { shas[name] } ?: return@firstNotNullOfOrNull null
            ProfileReference(doc.profileId, sha, doc.sensorMode, doc.referencePixelFrameId)
                .takeIf { it.sensorMode.isNotBlank() && it.pixelFrameId.isNotBlank() }
        }
    } catch (_: Exception) {
        null
    }

    private fun validatedFor(cameraId: String?): CalibrationProfile.Document? =
        assetNames.firstNotNullOfOrNull { name ->
            documentFor(name)?.takeIf {
                it.cameraId == cameraId && it.metricValidity == CalibrationProfile.VALIDITY_VALIDATED
            }
        }
}

/** Device the legacy CAL-01 asset was measured on (docs/integration/CAL-01.md). */
internal const val CAL01_DEVICE_MODEL = "SM-A536B"
