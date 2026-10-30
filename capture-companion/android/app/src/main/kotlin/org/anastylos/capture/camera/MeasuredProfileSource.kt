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

/** Reads `cal01-profile.json` once from APK assets; null-safe. */
class AssetMeasuredProfileSource(context: Context) : MeasuredProfileSource {
    private val appContext = context.applicationContext

    private val document: CalibrationProfile.Document? by lazy {
        try {
            appContext.assets.open("cal01-profile.json").use { stream ->
                CalibrationProfile.parse(stream.bufferedReader().readText())
            }
        } catch (e: Exception) {
            android.util.Log.w("AnastylosCam", "measured profile asset unreadable", e)
            null
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
            val doc = document ?: return null
            if (!CalibrationProfile.isCompatible(
                    doc, cameraId, analysisWidth, analysisHeight,
                    linearZoom, focusMode, focusDiopters,
                )
            ) {
                return null
            }
            CalibrationProfile.toIntrinsics(doc)
        } catch (_: Exception) {
            null
        }
    }
}
