package org.anastylos.capture.camera

import androidx.camera.view.PreviewView
import kotlinx.coroutines.flow.SharedFlow
import org.anastylos.capture.guidance.PoseEstimator

/**
 * Guided-capture camera contract. [GuidedCameraSource] is the
 * production implementation; guidance logic unit-tests against
 * fakes of this interface with scripted estimate streams.
 */
interface GuidedCamera {
    /**
     * Bound stream geometry for the detection overlay: analysis
     * buffer size plus the rotation CameraX applied to analyzed
     * frames, and the preview buffer size the overlay must agree
     * with. All public-API values; null before a bound camera.
     */
    data class StreamGeometry(
        val analysisWidth: Int,
        val analysisHeight: Int,
        val analysisRotationDeg: Int,
        val previewWidth: Int,
        val previewHeight: Int,
        /** CameraSelector lens facing (-1 when unknown). */
        val lensFacing: Int = -1,
        /** Effective Camera2 camera ID ("" when unknown). */
        val cameraId: String = "",
    )

    /** Latest-only estimate stream. */
    val estimates: SharedFlow<PoseEstimator.Estimate>

    /** Intrinsics resolved for the bound stream, or null (detection-only). */
    fun currentIntrinsics(): PoseEstimator.Intrinsics?

    /** Stream geometry for overlay mapping, or null before bind. */
    fun streamGeometry(): StreamGeometry?

    fun attachPreview(view: PreviewView?)

    fun setBoard(board: PoseEstimator.BoardSpec?)

    /** False (no throw) when the provider, camera, or bind fails. */
    suspend fun bindAsync(analysisWidth: Int = 1280, analysisHeight: Int = 720): Boolean

    /** Operator-requested Camera2 ID (null = safe default). Set before [bindAsync]. */
    var requestedCameraId: String?
    /** Effective Camera2 ID of the bound camera, null before a bind. */
    val effectiveCameraId: String?
    /** Substitution note when the request fell back, else null. */
    val cameraNote: String?
    /** Live inventory from the last bind (empty before first bind). */
    fun availableCameras(): List<UsableCamera>

    /** Applies sanitized settings to the bound camera; false (no throw) on failure. */
    fun applySettings(settings: CameraSettings, caps: CameraCapabilities): Boolean
    /**
     * Refreshes bind-time still options from the last
     * [applySettings] snapshot; selectively rebinds ImageCapture
     * only (no-op when the still slice is unchanged). Safe to
     * call on any thread; never throws.
     */
    suspend fun refreshStillOptions(): Boolean


    /** Requested JPEG size, null = max; set before [bindAsync]. */
    var requestedStillSize: Pair<Int, Int>?

    /** Last CaptureResult sample, null before the first sample. */
    val lastApplied: AppliedSettings?

    fun unbind()

    /** One full-resolution JPEG still. Throws on camera failure. */
    suspend fun takeGuidedStill(): GuidedCameraSource.GuidedStill
}
