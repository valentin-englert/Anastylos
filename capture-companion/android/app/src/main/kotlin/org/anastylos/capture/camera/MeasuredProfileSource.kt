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
}

/**
 * Reads measured profiles from APK assets; null-safe. Exact-geometry
 * asset first (`measured-profile-<WxH>.json`), then the legacy
 * `cal01-profile.json` single asset. Every candidate still passes
 * [CalibrationProfile.isCompatible], so a wrong-geometry asset can
 * never validate a metric pose.
 */
class AssetMeasuredProfileSource(context: Context) : MeasuredProfileSource {
    private val appContext = context.applicationContext

    private val cache = mutableMapOf<String, CalibrationProfile.Document?>()

    private fun documentFor(name: String): CalibrationProfile.Document? = synchronized(cache) {
        cache.getOrPut(name) {
            try {
                appContext.assets.open(name).use { stream ->
                    CalibrationProfile.parse(stream.bufferedReader().readText())
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
            val candidates = listOf(
                "measured-profile-${analysisWidth}x${analysisHeight}.json",
                "cal01-profile.json",
            )
            for (name in candidates) {
                val doc = documentFor(name) ?: continue
                if (!CalibrationProfile.isCompatible(
                        doc, cameraId, analysisWidth, analysisHeight,
                        linearZoom, focusMode, focusDiopters,
                    )
                ) {
                    continue
                }
                return CalibrationProfile.toIntrinsics(doc)
            }
            null
        } catch (_: Exception) {
            null
        }
    }
}
