package org.anastylos.capture.ui.guided

import androidx.compose.ui.geometry.Offset
import org.anastylos.capture.camera.GuidedCamera

/**
 * Analytic analysis-pixel → preview-view mapping for the
 * detection overlay. The guided camera binds preview and
 * analysis at the same resolution with the PreviewView in
 * FIT_CENTER, so the mapping is an exact derotation plus a
 * uniform fit — no restricted CameraX transform APIs.
 */
object OverlayMapping {
    /** Upright frame dimensions after derotating the analysis buffer. */
    fun uprightFrame(geometry: GuidedCamera.StreamGeometry): Pair<Int, Int> =
        when (normalizedRotation(geometry.analysisRotationDeg)) {
            90, 270 -> Pair(geometry.analysisHeight, geometry.analysisWidth)
            else -> Pair(geometry.analysisWidth, geometry.analysisHeight)
        }

    /**
     * True when the upright analysis frame and the preview
     * buffer agree in aspect (within 2%). On mismatch the
     * overlay must hide: drawing would misplace the quads.
     */
    fun aspectsMatch(geometry: GuidedCamera.StreamGeometry): Boolean {
        if (geometry.analysisWidth <= 0 || geometry.analysisHeight <= 0) return false
        if (geometry.previewWidth <= 0 || geometry.previewHeight <= 0) return false
        val (frameWidth, frameHeight) = uprightFrame(geometry)
        val analysisAspect = frameWidth.toDouble() / frameHeight
        val previewAspect = geometry.previewWidth.toDouble() / geometry.previewHeight
        return kotlin.math.abs(analysisAspect - previewAspect) / previewAspect <= 0.02
    }

    /**
     * Maps one raw analysis-buffer pixel onto FIT_CENTER view
     * coordinates. `rotationDegrees` is the CameraX rotation
     * (clockwise to upright) of the analyzed frame.
     */
    fun mapToView(
        x: Float,
        y: Float,
        geometry: GuidedCamera.StreamGeometry,
        viewWidth: Float,
        viewHeight: Float,
    ): Offset {
        val (uprightX, uprightY) = when (normalizedRotation(geometry.analysisRotationDeg)) {
            90 -> Pair(geometry.analysisHeight - y, x)
            180 -> Pair(geometry.analysisWidth - x, geometry.analysisHeight - y)
            270 -> Pair(y, geometry.analysisWidth - x)
            else -> Pair(x, y)
        }
        val (frameWidth, frameHeight) = uprightFrame(geometry)
        val scale = minOf(viewWidth / frameWidth, viewHeight / frameHeight)
        val offsetX = (viewWidth - frameWidth * scale) / 2f
        val offsetY = (viewHeight - frameHeight * scale) / 2f
        return Offset(offsetX + uprightX * scale, offsetY + uprightY * scale)
    }

    private fun normalizedRotation(degrees: Int): Int = ((degrees % 360) + 360) % 360
}
