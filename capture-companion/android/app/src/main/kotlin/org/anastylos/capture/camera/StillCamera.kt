package org.anastylos.capture.camera

/**
 * Live Observer still-camera contract. [CameraStillSource] is the
 * production implementation; the interface keeps experimental
 * Camera2-interop opt-ins inside the implementation, mirroring
 * [GuidedCamera].
 */
interface StillCamera {
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

    val isBound: Boolean

    var flashEnabled: Boolean

    fun hasBackCamera(): Boolean

    fun hasFrontCamera(): Boolean

    fun hasFlashUnit(): Boolean

    /** False (no throw) when the provider, camera, or bind fails. */
    suspend fun bindAsync(): Boolean

    fun unbind()

    fun setFlash(enabled: Boolean)

    /** One full-resolution JPEG still. Throws on camera failure. */
    suspend fun takeStill(): CapturedStill
}
