package org.anastylos.capture.camera

import android.content.Context
import android.util.Size
import android.util.Log
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.anastylos.capture.domain.nowUtc

/** Default [CameraProviderSource]: the real CameraX provider future. */
class DefaultCameraProviderSource(
    private val context: Context,
) : CameraProviderSource {
    override fun providerFuture() = ProcessCameraProvider.getInstance(context)
}

/**
 * CameraX still source for the frame sampler. Device-only: binds
 * Preview + ImageCapture to the caller's lifecycle and hands full
 * JPEG stills back as bytes.
 *
 * Binding is main-safe: [bindAsync] suspends on provider completion
 * and never blocks a thread, then performs the CameraX-required bind
 * on the main thread. Cancelling the caller cancels a pending
 * provider wait; a stalled provider surfaces as bind failure, never
 * an ANR.
 *
 * Capability honesty: every control the UI exposes ([hasBackCamera],
 * [hasFrontCamera], [hasFlashUnit]) is probed on the real device at
 * bind time. Nothing is assumed.
 */
class CameraStillSource(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val previewView: PreviewView? = null,
    private val providerSource: CameraProviderSource = DefaultCameraProviderSource(context),
    private val inventory: CameraInventorySource = CameraXInventorySource(),
    private val settingsApplier: SettingsApplier = CameraXSettingsApplier(),
) : StillCamera {
    private var provider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var imageCapture: ImageCapture? = null
    private var boundPreview: Preview? = null
    private var boundSelector: CameraSelector? = null
    private var lastStillSettings: CameraSettings = CameraSettings()
    private var lastStillCaps: CameraCapabilities = CameraCapabilities()
    private var lastStillOptions: StillOptions? = null

    /** Operator-requested Camera2 ID (null = safe default). Set before [bindAsync]. */
    override var requestedCameraId: String? = null

    var lensFacing: Int = CameraSelector.LENS_FACING_BACK
        private set

    /** Effective Camera2 ID of the bound camera, null before a bind. */
    override var effectiveCameraId: String? = null
        private set

    /** Substitution note when the request fell back, else null. */
    override var cameraNote: String? = null
        private set

    private var lastInventory: List<UsableCamera> = emptyList()

    /** Live inventory from the last bind (empty before first bind). */
    override fun availableCameras(): List<UsableCamera> = lastInventory

    override var requestedStillSize: Pair<Int, Int>? = null

    override fun applySettings(settings: CameraSettings, caps: CameraCapabilities): Boolean {
        lastStillSettings = settings
        lastStillCaps = caps
        val cam = camera ?: return false
        return settingsApplier.apply(cam, settings, caps)
    }

    override var flashEnabled: Boolean = false

    override val isBound: Boolean get() = camera != null

    override fun hasBackCamera(): Boolean =
        provider?.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA) ?: false

    override fun hasFrontCamera(): Boolean =
        provider?.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA) ?: false

    override fun hasFlashUnit(): Boolean = try {
        camera?.cameraInfo?.hasFlashUnit() == true
    } catch (_: Exception) {
        false
    }

    /**
     * Binds the requested camera ([requestedCameraId]) without
     * blocking any thread. A missing request resolves to the safe
     * default with a note ([cameraNote]); returns false when no
     * camera is usable, the bind fails, or the provider fails.
     * Cancellation propagates to a pending provider wait; the
     * CameraX bind itself runs on the main thread.
     */
    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    override suspend fun bindAsync(): Boolean {
        val cameraProvider = acquireProviderOrNull(providerSource) ?: return false
        return withContext(Dispatchers.Main.immediate) { bindProvider(cameraProvider) }
    }

    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    private fun bindProvider(cameraProvider: ProcessCameraProvider): Boolean {
        provider = cameraProvider
        val cameras = try {
            inventory.list(cameraProvider)
        } catch (_: Exception) {
            emptyList()
        }
        lastInventory = cameras
        val resolution = resolveCamera(requestedCameraId, cameras)
        if (resolution == null) {
            effectiveCameraId = null
            cameraNote = "no usable camera found"
            return false
        }
        val effective = resolution.camera
        lensFacing = effective.lensFacing
        effectiveCameraId = effective.id
        cameraNote = resolution.note
        Log.i(
            CAMERA_LOG_TAG,
            "still bind requested=${requestedCameraId ?: "<default>"} " +
                "effective=${effective.id} (${effective.label}) " +
                "available=[${cameras.joinToString { it.id }}]" +
                (resolution.note?.let { " note=$it" } ?: ""),
        )
        val selector = CameraSelector.Builder()
            .addCameraFilter { infos ->
                infos.filter {
                    try {
                        Camera2CameraInfo.from(it).cameraId == effective.id
                    } catch (_: Exception) {
                        false
                    }
                }
            }
            .build()
        boundSelector = selector
        val preview = Preview.Builder().build()
        previewView?.let { preview.setSurfaceProvider(it.surfaceProvider) }
        val capture = buildCapture(
            stillOptionsFor(lastStillSettings, lastStillCaps),
        )
        previewView?.display?.rotation?.let { capture.targetRotation = it }
        cameraProvider.unbindAll()
        try {
            camera = cameraProvider.bindToLifecycle(lifecycleOwner, selector, preview, capture)
        } catch (e: Exception) {
            cameraNote = "bind failed for camera ${effective.id}"
            Log.w(CAMERA_LOG_TAG, "still bind failed for camera ${effective.id}", e)
            return false
        }
        imageCapture = capture
        boundPreview = preview
        val resolved = try {
            capture.resolutionInfo?.resolution?.let { r -> "${r.width}x${r.height}" }
        } catch (_: Exception) {
            null
        }
        Log.i(
            CAMERA_LOG_TAG,
            "still stream requested=" +
                (requestedStillSize?.let { s -> "${s.first}x${s.second}" } ?: "max") +
                " resolved=$resolved",
        )
        return true
    }

    /**
     * Releases only this source's use cases. A dispose-time
     * `unbindAll` would detach the next screen's camera: on forward
     * navigation the entering screen binds before the leaving
     * screen disposes (black preview, no error).
     */
    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    private fun buildCapture(opts: StillOptions): ImageCapture {
        val captureBuilder = ImageCapture.Builder()
            .setCaptureMode(opts.captureMode)
            .setJpegQuality(95)
            .setFlashMode(
                if (flashEnabled) ImageCapture.FLASH_MODE_ON else ImageCapture.FLASH_MODE_OFF,
            )
        applyStillOptions(captureBuilder, opts)
        requestedStillSize?.let { (w, h) ->
            captureBuilder.setResolutionSelector(
                ResolutionSelector.Builder()
                    .setAspectRatioStrategy(aspectStrategyFor(w, h))
                    .setResolutionStrategy(
                        ResolutionStrategy(
                            Size(w, h),
                            ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER,
                        ),
                    )
                    .build(),
            )
        }
        return captureBuilder.build()
    }

    /**
     * Bakes the latest settings into ImageCapture and
     * selectively rebinds that use case alone; Preview stays
     * attached. Skips the rebind when the still slice is
     * unchanged. On failure the previous ImageCapture is
     * restored and false is returned.
     */
    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    override suspend fun refreshStillOptions(): Boolean {
        val opts = stillOptionsFor(lastStillSettings, lastStillCaps)
        if (opts == lastStillOptions && imageCapture != null) return true
        return withContext(Dispatchers.Main.immediate) { rebindCapture(opts) }
    }

    @androidx.camera.camera2.interop.ExperimentalCamera2Interop
    private fun rebindCapture(opts: StillOptions): Boolean {
        val cameraProvider = provider ?: return false
        val old = imageCapture ?: return false
        val selector = boundSelector ?: return false
        return try {
            val capture = buildCapture(opts)
            capture.targetRotation = old.targetRotation
            cameraProvider.unbind(old)
            camera = cameraProvider.bindToLifecycle(lifecycleOwner, selector, capture)
            imageCapture = capture
            lastStillOptions = opts
            val cam = camera
            if (cam != null) {
                settingsApplier.apply(cam, lastStillSettings, lastStillCaps)
            }
            Log.i(
                CAMERA_LOG_TAG,
                "still opts refreshed mode=${opts.captureMode} ae=${opts.aeMode} " +
                    "iso=${opts.iso} exp=${opts.exposureNs} af=${opts.afMode} " +
                    "awb=${opts.awbMode} ev=${opts.evSteps}",
            )
            true
        } catch (e: Exception) {
            Log.w(CAMERA_LOG_TAG, "still options rebind failed, restoring previous capture", e)
            try {
                camera = cameraProvider.bindToLifecycle(lifecycleOwner, selector, old)
                imageCapture = old
            } catch (_: Exception) {
                camera = null
                imageCapture = null
            }
            lastStillOptions = null
            false
        }
    }

    override fun unbind() {
        try {
            val owned = listOfNotNull(boundPreview, imageCapture)
            if (owned.isNotEmpty()) {
                provider?.unbind(*owned.toTypedArray())
            }
        } catch (_: Exception) {
        }
        camera = null
        imageCapture = null
        boundPreview = null
    }

    override fun setFlash(enabled: Boolean) {
        flashEnabled = enabled
        imageCapture?.flashMode =
            if (enabled) ImageCapture.FLASH_MODE_ON else ImageCapture.FLASH_MODE_OFF
    }

    /** One full-resolution JPEG still. Throws on any camera failure. */
    override suspend fun takeStill(): CapturedStill {
        val capture = imageCapture ?: throw ImageCaptureException(
            ImageCapture.ERROR_UNKNOWN,
            "camera is not bound",
            null,
        )
        val tmp = File.createTempFile("frame-", ".jpg", context.cacheDir)
        try {
            suspendCoroutine { cont ->
                val options = ImageCapture.OutputFileOptions.Builder(tmp).build()
                capture.takePicture(
                    options,
                    ContextCompat.getMainExecutor(context),
                    object : ImageCapture.OnImageSavedCallback {
                        override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                            cont.resumeWith(Result.success(Unit))
                        }

                        override fun onError(exception: ImageCaptureException) {
                            cont.resumeWith(Result.failure(exception))
                        }
                    },
                )
            }
            return CapturedStill(bytes = tmp.readBytes(), capturedAt = nowUtc())
        } finally {
            tmp.delete()
        }
    }

    companion object {
        /** False on misconfigured devices where CameraX cannot initialize. */
        suspend fun isAvailable(source: CameraProviderSource): Boolean =
            acquireProviderOrNull(source) != null
    }
}
